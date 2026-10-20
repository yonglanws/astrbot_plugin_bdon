"""Renders every page type to tests/output for visual review.

Usage (from the plugin directory):
    python tests/render_samples.py [region]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bdon.constants import (  # noqa: E402
    DEFAULT_ASSET_BASE,
    DEFAULT_DATA_SOURCES,
    DETAIL_WIDTH,
    LIST_PAGE_SIZE,
    LIST_SCALE,
    LIST_WIDTH,
    REGIONS,
    STAMP_LIST_WIDTH,
    list_columns,
)
from bdon.masterdata import MasterDataManager  # noqa: E402
from bdon.models import catalog_stats  # noqa: E402
from bdon.render import AstrBotRenderService, PageRenderer  # noqa: E402
from bdon.search import resolve  # noqa: E402

T2I_ENDPOINT = os.environ.get(
    "BDON_T2I_ENDPOINT", "http://127.0.0.1:8999/text2img"
)

KIND_ACCENTS = {
    "song": "#4C9DF0",
    "card": "#B86BD8",
    "support": "#3DBFA0",
    "stamp": "#D9A441",
    "comic": "#FF8899",
    "catalog": "#0072C2",
    "help": "#4C9DF0",
}
OUTPUT = ROOT / "tests" / "output"
DATA = ROOT / "tests" / ".data"


def base_context(
    manager: MasterDataManager,
    region: str,
    command: str,
    accent: str,
    script: str | None = None,
    region_prefix: str | None = None,
    page_width: int = LIST_WIDTH,
):
    profile = REGIONS[region]
    if script is None:
        script = "simplified" if region == "tw" else "traditional"
    if region_prefix is None:
        region_prefix = "" if region == "tw" else f"{region} "
    return {
        "region": profile,
        "script": script,
        "script_label": "日文"
        if region == "jp"
        else {"simplified": "简体", "traditional": "繁体"}[script],
        "region_prefix": region_prefix,
        "command_label": command,
        "accent": accent,
        "page_width": page_width,
        "page_title": f"{profile.label} · {command}",
    }


async def main(region: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manager = MasterDataManager(
        data_dir=DATA,
        asset_base=DEFAULT_ASSET_BASE,
        sources=list(DEFAULT_DATA_SOURCES),
        timeout=90,
    )
    print("syncing", region)
    await manager.sync_region(region, force=False)
    default_script = "simplified" if region == "tw" else "traditional"
    dataset = await manager.get_dataset(region, default_script)
    print(
        "loaded:",
        len(dataset.songs),
        "songs,",
        len(dataset.cards),
        "cards,",
        len(dataset.supports),
        "supports,",
        len(dataset.stamps),
        "stamps,",
        len(dataset.comics),
        "comics",
    )

    async def builtin_html_render(
        tmpl, data, return_url=True, options=None, umo=None
    ) -> str:
        """Mimics AstrBot's Star.html_render -> network t2i protocol."""
        payload = {
            "tmpl": tmpl,
            "json": return_url,
            "tmpldata": data,
            "options": options or {},
        }

        def post() -> tuple[bytes, str]:
            request = urllib.request.Request(
                f"{T2I_ENDPOINT}/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=240) as response:
                return response.read(), response.headers.get("Content-Type", "")

        raw, content_type = await asyncio.to_thread(post)
        suffix = ".jpg" if "jpeg" in content_type else ".png"
        path = OUTPUT / f"sample-{datetime.now():%H%M%S%f}{suffix}"
        path.write_bytes(raw)
        return str(path)

    renderer = PageRenderer(
        ROOT / "bdon" / "templates",
        AstrBotRenderService(builtin_html_render, timeout=120),
    )

    async def render(
        name: str,
        template: str,
        context: dict,
        width: int = DETAIL_WIDTH,
        image_type: str = "png",
        scale: str | None = LIST_SCALE,
    ):
        path = Path(
            await renderer.render(
                template,
                context,
                width=width,
                image_type=image_type,
                scale=scale,
            )
        )
        print("rendered", name, "->", path, path.stat().st_size, "bytes")
        return path

    async def render_list(
        name: str,
        template: str,
        context: dict,
        kind: str,
        items,
        width: int = LIST_WIDTH,
    ):
        context["page_width"] = width
        context["resolved"] = resolve(items, context.pop("query", ""), page_size=LIST_PAGE_SIZE, kind=kind)
        context["columns"] = list_columns(kind, len(context["resolved"].items))
        return await render(
            name,
            template,
            context,
            width=width,
            image_type="jpeg",
            scale=LIST_SCALE,
        )

    # Song
    song = dataset.songs[0]
    ctx = base_context(manager, region, "查歌 · 歌曲详情", song.band_color)
    ctx["song"] = song
    ctx["item"] = song
    await render("song_detail", "song_detail.html", ctx)

    ctx = base_context(manager, region, "查歌", KIND_ACCENTS["song"])
    ctx["query"] = ""
    await render_list("song_list", "song_list.html", ctx, "song", dataset.songs)

    # Card
    card = dataset.cards[0]
    ctx = base_context(manager, region, "查卡 · 卡牌详情", card.band_color)
    ctx["card"] = card
    ctx["item"] = card
    await render("card_detail", "card_detail.html", ctx)

    ctx = base_context(manager, region, "查卡", KIND_ACCENTS["card"])
    ctx["query"] = ""
    await render_list("card_list", "card_list.html", ctx, "card", dataset.cards)

    # Support
    support = dataset.supports[0]
    ctx = base_context(manager, region, "查支援卡 · 详情", support.band_color)
    ctx["support"] = support
    ctx["item"] = support
    await render("support_detail", "support_detail.html", ctx)

    ctx = base_context(manager, region, "查支援卡", KIND_ACCENTS["support"])
    ctx["query"] = ""
    await render_list(
        "support_list", "support_list.html", ctx, "support", dataset.supports
    )

    # Stamp list keeps its original 900px grid; a single stamp is sent as PNG.
    ctx = base_context(
        manager, region, "查贴纸", KIND_ACCENTS["stamp"], page_width=STAMP_LIST_WIDTH
    )
    ctx["query"] = ""
    await render_list(
        "stamp_list",
        "stamp_list.html",
        ctx,
        "stamp",
        dataset.stamps,
        width=STAMP_LIST_WIDTH,
    )

    # Traditional text variant: same server and query, different script.
    traditional = await manager.get_dataset(region, "traditional")
    ctx = base_context(
        manager,
        region,
        "查贴纸 · 繁体",
        KIND_ACCENTS["stamp"],
        script="traditional",
        region_prefix="tw " if region == "tw" else "jp ",
        page_width=STAMP_LIST_WIDTH,
    )
    ctx["query"] = ""
    await render_list(
        "stamp_list_tw",
        "stamp_list.html",
        ctx,
        "stamp",
        traditional.stamps,
        width=STAMP_LIST_WIDTH,
    )

    # Comic
    ctx = base_context(manager, region, "查漫画", KIND_ACCENTS["comic"])
    ctx["query"] = ""
    await render_list("comic_list", "comic_list.html", ctx, "comic", dataset.comics)

    # Catalog
    ctx = base_context(manager, region, "查图鉴", KIND_ACCENTS["catalog"])
    ctx["stats"] = catalog_stats(dataset)
    await render("catalog", "catalog.html", ctx, image_type="jpeg", scale=LIST_SCALE)

    # Help
    ctx = base_context(manager, region, "帮助", KIND_ACCENTS["help"])
    await render("help", "help.html", ctx)

    # Search result lists
    ctx = base_context(manager, region, "查歌 · MyGO", KIND_ACCENTS["song"])
    ctx["query"] = "MyGO"
    await render_list("song_search", "song_list.html", ctx, "song", dataset.songs)

    ctx = base_context(manager, region, "查卡 · SSR", KIND_ACCENTS["card"])
    ctx["query"] = "SSR"
    await render_list("card_search", "card_list.html", ctx, "card", dataset.cards)

    # Empty state
    ctx = base_context(manager, region, "查歌 · 不存在的歌", KIND_ACCENTS["song"])
    ctx["query"] = "不存在的歌zzz"
    await render_list("song_empty", "song_list.html", ctx, "song", dataset.songs)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "tw"))
