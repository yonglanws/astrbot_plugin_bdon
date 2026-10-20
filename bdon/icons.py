"""Rarity and attribute icons published by bdon.moe.

The site overlays these on every card thumbnail: a neon rarity mark in the
corner and a coloured attribute icon next to the card type. They are small
transparent PNGs, so the renderer embeds them as data URIs — the t2i service
renders a detached document and cannot fetch them from the plugin directory.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

from .constants import ATTRIBUTES, MEMBER_RARITIES, SUPPORT_RARITIES

ICON_DIR = Path(__file__).resolve().parents[1] / "res" / "icons"

# bdon.moe names its member-card rarity marks RarityIcon_<label> and its support
# (Snap) marks SP_CardRarityIcon_<label>; the "生日" member rarity is the odd one
# out and ships as RarityIcon_BD.
_BD_LABEL = "BD"

# Support rarity 10 is labelled 特殊 in-game but published as a plain R mark.
_SUPPORT_R_OVERRIDE = "R"


def _rarity_file(kind: str, label: str) -> str:
    if kind == "support":
        return f"SP_CardRarityIcon_{label}.png"
    if label == MEMBER_RARITIES[20][0]:
        return f"RarityIcon_{_BD_LABEL}.png"
    return f"RarityIcon_{label}.png"


def _attribute_file(label: str) -> str:
    return f"CardType-{label}.png"


@lru_cache(maxsize=32)
def _data_uri(path: str) -> str:
    """Reads one icon as a ``data:image/png;base64,...`` URI (cached)."""
    try:
        payload = Path(path).read_bytes()
    except OSError:
        return ""
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def rarity_icon(kind: str, rarity: int) -> str:
    """Data URI for one rarity mark; empty when the icon is not bundled."""
    table = MEMBER_RARITIES if kind == "card" else SUPPORT_RARITIES
    entry = table.get(rarity)
    if not entry:
        return ""
    label = entry[0]
    if kind == "support" and rarity == 10:
        label = _SUPPORT_R_OVERRIDE
    return _data_uri(str(ICON_DIR / _rarity_file(kind, label)))


def attribute_icon(card_type: int) -> str:
    """Data URI for one attribute icon; empty when the icon is not bundled."""
    entry = ATTRIBUTES.get(card_type)
    if not entry:
        return ""
    return _data_uri(str(ICON_DIR / _attribute_file(entry[1])))


def available_icons() -> dict[str, bool]:
    """Which bundled icons resolved, for diagnostics and tests."""
    result: dict[str, bool] = {}
    for kind, table in (("card", MEMBER_RARITIES), ("support", SUPPORT_RARITIES)):
        for rarity in table:
            result[f"{kind}/{rarity}"] = bool(rarity_icon(kind, rarity))
    for card_type in ATTRIBUTES:
        result[f"attr/{card_type}"] = bool(attribute_icon(card_type))
    return result
