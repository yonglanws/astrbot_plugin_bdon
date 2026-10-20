"""Builds the published Our Notes asset URLs for one game server.

The asset service publishes every MasterData image as
``{base}/{region}/{language}/{key}/{label}.webp`` where ``key`` is the
MasterData path without its extension and ``label`` is the key's last segment.
The TW catalog is the service's default region, so its URLs have no region
segment.
"""

from __future__ import annotations

from urllib.parse import quote

from .constants import RegionProfile


class AssetBuilder:
    def __init__(
        self, base: str, region: RegionProfile, script: str = "simplified"
    ) -> None:
        self.base = base.rstrip("/")
        self.region = region
        # The TW catalog publishes both Chinese scripts; pick the one that
        # matches the command text so a Simplified query does not return
        # Traditional artwork (sticker images carry visible Chinese text).
        if region.key == "tw":
            self.language = "zh-Hans" if script == "simplified" else "zh-Hant"
        else:
            self.language = region.asset_language

    def url(self, path: str, label: str | None = None) -> str:
        key = path.strip().lstrip("/")
        if key.lower().endswith(".png"):
            key = key[:-4]
        # Member-card atlases are published under the original image's key.
        if key.endswith("_atlas"):
            key = key[: -len("_atlas")]
        if not key:
            return ""
        if label is None:
            label = f"{key.split('/')[-1]}.webp"
        segments = [
            self.region.asset_prefix,
            self.language,
            *key.split("/"),
            label,
        ]
        return self.base + "/" + "/".join(quote(part, safe="") for part in segments if part)

    def alternate(self, path: str, label: str | None = None) -> str:
        """The other Chinese script, used as a browser-side fallback on TW."""
        if self.region.key != "tw":
            return ""
        alternate = "zh-Hant" if self.language == "zh-Hans" else "zh-Hans"
        key = path.strip().lstrip("/")
        if key.lower().endswith(".png"):
            key = key[:-4]
        if key.endswith("_atlas"):
            key = key[: -len("_atlas")]
        if not key:
            return ""
        if label is None:
            label = f"{key.split('/')[-1]}.webp"
        segments = [alternate, *key.split("/"), label]
        return self.base + "/" + "/".join(quote(part, safe="") for part in segments if part)

    def pair(self, path: str, label: str | None = None) -> tuple[str, str]:
        return self.url(path, label), self.alternate(path, label)

    # -- helpers named after the MasterData paths ---------------------------------
    def jacket(self, asset_name: str) -> tuple[str, str]:
        return self.pair(f"Image/Jacket/{asset_name}.png")

    def card_full(self, asset_id: int) -> tuple[str, str]:
        return self.pair(f"MemberCard/{asset_id}/member_full_atlas.png")

    def card_thumb(self, asset_id: int) -> tuple[str, str]:
        return self.pair(f"MemberCard/{asset_id}/member_thumbnail_atlas.png")

    def support_full(self, asset_id: int) -> tuple[str, str]:
        return self.pair(f"SupportCard/{asset_id}/snap_full.png")

    def support_thumb(self, asset_id: int) -> tuple[str, str]:
        return self.pair(f"SupportCard/{asset_id}/snap_thumbnail.png")

    def stamp(self, asset_path: str) -> tuple[str, str]:
        return self.pair(f"{asset_path}.png")

    def comic(self, asset_name: str) -> tuple[str, str]:
        return self.pair(f"Image/Comic/{asset_name}.png")

    def event_logo(self, asset_path: str) -> tuple[str, str]:
        return self.pair(f"Image/Event/{asset_path}.png") if asset_path else ("", "")

    def event_background(self, asset_path: str) -> tuple[str, str]:
        return self.pair(f"Image/Event/{asset_path}.png") if asset_path else ("", "")

    def event_banner(self, asset_path: str) -> tuple[str, str]:
        return self.pair(f"Image/Event/{asset_path}.png") if asset_path else ("", "")

    def item(self, image_path: str) -> tuple[str, str]:
        return self.pair(f"{image_path}.png") if image_path else ("", "")

    def character_face(self, character_id: int) -> tuple[str, str]:
        return self.pair(f"Character/Image/{character_id}/character_face_icon.png")
