"""AstrBot plugin: BanG Dream! Our Notes song / card / support / stamp / comic queries."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from astrbot.api import logger

try:
    import aiohttp
except ImportError:  # pragma: no cover - aiohttp ships with AstrBot
    aiohttp = None  # type: ignore[assignment]

try:
    from astrbot.api import AstrBotConfig
except ImportError:  # older AstrBot releases type the config as a plain dict
    AstrBotConfig = dict  # type: ignore[misc,assignment]

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .bdon.constants import (
    ART_COMMANDS,
    BROWSE_KINDS,
    BROWSE_LIMIT,
    COMMAND_ALIASES,
    DEFAULT_ASSET_BASE,
    DEFAULT_DATA_SOURCES,
    DETAIL_WIDTH,
    KIND_COMMANDS,
    KIND_LABELS,
    LIST_JPEG_QUALITY,
    LIST_PAGE_SIZE,
    LIST_SCALE,
    LIST_WIDTH,
    PLUGIN_NAME,
    REGION_ALIASES,
    REGIONS,
    SCRIPT_LABELS,
    STAMP_LIST_WIDTH,
    SYNC_COMMANDS,
    list_columns,
    resolve_region,
)
from .bdon.masterdata import MasterDataManager
from .bdon.models import catalog_stats
from .bdon.render import AstrBotRenderService, PageRenderer, RenderError, RenderService
from .bdon.search import Resolved, resolve


KIND_ACCENTS = {
    "song": "#4C9DF0",
    "card": "#B86BD8",
    "support": "#3DBFA0",
    "stamp": "#D9A441",
    "comic": "#FF8899",
    "event": "#E05A7A",
    "event_list": "#E05A7A",
    "catalog": "#0072C2",
    "help": "#4C9DF0",
}

CATALOG_SUBKINDS = {
    "歌": "song",
    "曲": "song",
    "歌曲": "song",
    "卡": "card",
    "卡牌": "card",
    "支援": "support",
    "支援卡": "support",
    "贴纸": "stamp",
    "貼紙": "stamp",
    "表情": "stamp",
    "漫画": "comic",
    "漫畫": "comic",
    "活动": "event",
    "活動": "event",
}


def _build_command_pattern() -> re.Pattern[str]:
    commands = sorted(
        list(COMMAND_ALIASES) + list(ART_COMMANDS) + list(SYNC_COMMANDS),
        key=len,
        reverse=True,
    )
    regions = sorted(REGION_ALIASES, key=len, reverse=True)
    command_words = "|".join(re.escape(word) for word in commands)
    region_words = "|".join(re.escape(word) for word in regions)
    return re.compile(
        rf"(?i)^[/／!！]?\s*(?:(?P<region>{region_words})\s*)?"
        rf"(?P<cmd>{command_words})(?P<args>[\s\S]*)$",
        re.IGNORECASE,
    )


COMMAND_RE = _build_command_pattern()


def _data_dir(plugin_name: str) -> Path:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        return Path(get_astrbot_data_path()) / "plugin_data" / plugin_name
    except Exception:  # noqa: BLE001 - older AstrBot layouts
        try:
            from astrbot.api.star import StarTools

            return Path(StarTools.get_data_dir(plugin_name))
        except Exception:  # noqa: BLE001 - direct/test invocation
            return Path(__file__).resolve().parent / "data"


def _display_prefix(prefix: str | None) -> str:
    """The prefix shown in command hints.

    cn is the default, so hints omit it; every other region keeps its prefix and
    a trailing space (``jp查卡`` -> ``jp 查卡``).
    """
    value = (prefix or "").strip().lower()
    if not value or value == "cn":
        return ""
    return f"{value} "


def _discard(path: Path) -> None:
    """Removes a superseded cache file, ignoring races with another request."""
    try:
        path.unlink()
    except OSError:
        pass


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class BdonPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.plugin_name = PLUGIN_NAME
        self.data_dir = _data_dir(self.plugin_name)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.image_dir = self.data_dir / "images"
        self.image_dir.mkdir(parents=True, exist_ok=True)

        sources = self.config.get("data_source_bases") or list(DEFAULT_DATA_SOURCES)
        if not isinstance(sources, list):
            sources = list(DEFAULT_DATA_SOURCES)
        self.manager = MasterDataManager(
            data_dir=self.data_dir,
            asset_base=str(self.config.get("asset_base") or DEFAULT_ASSET_BASE),
            sources=[str(source) for source in sources if str(source).strip()],
            timeout=_as_int(self.config.get("sync_timeout"), 60),
        )
        render_endpoint = str(self.config.get("render_endpoint") or "").strip()
        fallback_service = None
        if render_endpoint:
            fallback_service = RenderService(
                endpoint=render_endpoint,
                output_dir=self.data_dir / "renders",
                timeout=_as_int(self.config.get("render_timeout"), 120),
                keep_files=200,
            )
        # Primary backend: AstrBot's own html_render, which forwards to the t2i
        # endpoint configured in AstrBot (t2i_endpoint).
        self.renderer = PageRenderer(
            Path(__file__).resolve().parent / "bdon" / "templates",
            AstrBotRenderService(
                getattr(self, "html_render", None),
                fallback=fallback_service,
                timeout=_as_int(self.config.get("render_timeout"), 120),
            ),
        )
        self._startup_task: asyncio.Task[Any] | None = None
        self._daily_task: asyncio.Task[Any] | None = None

    # -- lifecycle -------------------------------------------------------------
    async def initialize(self) -> None:
        self._startup_task = asyncio.create_task(self._startup_sync())
        if bool(self.config.get("auto_sync", True)):
            self._daily_task = asyncio.create_task(
                self.manager.daily_loop(
                    lambda: list(REGIONS),
                    hour=_as_int(self.config.get("sync_hour"), 4),
                    minute=30,
                )
            )
        logger.info("[bdon] initialized; default region cn (simplified)")

    async def terminate(self) -> None:
        tasks = [
            task
            for task in (self._startup_task, self._daily_task)
            if task and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._startup_task = None
        self._daily_task = None

    async def _startup_sync(self) -> None:
        await asyncio.sleep(3)
        # Always check every server: the manifest read is cheap and a version
        # match skips the download, so this keeps jp/kr/en current even when the
        # admin only ever queries the default server. The first install pulls
        # all four in parallel (about two seconds in practice).
        fresh = not any(self.manager.has_required_data(key) for key in REGIONS)
        if fresh:
            logger.info("[bdon] first run: syncing all servers")
        try:
            await self.manager.sync_all(list(REGIONS), force=False, parallel=True)
        except Exception:  # noqa: BLE001 - startup must not crash the bot
            logger.exception("[bdon] startup sync failed")

    # -- command entry ---------------------------------------------------------
    @filter.regex(COMMAND_RE.pattern)
    async def on_bdon_message(self, event: AstrMessageEvent):
        text = event.get_message_str().strip()
        match = COMMAND_RE.match(text)
        if not match:
            return
        raw_command = match.group("cmd") or ""
        region_prefix = (match.group("region") or "").strip()
        args = (match.group("args") or "").strip()

        if raw_command.lower() in {word.lower() for word in SYNC_COMMANDS}:
            event.stop_event()
            async for result in self._handle_sync(event):
                yield result
            return

        kind = COMMAND_ALIASES.get(raw_command) or COMMAND_ALIASES.get(
            raw_command.lower()
        )
        art_kind = ART_COMMANDS.get(raw_command.lower())
        if kind is None and art_kind is None:
            return
        # A bare "帮助" / "图鉴" is too easy to collide with other plugins;
        # require a region prefix (or a brand prefix for help).
        if not region_prefix and raw_command.lower() in {
            "帮助",
            "说明",
            "說明",
            "help",
            "图鉴",
            "圖鑑",
        }:
            return

        region, script = resolve_region(region_prefix)
        event.stop_event()

        if art_kind is not None:
            async for result in self._handle_art(
                event, region, script, region_prefix, art_kind, args
            ):
                yield result
            return

        if kind == "help":
            try:
                path = await self._render_help()
            except Exception as exc:  # noqa: BLE001
                logger.exception("[bdon] help render failed")
                yield event.plain_result(f"渲染失败：{exc}")
                return
            yield event.image_result(str(path))
            return

        profile = REGIONS[region]
        if not self.manager.has_required_data(region):
            yield event.plain_result(
                f"首次查询{profile.label}，正在同步 MasterData，请稍候…"
            )
        try:
            dataset = await self.manager.get_dataset(region, script)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[bdon] masterdata unavailable for %s", region)
            yield event.plain_result(
                f"{profile.label} 数据暂不可用：{exc}\n请稍后重试，或让管理员发送「同步数据」。"
            )
            return

        try:
            if kind == "event_list":
                path = await self._render_event_list(
                    region, script, region_prefix, dataset
                )
            elif kind == "catalog":
                subkind = CATALOG_SUBKINDS.get(args)
                if subkind == "event":
                    path = await self._render_event_list(
                        region, script, region_prefix, dataset, "图鉴"
                    )
                elif subkind:
                    path = await self._render_query(
                        region, script, region_prefix, dataset, subkind, ""
                    )
                else:
                    path = await self._render_catalog(
                        region, script, region_prefix, dataset
                    )
            else:
                path = await self._render_query(
                    region, script, region_prefix, dataset, kind, args
                )
        except RenderError as exc:
            logger.warning("[bdon] render failed: %s", exc)
            yield event.plain_result(f"图片渲染失败：{exc}")
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("[bdon] query failed")
            yield event.plain_result(f"查询失败：{exc}")
            return
        yield event.image_result(str(path))

    # -- sync ------------------------------------------------------------------
    async def _handle_sync(self, event: AstrMessageEvent):
        if not event.is_admin():
            yield event.plain_result("只有管理员可以同步 MasterData。")
            return
        # A manual sync always covers every server: an admin asking for a refresh
        # means "make sure everything is up to date", not "refresh the default".
        regions = list(REGIONS)
        yield event.plain_result(
            f"开始同步 {len(regions)} 个区服的数据："
            + "、".join(REGIONS[key].label for key in regions)
        )
        results = await self.manager.sync_all(regions, force=True, parallel=True)
        lines = []
        for key, (ok, message) in results.items():
            lines.append(f"{'✓' if ok else '✗'} {REGIONS[key].label}：{message}")
        yield event.plain_result("同步完成\n" + "\n".join(lines))

    # -- raw artwork -----------------------------------------------------------
    async def _handle_art(
        self,
        event: AstrMessageEvent,
        region: str,
        script: str,
        region_prefix: str,
        kind: str,
        args: str,
    ):
        """``card <id>`` / ``scard <id>``: send the original card art."""
        profile = REGIONS[region]
        if not self.manager.has_required_data(region):
            yield event.plain_result(
                f"首次查询{profile.label}，正在同步 MasterData，请稍候…"
            )
        try:
            dataset = await self.manager.get_dataset(region, script)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[bdon] masterdata unavailable for %s", region)
            yield event.plain_result(
                f"{profile.label} 数据暂不可用：{exc}\n请稍后重试，或让管理员发送「同步数据」。"
            )
            return

        items = dataset.items_for(kind)
        label = KIND_LABELS[kind]
        command = "card" if kind == "card" else "scard"
        prefix = _display_prefix(region_prefix)
        query = unicodedata.normalize("NFKC", args).strip().lstrip("#＃").strip()
        if not query:
            sample = items[0].display_id if items else 1
            yield event.plain_result(
                f"用法：{prefix}{command} {label}ID（例如 {prefix}{command} {sample}）。"
                f"也可以直接发送 {prefix}{command} {label}名。"
            )
            return

        item = None
        if query.isdigit():
            item = next(
                (
                    entry
                    for entry in items
                    if str(entry.id) == query or str(entry.display_id) == query
                ),
                None,
            )
            if item is None:
                yield event.plain_result(f"没有找到 ID 为 {query} 的{label}。")
                return
        else:
            resolved = resolve(items, query, page_size=1, kind=kind)
            if resolved.mode == "detail" and resolved.item is not None:
                item = resolved.item
            elif resolved.items:
                item = resolved.items[0]
                if resolved.total > 1:
                    yield event.plain_result(
                        f"匹配到 {resolved.total} 个{label}，先返回最接近的"
                        f"「{item.title_for_search}」#{item.display_id}。"
                    )
            else:
                yield event.plain_result(f"没有找到匹配「{query}」的{label}。")
                return

        primary = getattr(item, "art_url", "")
        fallback = getattr(item, "art_fallback", "")
        path = await self._fetch_image(primary)
        if path is None and fallback:
            path = await self._fetch_image(fallback)
        if path is not None:
            yield event.image_result(str(path))
        elif primary or fallback:
            # Let the adapter fetch the asset itself rather than showing nothing.
            yield event.image_result(primary or fallback)
        else:
            yield event.plain_result(
                f"「{item.title_for_search}」#{item.display_id} 暂无卡面图片。"
            )

    async def _fetch_image(self, url: str) -> Path | None:
        """Downloads an asset once and keeps it in the plugin data directory."""
        url = (url or "").strip()
        if not url:
            return None
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            suffix = ".png"
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
        cached = self.image_dir / f"{digest}{suffix}"
        # webp originals are converted to png and the webp is removed, so the
        # converted file is what a warm cache looks like.
        if suffix != ".png":
            converted = cached.with_suffix(".png")
            if converted.is_file() and converted.stat().st_size > 0:
                return converted
        if cached.is_file() and cached.stat().st_size > 0:
            return await self._prefer_png(cached)

        data = await self._download_image(url)
        if not data:
            return None
        try:
            temporary = cached.with_suffix(cached.suffix + ".part")
            temporary.write_bytes(data)
            temporary.replace(cached)
        except OSError:
            return None
        self._cleanup_images()
        return await self._prefer_png(cached)

    async def _download_image(self, url: str) -> bytes | None:
        timeout = _as_int(self.config.get("image_timeout"), 120)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "image/webp,image/png,image/jpeg,image/*;q=0.8",
            "Referer": "https://bdon.moe/",
        }
        try:
            if aiohttp is not None:
                client_timeout = aiohttp.ClientTimeout(total=timeout)
                async with aiohttp.ClientSession(
                    timeout=client_timeout, headers=headers
                ) as session:
                    async with session.get(url) as response:
                        if response.status != 200:
                            return None
                        return await response.read()
            return await asyncio.to_thread(
                self._download_image_sync, url, timeout, headers
            )
        except Exception as exc:  # noqa: BLE001 - fall back to the remote URL
            logger.warning("[bdon] image download failed: %s (%s)", url, exc)
            return None

    @staticmethod
    def _download_image_sync(url: str, timeout: int, headers: dict[str, str]) -> bytes:
        import urllib.request

        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    @staticmethod
    async def _prefer_png(path: Path) -> Path:
        """Converts webp to png when Pillow is available, for wider adapter support.

        The asset service only publishes webp, but several chat adapters refuse
        it, so ``card`` / ``scard`` / 漫画 would silently fail to display. The
        converted png replaces the webp once it is on disk, keeping the cache at
        one file per asset.
        """
        if path.suffix.lower() != ".webp":
            return path
        png = path.with_suffix(".png")
        if png.is_file() and png.stat().st_size > 0:
            _discard(path)
            return png
        try:
            from PIL import Image
        except ImportError:
            return path

        def convert() -> None:
            with Image.open(path) as image:
                image.convert("RGBA").save(png, "PNG", optimize=True)

        try:
            await asyncio.to_thread(convert)
        except Exception:  # noqa: BLE001 - send the original webp
            # Another request may have finished the conversion and removed the
            # webp while this one was reading it.
            if png.is_file() and png.stat().st_size > 0:
                return png
            return path
        _discard(path)
        return png

    def _cleanup_images(self) -> None:
        try:
            files = sorted(
                self.image_dir.glob("*"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        cutoff = time.time() - 7 * 24 * 3600
        for index, path in enumerate(files):
            try:
                if index >= 300 or path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    # -- rendering -------------------------------------------------------------
    def _base_context(
        self,
        region: str,
        script: str,
        region_prefix: str,
        command_label: str,
        accent: str,
        page_width: int = LIST_WIDTH,
    ) -> dict[str, Any]:
        profile = REGIONS[region]
        # JP masterdata only ships Japanese text, so the traditional-script
        # selection falls through to Japanese there; label the footer by what
        # the page actually shows.
        script_label = "日文" if region == "jp" else SCRIPT_LABELS.get(script, "")
        return {
            "region": profile,
            "script": script,
            "script_label": script_label,
            "region_prefix": _display_prefix(region_prefix),
            "command_label": command_label,
            "accent": accent,
            "page_width": page_width,
            "page_title": f"{profile.label} · {command_label} · 汐见萤Bot",
        }

    async def _render_help(self) -> str | Path:
        # Help describes the default experience: 港台服 rendered in Simplified
        # Chinese, with no region prefix in any example.
        context = self._base_context(
            "tw", "simplified", "", "帮助", KIND_ACCENTS["help"]
        )
        context["page_title"] = "邦多利 Our Notes 帮助 · 汐见萤Bot"
        return await self.renderer.render(
            "help.html", context, width=LIST_WIDTH, scale=LIST_SCALE
        )

    async def _render_catalog(
        self, region: str, script: str, region_prefix: str, dataset: Any
    ) -> str | Path:
        context = self._base_context(
            region,
            script,
            region_prefix,
            KIND_COMMANDS["catalog"],
            KIND_ACCENTS["catalog"],
            page_width=LIST_WIDTH,
        )
        context["stats"] = catalog_stats(dataset)
        return await self.renderer.render(
            "catalog.html",
            context,
            width=LIST_WIDTH,
            image_type="jpeg",
            quality=LIST_JPEG_QUALITY,
            scale=LIST_SCALE,
        )

    async def _render_event_list(
        self,
        region: str,
        script: str,
        region_prefix: str,
        dataset: Any,
        command_label: str = "活动一览",
    ) -> str | Path:
        items = dataset.items_for("event")
        resolved = Resolved(mode="list", items=list(items), total=len(items))
        return await self._render_event_listing(
            region, script, region_prefix, resolved, command_label
        )

    async def _render_event_listing(
        self,
        region: str,
        script: str,
        region_prefix: str,
        resolved: Resolved,
        command_label: str,
    ) -> str | Path:
        context = self._base_context(
            region,
            script,
            region_prefix,
            command_label,
            KIND_ACCENTS["event"],
            page_width=LIST_WIDTH,
        )
        context["resolved"] = resolved
        context["kind"] = "event"
        context["columns"] = list_columns("event", len(resolved.items))
        return await self.renderer.render(
            "event_list.html",
            context,
            width=LIST_WIDTH,
            image_type="jpeg",
            quality=LIST_JPEG_QUALITY,
            scale=LIST_SCALE,
        )

    @staticmethod
    def _resolve_event(events: list[Any], args: str) -> Resolved:
        """Empty query shows what is running now, else the nearest event."""
        query = (args or "").strip()
        if query:
            return resolve(events, query, page_size=LIST_PAGE_SIZE, kind="event")

        current = [event for event in events if event.status == "ongoing"]
        if current:
            if len(current) == 1:
                return Resolved(
                    mode="detail", item=current[0], total=1, exact=True
                )
            return Resolved(mode="list", items=current, total=len(current))

        upcoming = sorted(
            (event for event in events if event.status == "upcoming" and event.start_dt),
            key=lambda event: event.start_dt,
        )
        if upcoming:
            return Resolved(mode="detail", item=upcoming[0], total=1, exact=True)

        ended = sorted(
            (event for event in events if event.start_dt),
            key=lambda event: event.start_dt,
            reverse=True,
        )
        if ended:
            return Resolved(mode="detail", item=ended[0], total=1, exact=True)
        return Resolved(mode="list", total=0)

    async def _render_query(
        self,
        region: str,
        script: str,
        region_prefix: str,
        dataset: Any,
        kind: str,
        args: str,
    ) -> str | Path:
        items = dataset.items_for(kind)
        command_label = KIND_COMMANDS[kind]
        browse: dict[str, int] | None = None

        if kind == "event":
            resolved = self._resolve_event(items, args)
        else:
            if (
                not (args or "").strip()
                and kind in BROWSE_KINDS
                and len(items) > BROWSE_LIMIT
            ):
                ordered = sorted(
                    items,
                    key=lambda item: (
                        getattr(item, "start_dt", None) or datetime.min,
                        item.id,
                    ),
                    reverse=True,
                )
                browse = {
                    "shown": BROWSE_LIMIT,
                    "total": len(items),
                    "limit": BROWSE_LIMIT,
                }
                items = ordered[:BROWSE_LIMIT]
            resolved = resolve(items, args, page_size=LIST_PAGE_SIZE, kind=kind)

        if resolved.mode == "detail" and resolved.item is not None:
            item = resolved.item
            # Stickers and comics are single finished images: send the asset
            # itself instead of wrapping it in a rendered page.
            if kind in {"stamp", "comic"}:
                label = KIND_LABELS[kind]
                path = await self._fetch_image(item.image_url)
                if path is None:
                    path = await self._fetch_image(item.image_fallback)
                if path is not None:
                    return path
                if item.image_url:
                    return item.image_url
                raise RenderError(f"「{item.name}」暂无{label}图片")
            accent = getattr(item, "band_color", "") or KIND_ACCENTS[kind]
            context = self._base_context(
                region,
                script,
                region_prefix,
                f"{command_label} · {KIND_LABELS[kind]}详情",
                accent,
                page_width=DETAIL_WIDTH,
            )
            context["item"] = item
            context[kind] = item
            return await self.renderer.render(
                f"{kind}_detail.html",
                context,
                width=DETAIL_WIDTH,
                scale=LIST_SCALE,
            )

        if kind == "event":
            return await self._render_event_listing(
                region,
                script,
                region_prefix,
                resolved,
                command_label + (f" · {resolved.query}" if resolved.query else ""),
            )

        # The sticker grid keeps its original denser canvas; every other list
        # shares the wider canvas so cards line up at a readable size.
        width = STAMP_LIST_WIDTH if kind == "stamp" else LIST_WIDTH
        context = self._base_context(
            region,
            script,
            region_prefix,
            f"{command_label}" + (f" · {resolved.query}" if resolved.query else ""),
            KIND_ACCENTS[kind],
            page_width=width,
        )
        context["resolved"] = resolved
        context["kind"] = kind
        context["columns"] = list_columns(kind, len(resolved.items))
        if browse is not None:
            context["browse"] = browse
        # Long card grids compress well as high-quality JPEG while keeping text
        # crisp; detail pages stay PNG.
        return await self.renderer.render(
            f"{kind}_list.html",
            context,
            width=width,
            image_type="jpeg",
            quality=LIST_JPEG_QUALITY,
            scale=LIST_SCALE,
        )
