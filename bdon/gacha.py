"""Simulated 卡池 pulls and the per-user counters behind them.

The pool itself is built in :mod:`bdon.models`; this module only turns its
weighted prize list into draws. A ten-pull reserves its last draw for SR or
above, matching the game's ``_ensuredCount`` / ``_ensuredRarity`` product rule
and bdon.moe's simulator.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import (
    GACHA_KIND_LABELS,
    Gacha,
    GachaDraw,
    gacha_rarity_info,
)

TEN_PULL = 10
MAX_PULLS = 10
MIN_PULLS = 1
# MasterGachaProduct._ensuredRarity on the ten-pull products: SR.
GUARANTEE_RARITY = 3

COUNT_WORDS: dict[str, int] = {
    "单抽": 1,
    "單抽": 1,
    "单": 1,
    "單": 1,
    "一抽": 1,
    "十连": 10,
    "十連": 10,
    "十抽": 10,
    "十次": 10,
    "10连": 10,
    "10連": 10,
}


def parse_sim_args(
    args: str, default_count: int = 1
) -> tuple[str, int]:
    """Splits ``[卡池关键词] [1|10]`` into ``(query, count)``.

    A trailing 1/10 is a pull count, while any other number is a pool ID or a
    word of the pool name. ``#1`` forces the ID interpretation for the two
    ambiguous values, and a count word (``十连`` / ``单抽``) works anywhere.
    """
    tokens = [
        token
        for token in re.split(r"\s+", unicodedata.normalize("NFKC", args or "").strip())
        if token
    ]
    count = default_count
    query: list[str] = []
    for index, token in enumerate(tokens):
        lowered = token.lower()
        if lowered in COUNT_WORDS:
            count = COUNT_WORDS[lowered]
            continue
        if token.startswith(("#", "＃")):
            query.append(token.lstrip("#＃"))
            continue
        if token.isdigit():
            value = int(token)
            if index == len(tokens) - 1 and value in (1, 10):
                count = value
            else:
                query.append(token)
            continue
        query.append(token)
    return " ".join(query).strip(), max(MIN_PULLS, min(MAX_PULLS, count))


def default_gacha(gachas: Iterable[Gacha]) -> Gacha | None:
    """The pool a bare ``模拟抽卡`` should use: current, then newest."""
    items = list(gachas)
    if not items:
        return None
    ongoing = [gacha for gacha in items if gacha.status == "ongoing"]
    if ongoing:
        return max(ongoing, key=lambda gacha: (gacha.priority, gacha.id))
    upcoming = sorted(
        (gacha for gacha in items if gacha.status == "upcoming" and gacha.start_dt),
        key=lambda gacha: gacha.start_dt,
    )
    if upcoming:
        return upcoming[0]
    permanent = [gacha for gacha in items if gacha.status == "permanent"]
    if permanent:
        return max(permanent, key=lambda gacha: gacha.id)
    return max(items, key=lambda gacha: (gacha.priority, gacha.id))


def _weighted_choice(
    draws: list[GachaDraw], rng: Any = random
) -> GachaDraw:
    total = sum(max(0.0, draw.weight) for draw in draws)
    if total <= 0:
        return draws[0]
    roll = rng.random() * total
    upto = 0.0
    for draw in draws:
        upto += max(0.0, draw.weight)
        if roll < upto:
            return draw
    return draws[-1]


@dataclass
class DrawResult:
    """One card/item in a pull."""

    draw: GachaDraw
    index: int
    guaranteed: bool = False

    @property
    def name(self) -> str:
        return self.draw.name

    @property
    def subtitle(self) -> str:
        return self.draw.subtitle

    @property
    def kind_label(self) -> str:
        return self.draw.kind_label

    @property
    def rarity_label(self) -> str:
        return self.draw.rarity_label

    @property
    def rarity_color(self) -> str:
        return self.draw.rarity_color

    @property
    def rarity_icon(self) -> str:
        return self.draw.rarity_icon

    @property
    def pickup(self) -> bool:
        return self.draw.pickup

    @property
    def image_url(self) -> str:
        return self.draw.image_url

    @property
    def image_fallback(self) -> str:
        return self.draw.image_fallback

    @property
    def art_url(self) -> str:
        return self.draw.art_url

    @property
    def art_fallback(self) -> str:
        return self.draw.art_fallback

    @property
    def amount(self) -> int:
        return self.draw.amount


def perform_draw(
    gacha: Gacha, count: int, rng: Any = random
) -> list[DrawResult]:
    """Draws ``count`` prizes; a ten-pull's last draw is SR or above."""
    count = max(MIN_PULLS, min(MAX_PULLS, int(count)))
    pool = [draw for draw in gacha.draws if draw.weight > 0]
    if not pool:
        return []
    guarantee_rarity = gacha.ten_guarantee_rarity or GUARANTEE_RARITY
    guarantee_pool = [
        draw
        for draw in pool
        if draw.kind != "item" and draw.rarity >= guarantee_rarity
    ]
    guarantee_count = (
        1
        if count >= TEN_PULL and guarantee_pool and gacha.has_ten_guarantee
        else 0
    )
    results: list[DrawResult] = []
    for index in range(count):
        guaranteed = bool(
            guarantee_count and index >= count - guarantee_count
        )
        candidates = guarantee_pool if guaranteed else pool
        results.append(
            DrawResult(
                draw=_weighted_choice(candidates, rng),
                index=index + 1,
                guaranteed=guaranteed,
            )
        )
    return results


def _to_int(value: Any, default: int = 0) -> int:
    """Lenient integer parsing for the on-disk counter file."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class GachaStats:
    """A user's cumulative pulls for one pool."""

    pulls: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    pickups: int = 0
    updated: float = 0.0

    def record(self, results: Iterable[DrawResult]) -> None:
        for result in results:
            draw = result.draw
            key = f"{draw.kind}:{draw.rarity}"
            self.counts[key] = self.counts.get(key, 0) + 1
            self.pulls += 1
            if draw.pickup:
                self.pickups += 1
        self.updated = time.time()

    @property
    def ssr_count(self) -> int:
        """SSR-tier pulls; the 生日 rarity is the member-card premium tier."""
        return (
            self.counts.get("member:4", 0)
            + self.counts.get("member:20", 0)
            + self.counts.get("support:4", 0)
        )

    @property
    def sr_count(self) -> int:
        return self.counts.get("member:3", 0) + self.counts.get("support:3", 0)

    @property
    def r_count(self) -> int:
        return self.counts.get("member:2", 0) + self.counts.get("support:2", 0)

    @property
    def other_count(self) -> int:
        return max(0, self.pulls - self.ssr_count - self.sr_count - self.r_count)

    @staticmethod
    def _rate_label(value: int, total: int) -> str:
        return f"{value / total * 100:.1f}%" if total else "—"

    @property
    def ssr_rate_label(self) -> str:
        return self._rate_label(self.ssr_count, self.pulls)

    @property
    def sr_rate_label(self) -> str:
        return self._rate_label(self.sr_count, self.pulls)

    @property
    def r_rate_label(self) -> str:
        return self._rate_label(self.r_count, self.pulls)

    def rarity_rows(self) -> list[dict[str, Any]]:
        """Non-zero rarity buckets, member first, premium tiers on top."""
        rows: list[dict[str, Any]] = []
        for kind in ("member", "support", "item"):
            for rarity in (20, 10, 4, 3, 2, 0):
                count = self.counts.get(f"{kind}:{rarity}", 0)
                if not count:
                    continue
                label, color, icon = gacha_rarity_info(kind, rarity)
                rows.append(
                    {
                        "label": label,
                        "kind_label": GACHA_KIND_LABELS.get(kind, "道具"),
                        "color": color,
                        "icon": icon,
                        "count": count,
                    }
                )
        return rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "pulls": self.pulls,
            "counts": dict(self.counts),
            "pickups": self.pickups,
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> GachaStats:
        if not isinstance(raw, dict):
            return cls()
        counts = raw.get("counts")
        if not isinstance(counts, dict):
            counts = {}
        return cls(
            pulls=_to_int(raw.get("pulls")),
            counts={
                str(key): _to_int(value)
                for key, value in counts.items()
            },
            pickups=_to_int(raw.get("pickups")),
            updated=_to_float(raw.get("updated")),
        )


class GachaStateStore:
    """JSON-backed pull counters, one bucket per ``(user, gacha)``.

    The file lives in the plugin data directory, so a bot restart keeps the
    counts. Writes are atomic (``.part`` + ``os.replace``) and serialised by one
    asyncio lock; the store is small enough to keep in memory after the first
    read.
    """

    def __init__(self, path: Path, keep_users: int = 500) -> None:
        self.path = Path(path)
        self.keep_users = max(50, int(keep_users))
        self._users: dict[str, dict[str, dict[str, Any]]] | None = None
        self._lock = asyncio.Lock()

    def _load_sync(self) -> dict[str, dict[str, dict[str, Any]]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        users = payload.get("users") if isinstance(payload, dict) else None
        if not isinstance(users, dict):
            return {}
        return {
            str(user): dict(bucket)
            for user, bucket in users.items()
            if isinstance(bucket, dict)
        }

    def _save_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".part")
        payload = {"version": 1, "users": self._users or {}}
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    async def _ensure_loaded(self) -> dict[str, dict[str, dict[str, Any]]]:
        if self._users is None:
            self._users = await asyncio.to_thread(self._load_sync)
        return self._users

    def _prune(self) -> None:
        if not self._users or len(self._users) <= self.keep_users:
            return
        ranked = sorted(
            self._users.items(),
            key=lambda pair: max(
                (
                    _to_float(value.get("updated"))
                    for value in pair[1].values()
                ),
                default=0.0,
            ),
            reverse=True,
        )
        self._users = dict(ranked[: self.keep_users])

    async def record(
        self, user: str, gacha_id: int, results: list[DrawResult]
    ) -> GachaStats:
        async with self._lock:
            users = await self._ensure_loaded()
            bucket = users.setdefault(str(user), {})
            stats = GachaStats.from_dict(bucket.get(str(gacha_id)))
            stats.record(results)
            bucket[str(gacha_id)] = stats.to_dict()
            self._prune()
            await asyncio.to_thread(self._save_sync)
            return stats

    async def stats(self, user: str, gacha_id: int) -> GachaStats:
        async with self._lock:
            users = await self._ensure_loaded()
            return GachaStats.from_dict(
                users.get(str(user), {}).get(str(gacha_id))
            )

    async def reset(self, user: str, gacha_id: int) -> bool:
        async with self._lock:
            users = await self._ensure_loaded()
            bucket = users.get(str(user))
            if not bucket or str(gacha_id) not in bucket:
                return False
            bucket.pop(str(gacha_id), None)
            if not bucket:
                users.pop(str(user), None)
            await asyncio.to_thread(self._save_sync)
            return True
