"""Builds the published Our Notes asset URLs for one game server.

The asset service publishes every MasterData image as
``{base}/{region}/{language}/{key}/{label}.webp`` where ``key`` is the
MasterData path without its extension and ``label`` is the key's last segment.
The TW catalog is the service's default region, so its URLs have no region
segment.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import quote, urlparse

from .constants import RegionProfile


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    # ::ffff:127.0.0.1 must be judged as the IPv4 loopback it really is.
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return address.is_global


def is_public_http_url(url: str) -> bool:
    """Whether a server-side fetch of ``url`` is allowed: public http(s) hosts.

    Blocks non-http schemes, localhost names and loopback / private / reserved
    address literals so a misconfigured base cannot turn the bot into an SSRF
    probe. DNS names are allowed on purpose: deployments behind Clash/sing-box
    TUN get synthetic fake-IP answers (198.18/15), and a separate lookup here
    would reject a host the HTTP client can still reach through the proxy.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith((".localhost", ".local")):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True
    return _is_public_address(host)


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
        key = key.removesuffix("_atlas")
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
        key = key.removesuffix("_atlas")
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

    def skill_icon(self, asset_name: str) -> tuple[str, str]:
        return self.pair(f"Character/Skill/{asset_name}.png") if asset_name else ("", "")
