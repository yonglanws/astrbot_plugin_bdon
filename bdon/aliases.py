"""Community-maintained character aliases for search.

The game's own tables only carry the official name in each language, but
players search by nicknames ("灯"), romanised short forms ("tomori", "tmr")
or Chinese-circle spellings.  ``res/character_aliases.json`` holds those
extra spellings, keyed by character ID, and is merged into each character's
search text when the dataset is built.
"""

from __future__ import annotations

import json
import threading
from functools import lru_cache
from pathlib import Path


ALIAS_FILE = Path(__file__).resolve().parents[1] / "res" / "character_aliases.json"

_lock = threading.Lock()
_cache: dict[int, tuple[str, ...]] | None = None


def _load() -> dict[int, tuple[str, ...]]:
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is not None:
            return _cache
        table: dict[int, tuple[str, ...]] = {}
        try:
            payload = json.loads(ALIAS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = table
            return table
        raw = payload.get("characters") if isinstance(payload, dict) else None
        if isinstance(raw, dict):
            for key, values in raw.items():
                try:
                    character_id = int(key)
                except (TypeError, ValueError):
                    continue
                if isinstance(values, str):
                    values = [values]
                table[character_id] = tuple(
                    str(value).strip() for value in values or [] if str(value).strip()
                )
        _cache = table
        return table


@lru_cache(maxsize=1)
def all_aliases() -> dict[int, tuple[str, ...]]:
    """Every known alias, keyed by character ID."""
    return dict(_load())


def character_aliases(character_id: int) -> tuple[str, ...]:
    """Aliases for one character; empty when the file has no entry."""
    return _load().get(int(character_id or 0), ())
