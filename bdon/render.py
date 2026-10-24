"""Jinja2 page rendering plus AstrBot's built-in html_render backend."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiohttp
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .assets import is_public_http_url

logger = logging.getLogger("astrbot")

# Starting viewport height for full-page screenshots. ``full_page`` grows the
# capture to the document's real height, so this only has to be tall enough for
# one layout pass; a small value keeps short pages from being padded with blank
# background. See AstrBotRenderService._options.
MIN_VIEWPORT_HEIGHT = 200

# The bundled MiSans face, embedded into every page. The t2i service renders a
# detached document, so a font file on disk is unreachable from the page; the
# face has to travel inside it. ``tests/build_font.py`` regenerates the subset
# (only the glyphs the templates and masterdata can display) and writes the
# base64 payload next to it.
FONT_FAMILY = "MiSansEmbedded"
FONT_B64_FILE = "misans-subset.b64.txt"


def _load_font_face(package_dir: Path) -> str:
    """Reads the bundled font's base64 payload; empty when it has not been built."""
    for candidate in (
        package_dir.parent / "res" / FONT_B64_FILE,
        package_dir / "res" / FONT_B64_FILE,
    ):
        try:
            payload = candidate.read_text(encoding="ascii").strip()
        except OSError:
            continue
        if payload:
            return payload
    return ""


def _inject_font_face(html: str, payload: str) -> str:
    """Prepends an ``@font-face`` rule so the page renders with MiSans.

    Injected before ``</head>`` (or at the top of the document) so it wins over
    the generic families the stylesheet lists as fallbacks. ``font-display:
    block`` keeps the page from painting a fallback face for the first frames.
    """
    rule = (
        f"<style>@font-face{{font-family:'{FONT_FAMILY}';"
        f"src:url(data:font/ttf;base64,{payload}) format('truetype');"
        f"font-weight:400 700;font-style:normal;font-display:block}}</style>"
    )
    lowered = html.lower()
    index = lowered.find("</head>")
    if index == -1:
        index = lowered.find("<body")
    if index == -1:
        return rule + html
    return html[:index] + rule + html[index:]


class RenderError(RuntimeError):
    pass


def build_environment(template_dir: Path) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["num"] = lambda value: f"{int(value or 0):,}"
    return env


class RenderService:
    def __init__(
        self,
        endpoint: str,
        output_dir: Path,
        timeout: int = 120,
        keep_files: int = 200,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.output_dir = Path(output_dir)
        self.timeout = max(15, int(timeout))
        self.keep_files = max(20, int(keep_files))
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def render(
        self,
        html: str,
        width: int = 1080,
        image_type: str = "png",
        quality: int = 90,
        scale: str = "high",
    ) -> Path:
        options: dict[str, Any] = {
            "type": image_type,
            "full_page": True,
            "viewport_width": int(width),
            # Not a cap: full_page still grows to the real document height. See
            # MIN_VIEWPORT_HEIGHT.
            "viewport_height": MIN_VIEWPORT_HEIGHT,
            "device_scale_factor_level": scale,
            "animations": "disabled",
            "caret": "hide",
            "timeout": self.timeout * 1000,
        }
        if image_type.lower() in {"jpeg", "jpg"}:
            options["quality"] = int(quality)
        payload = {"html": html, "options": options}
        data, content_type = await self._post(payload)
        if data is None:
            raise RenderError("渲染服务没有返回图片")
        return await asyncio.to_thread(self._save, data, content_type)

    async def _post(self, payload: dict[str, Any]) -> tuple[bytes | None, str]:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        headers = {
            "Content-Type": "application/json",
            "Accept": "image/png,image/jpeg,application/json",
        }
        urls = [f"{self.endpoint}/text2img/generate", f"{self.endpoint}/html"]
        # Also accept an endpoint that already names the t2i route, which is how
        # AstrBot's own t2i_endpoint is often written.
        if self.endpoint.rstrip("/").endswith("/text2img"):
            urls.insert(0, f"{self.endpoint}/generate")
        last_error: Exception | None = None
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            for url in urls:
                try:
                    async with session.post(url, json=payload) as response:
                        if response.status == 404:
                            last_error = RenderError(f"HTTP 404: {url}")
                            continue
                        if response.status >= 400:
                            body = (await response.text())[:300]
                            raise RenderError(f"HTTP {response.status}: {body}")
                        content_type = response.headers.get("Content-Type", "")
                        raw = await response.read()
                        if content_type.startswith("image/"):
                            return raw, content_type
                        return await self._decode_json(raw)
                except RenderError:
                    raise
                except Exception as exc:  # noqa: BLE001 - try the next endpoint shape
                    last_error = exc
        raise RenderError(f"无法连接渲染服务 {self.endpoint}: {last_error}")

    async def _decode_json(self, raw: bytes) -> tuple[bytes | None, str]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise RenderError("渲染服务返回了无法识别的内容") from exc
        if not isinstance(payload, dict):
            raise RenderError("渲染服务返回了无法识别的内容")
        for key in ("data", "image", "base64"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                if value.startswith("data:image"):
                    value = value.split(",", 1)[-1]
                try:
                    raw = base64.b64decode(value)
                except Exception:  # noqa: BLE001
                    continue
                return raw, self._image_type(raw)
        for key in ("url", "path", "file"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("/"):
                value = f"{self.endpoint}{value}"
            if isinstance(value, str) and value.startswith("http"):
                return await self._download(value), "image/png"
        raise RenderError(f"渲染服务返回了未知格式: {str(payload)[:200]}")

    async def _download(self, url: str) -> bytes:
        if not is_public_http_url(url):
            raise RenderError("渲染服务返回了非公网图片地址")
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # The render service must point at the image directly; refusing
            # redirects keeps a public URL from bouncing into a private host.
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    raise RenderError(f"下载渲染图片失败: HTTP {response.status}")
                return await response.read()

    @staticmethod
    def _image_type(raw: bytes) -> str:
        if raw.startswith(b"\xff\xd8"):
            return "image/jpeg"
        if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
            return "image/webp"
        return "image/png"

    def _save(self, data: bytes, content_type: str) -> Path:
        extension = ".jpg" if "jpeg" in content_type or "jpg" in content_type else ".png"
        name = f"bdon-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}{extension}"
        path = self.output_dir / name
        path.write_bytes(data)
        self._cleanup()
        return path

    def _cleanup(self) -> None:
        try:
            files = sorted(
                self.output_dir.glob("bdon-*"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        cutoff = time.time() - 3 * 24 * 3600
        for index, path in enumerate(files):
            try:
                if index >= self.keep_files or path.stat().st_mtime < cutoff:
                    os.remove(path)
            except OSError:
                continue


class AstrBotRenderService:
    """Renders through AstrBot's built-in ``Star.html_render``.

    AstrBot forwards the template to the t2i endpoint configured in AstrBot
    (``t2i_endpoint``), so plugins do not need their own HTTP client.  The
    direct ``RenderService`` remains available as an optional fallback for
    deployments that want to pin a specific endpoint.
    """

    def __init__(
        self,
        render_func: Callable[..., Any] | None,
        fallback: RenderService | None = None,
        timeout: int = 120,
        scale: str = "high",
    ) -> None:
        self.render_func = render_func
        self.fallback = fallback
        self.timeout = max(15, int(timeout))
        self.scale = scale if scale in {"normal", "high", "ultra"} else "high"

    @staticmethod
    def _escape_jinja(html: str) -> str:
        """Keep already-rendered HTML opaque to the remote Jinja2 pass.

        CSS single braces are untouched; only Jinja delimiters are escaped, so
        text such as ``{{name}}`` in a song title cannot become a template.
        """
        return (
            html.replace("{{", "&#123;{")
            .replace("{%", "&#123;%")
            .replace("{#", "&#123;#")
        )

    def _options(
        self,
        width: int,
        image_type: str,
        quality: int,
        scale: str = "high",
    ) -> dict[str, Any]:
        # ``viewport_height`` is the only height knob the t2i service exposes, and
        # it is a starting viewport, not a cap: with ``full_page`` the screenshot
        # still grows to the document's real height. Leaving it at the service
        # default (720) would pad every short page with a blank band of page
        # background, so pin it just tall enough to avoid an extra layout pass.
        options: dict[str, Any] = {
            "full_page": True,
            "type": image_type,
            "device_scale_factor_level": scale,
            "omit_background": False,
            "viewport_width": int(width),
            "viewport_height": MIN_VIEWPORT_HEIGHT,
            "timeout": self.timeout * 1000,
        }
        if image_type.lower() in {"jpeg", "jpg"}:
            options["quality"] = int(quality)
        return options

    async def render(
        self,
        html: str,
        width: int = 1080,
        image_type: str = "png",
        quality: int = 90,
        scale: str | None = None,
    ) -> str | Path:
        requested = str(scale or self.scale).lower()
        if requested not in {"normal", "high", "ultra"}:
            requested = "high"
        if self.render_func is None:
            if self.fallback is None:
                raise RenderError(
                    "当前 AstrBot 版本不支持内置 html_render，且未配置回退渲染服务"
                )
            return await self.fallback.render(
                html,
                width=width,
                image_type=image_type,
                quality=quality,
                scale=requested,
            )

        escaped = self._escape_jinja(html)
        last_error: Exception | None = None
        # Tall pages are sensitive to the scale factor and the behaviour differs
        # between t2i service builds, so try the requested scale first and fall
        # back through the other supported levels (highest quality first).
        order = {
            "ultra": ("ultra", "high", "normal"),
            "high": ("high", "ultra", "normal"),
            "normal": ("normal", "high", "ultra"),
        }[requested]
        for candidate in order:
            try:
                result = await self.render_func(
                    escaped,
                    {},
                    return_url=False,
                    options=self._options(width, image_type, quality, candidate),
                )
            except Exception as exc:  # noqa: BLE001 - try the next scale
                last_error = exc
                logger.warning(
                    "[bdon] AstrBot built-in html_render failed at scale %s: %s",
                    candidate,
                    exc,
                )
                continue
            if result:
                return result
            last_error = RenderError("AstrBot 内置生图没有返回图片")

        if self.fallback is not None:
            return await self.fallback.render(
                html,
                width=width,
                image_type=image_type,
                quality=quality,
                scale="normal",
            )
        raise RenderError(f"AstrBot 内置生图失败：{last_error}") from last_error


class PageRenderer:
    def __init__(self, template_dir: Path, service: Any) -> None:
        self.env = build_environment(template_dir)
        self.service = service
        self.font_face = _load_font_face(template_dir.parent)

    def html(self, template_name: str, context: dict[str, Any]) -> str:
        template = self.env.get_template(template_name)
        return template.render(**context)

    async def render(
        self,
        template_name: str,
        context: dict[str, Any],
        width: int = 1080,
        image_type: str = "png",
        quality: int = 90,
        scale: str | None = None,
    ) -> str | Path:
        html = self.html(template_name, context)
        if self.font_face:
            html = _inject_font_face(html, self.font_face)
        return await self.service.render(
            html,
            width=width,
            image_type=image_type,
            quality=quality,
            scale=scale,
        )
