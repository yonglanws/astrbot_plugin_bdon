"""Downloads and caches the Our Notes masterdata for each game server."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from .constants import (
    ALL_TABLES,
    DEFAULT_DATA_SOURCES,
    REGIONS,
    REQUIRED_TABLES,
    SCRIPT_TEXT_FIELDS,
)
from .models import Dataset, DatasetBuilder

try:  # AstrBot ships aiohttp; the fallback keeps the module importable in tests.
    import aiohttp
except ImportError:  # pragma: no cover
    aiohttp = None  # type: ignore[assignment]

logger = logging.getLogger("astrbot")


class MasterDataError(RuntimeError):
    pass


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(temporary, path)


class MasterDataManager:
    def __init__(
        self,
        data_dir: Path,
        asset_base: str,
        sources: Iterable[str] | None = None,
        timeout: int = 60,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.asset_base = asset_base
        self.sources = [source.rstrip("/") for source in (sources or DEFAULT_DATA_SOURCES)]
        self.timeout = max(10, int(timeout))
        # Keyed by (server key, script): cn and tw/hk read the same files but
        # build separate text variants, so both can be cached at once.
        self._datasets: dict[tuple[str, str], Dataset] = {}
        self._dataset_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._sync_locks: dict[str, asyncio.Lock] = {
            key: asyncio.Lock() for key in REGIONS
        }
        # Coalesces the manifest fetch when several regions sync in parallel.
        self._version_lock = asyncio.Lock()
        self._version_cache: tuple[float, dict[str, Any]] | None = None

    # -- paths -----------------------------------------------------------------
    def region_dir(self, region: str) -> Path:
        profile = REGIONS[region]
        return self.data_dir / "masterdata" / profile.data_region

    def table_path(self, region: str, table: str) -> Path:
        return self.region_dir(region) / f"{table}.json"

    def meta_path(self, region: str) -> Path:
        return self.region_dir(region) / "_meta.json"

    def has_required_data(self, region: str) -> bool:
        return all(
            self.table_path(region, table).is_file()
            for table in REQUIRED_TABLES
        )

    def has_all_tables(self, region: str) -> bool:
        return all(self.table_path(region, table).is_file() for table in ALL_TABLES)

    def local_meta(self, region: str) -> dict[str, Any]:
        try:
            return _read_json(self.meta_path(region))
        except (OSError, ValueError):
            return {}

    # -- public ----------------------------------------------------------------
    def _dataset_lock(self, region: str, script: str) -> asyncio.Lock:
        key = (region, script)
        lock = self._dataset_locks.get(key)
        if lock is None:
            lock = self._dataset_locks[key] = asyncio.Lock()
        return lock

    async def get_dataset(
        self,
        region: str,
        script: str = "simplified",
        force_sync: bool = False,
    ) -> Dataset:
        if region not in REGIONS:
            raise MasterDataError(f"Unknown region: {region}")
        if script not in SCRIPT_TEXT_FIELDS:
            script = "simplified"
        key = (region, script)
        async with self._dataset_lock(region, script):
            if not force_sync and key in self._datasets:
                return self._datasets[key]
            if force_sync or not self.has_required_data(region):
                await self.sync_region(region, force=force_sync)
            async with self._sync_locks[region]:
                try:
                    dataset = await asyncio.to_thread(
                        self._load_dataset, region, script
                    )
                except MasterDataError:
                    if force_sync:
                        raise
                    logger.warning("[bdon] %s data unreadable; forcing a re-sync", region)
                    await self._sync_region_locked(region, True)
                    dataset = await asyncio.to_thread(
                        self._load_dataset, region, script
                    )
            self._datasets[key] = dataset
            return dataset

    def invalidate(self, region: str | None = None) -> None:
        if region is None:
            self._datasets.clear()
            return
        for key in [item for item in self._datasets if item[0] == region]:
            self._datasets.pop(key, None)

    async def sync_region(self, region: str, force: bool = False) -> bool:
        """Returns True when files were downloaded, False when already current."""
        if region not in REGIONS:
            raise MasterDataError(f"Unknown region: {region}")
        async with self._sync_locks[region]:
            return await self._sync_region_locked(region, force)

    async def _sync_region_locked(self, region: str, force: bool) -> bool:
        profile = REGIONS[region]
        directory = self.region_dir(region)
        directory.mkdir(parents=True, exist_ok=True)

        remote = await self._remote_version(profile.data_region)
        local_meta = self.local_meta(region)
        local_version = local_meta.get("version")
        remote_version = remote.get("version")
        has_data = self.has_required_data(region)
        # A skipped optional table (or a previous partial failure) must not be
        # frozen forever just because the version string already matches.
        complete = self.has_all_tables(region) and not local_meta.get("failures")
        if not force and has_data and complete:
            if remote_version:
                if local_version == remote_version:
                    logger.info(
                        "[bdon] %s masterdata already current (%s)",
                        profile.label,
                        remote_version,
                    )
                    return False
            elif local_version:
                # The version file is unreachable but we have a complete, tagged
                # dataset. Treating "unknown" as "changed" would re-download 16
                # tables every startup whenever a mirror hiccups, so keep what we
                # have and let the next run compare again.
                logger.info(
                    "[bdon] %s masterdata version unknown; keeping %s",
                    profile.label,
                    local_version,
                )
                return False

        started = time.time()
        failures: list[str] = []
        semaphore = asyncio.Semaphore(4)

        async def download(table: str) -> tuple[str, bool, str]:
            async with semaphore:
                try:
                    payload = await self._download_table(profile.data_region, table)
                    _write_json_atomic(self.table_path(region, table), payload)
                    return table, True, ""
                except Exception as exc:  # noqa: BLE001 - reported per table
                    return table, False, str(exc)

        results = await asyncio.gather(*(download(table) for table in ALL_TABLES))
        for table, ok, error in results:
            if not ok:
                failures.append(f"{table}: {error}")

        missing_required = [
            table for table in REQUIRED_TABLES if not self.table_path(region, table).is_file()
        ]
        if missing_required:
            raise MasterDataError(
                f"{profile.label} 数据同步失败，缺少必需表: {', '.join(missing_required)}"
                + (f"；{failures[0]}" if failures else "")
            )

        meta = {
            "region": profile.data_region,
            "version": remote_version or "",
            "client_version": remote.get("client_version", ""),
            "resource_version": remote.get("resource_version", ""),
            "synced_at": datetime.now().isoformat(timespec="seconds"),
            "source": self.sources[0] if self.sources else "",
            "tables": len(ALL_TABLES) - len(failures),
            "failures": failures,
        }
        _write_json_atomic(self.meta_path(region), meta)
        self.invalidate(region)
        logger.info(
            "[bdon] synced %s masterdata in %.1fs (%d/%d tables)",
            profile.label,
            time.time() - started,
            len(ALL_TABLES) - len(failures),
            len(ALL_TABLES),
        )
        return True

    async def sync_all(
        self,
        regions: Iterable[str],
        force: bool = False,
        parallel: bool = False,
    ) -> dict[str, tuple[bool, str]]:
        """Syncs several servers, returning one ``(ok, message)`` per region.

        ``parallel`` runs the regions concurrently, which cuts a first install
        from the sum of four downloads to the slowest one. Each region has its
        own lock, so the daily loop can keep calling this serially.
        """
        if isinstance(regions, str):
            regions = [regions]
        regions = list(regions)
        unique = [region for region in dict.fromkeys(regions) if region in REGIONS]

        async def one(region: str) -> tuple[str, tuple[bool, str]]:
            try:
                changed = await self.sync_region(region, force=force)
                return region, (True, "已更新" if changed else "已是最新")
            except Exception as exc:  # noqa: BLE001 - summarised to the user
                logger.warning("[bdon] sync %s failed: %s", region, exc)
                return region, (False, str(exc))

        if parallel:
            pairs = await asyncio.gather(*(one(region) for region in unique))
            results = dict(pairs)
        else:
            results = {}
            for region in unique:
                _, value = await one(region)
                results[region] = value

        # Preserve the caller's ordering, and report unknown regions too.
        ordered: dict[str, tuple[bool, str]] = {}
        for region in regions:
            if region in REGIONS:
                ordered[region] = results[region]
            elif region not in ordered:
                ordered[region] = (False, "未知区服")
        return ordered

    async def daily_loop(
        self,
        region_provider: Callable[[], Iterable[str]],
        hour: int,
        minute: int,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        while True:
            now = datetime.now()
            target = now.replace(
                hour=max(0, min(23, int(hour))),
                minute=max(0, min(59, int(minute))),
                second=0,
                microsecond=0,
            )
            if target <= now:
                target += timedelta(days=1)
            await sleep(max(30.0, (target - now).total_seconds()))
            regions = list(region_provider())
            logger.info("[bdon] daily masterdata sync: %s", ",".join(regions))
            try:
                await self.sync_all(regions, force=False)
            except Exception:  # noqa: BLE001 - the loop must survive
                logger.exception("[bdon] daily sync failed")

    # -- loading ---------------------------------------------------------------
    def _load_dataset(self, region: str, script: str) -> Dataset:
        profile = REGIONS[region]
        tables: dict[str, Any] = {}
        for table in ALL_TABLES:
            path = self.table_path(region, table)
            try:
                tables[table] = _read_json(path)
            except FileNotFoundError:
                if table in REQUIRED_TABLES:
                    raise MasterDataError(f"缺少数据表 {table}")
                tables[table] = {"_allData": []}
            except ValueError as exc:
                if table in REQUIRED_TABLES:
                    raise MasterDataError(f"数据表 {table} 损坏: {exc}") from exc
                tables[table] = {"_allData": []}
        builder = DatasetBuilder(profile, self.asset_base, script)
        return builder.build(tables)

    # -- downloads -------------------------------------------------------------
    async def _remote_version(self, data_region: str) -> dict[str, Any]:
        now = time.time()
        cached = self._version_cache[1] if self._version_cache else None
        if (
            self._version_cache is None
            or now - self._version_cache[0] >= 600
        ):
            async with self._version_lock:
                now = time.time()
                if (
                    self._version_cache is None
                    or now - self._version_cache[0] >= 600
                ):
                    payload: dict[str, Any] = {}
                    for source in self.sources:
                        try:
                            data = await self._fetch_bytes(
                                f"{source}/current_version.json"
                            )
                            payload = json.loads(data.decode("utf-8"))
                            break
                        except Exception:  # noqa: BLE001 - try the next mirror
                            continue
                    self._version_cache = (now, payload)
                    cached = payload
                else:
                    cached = self._version_cache[1]
        regions = cached.get("regions") if isinstance(cached, dict) else None
        if isinstance(regions, dict):
            entry = regions.get(data_region)
            if isinstance(entry, dict):
                return entry
        return {}

    async def _download_table(self, data_region: str, table: str) -> Any:
        errors = []
        for source in self.sources:
            url = f"{source}/{data_region}/{table}.json"
            try:
                data = await self._fetch_bytes(url)
                return json.loads(data.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001 - try the next mirror
                errors.append(f"{source}: {exc}")
        raise MasterDataError("; ".join(errors[-2:]) or "no source available")

    async def _fetch_bytes(self, url: str) -> bytes:
        headers = {
            "User-Agent": "astrbot-plugin-bdon/1.0 (+https://github.com/StarMoe-org/moenotes)",
            "Accept": "application/json,*/*",
        }
        if aiohttp is not None:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        raise MasterDataError(f"HTTP {response.status}")
                    return await response.read()
        # Standard-library fallback for environments without aiohttp.
        import urllib.request

        def fetch() -> bytes:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()

        return await asyncio.to_thread(fetch)
