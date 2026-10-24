"""Renders every page type to tests/output for visual review.

Usage (from the plugin directory):
    python tests/render_samples.py [region]
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bdon.chart import load_score  # noqa: E402
from bdon.constants import (  # noqa: E402
    BROWSE_KINDS,
    BROWSE_LIMIT,
    DEFAULT_ASSET_BASE,
    DEFAULT_DATA_SOURCES,
    DETAIL_WIDTH,
    EVENT_WIDTH,
    LIST_PAGE_SIZE,
    LIST_SCALE,
    LIST_WIDTH,
    REGIONS,
    STAMP_LIST_WIDTH,
    list_columns,
)
from bdon.masterdata import MasterDataManager  # noqa: E402
from bdon.models import catalog_stats  # noqa: E402
from bdon.render import PageRenderer, RenderService  # noqa: E402
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
    "event": "#E05A7A",
    "catalog": "#0072C2",
    "help": "#4C9DF0",
}
OUTPUT = ROOT / "tests" / "output"
DATA = ROOT / "tests" / ".data"
# ``BDON_SAMPLES_ONLY=song_detail,card_detail`` re-renders just those samples.
ONLY = {
    part.strip()
    for part in os.environ.get("BDON_SAMPLES_ONLY", "").split(",")
    if part.strip()
}


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

    # Use the plugin's own HTTP render path (the same endpoint contract as
    # AstrBot's t2i fallback) instead of bespoke network code in this tool.
    renderer = PageRenderer(
        ROOT / "bdon" / "templates",
        RenderService(T2I_ENDPOINT, OUTPUT, timeout=240),
    )

    async def render(
        name: str,
        template: str,
        context: dict,
        width: int = DETAIL_WIDTH,
        image_type: str = "png",
        scale: str | None = LIST_SCALE,
    ):
        if ONLY and name not in ONLY:
            return None
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
        query = context.pop("query", "")
        browse = None
        if not query and kind in BROWSE_KINDS and len(items) > BROWSE_LIMIT:
            ordered = sorted(
                items,
                key=lambda item: (item.start_dt or datetime.min, item.id),
                reverse=True,
            )
            browse = {
                "shown": BROWSE_LIMIT,
                "total": len(items),
                "limit": BROWSE_LIMIT,
            }
            items = ordered[:BROWSE_LIMIT]
        context["resolved"] = resolve(items, query, page_size=LIST_PAGE_SIZE, kind=kind)
        context["columns"] = list_columns(kind, len(context["resolved"].items))
        if browse is not None:
            context["browse"] = browse
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
    song_chart = DATA / "charts" / "chart_0001_0001_03.json"
    if song_chart.is_file():
        score = load_score(song_chart.read_bytes())
        bpm_min, bpm_max = score.bpm_range()
        seconds = max(0.0, score.position(score.max_tick())[2] / 1000.0)
        difficulty = song.difficulties[-1] if song.difficulties else None
        nps = difficulty.notes / seconds if difficulty is not None and seconds > 0 else None
        ctx["metrics"] = {
            "difficulty_label": difficulty.label if difficulty is not None else "EX",
            "bpm_label": f"{bpm_min:g}" if bpm_min == bpm_max else f"{bpm_min:g}–{bpm_max:g}",
            "duration_label": f"{int(seconds) // 60}:{int(seconds) % 60:02d}",
            "bars": score.position(score.max_tick())[0] + 1,
            "nps": f"{nps:.2f}" if nps is not None else None,
        }
    await render("song_detail", "song_detail.html", ctx)

    # 2D chart sheets (查谱面), rendered from bundled score fixtures so the
    # samples stay reproducible offline (live charts come from assets.bdon.moe).
    from bdon.chartsheet import build_sheet  # noqa: E402
    from bdon.constants import DIFFICULTY_NAMES  # noqa: E402

    fixture = DATA / "charts" / "chart_0001_0001_03.json"

    def chart_context(chart_song, difficulty, path: Path):
        credits = []
        if chart_song.lyricist:
            credits.append(f"作词 {chart_song.lyricist}")
        if chart_song.composer:
            credits.append(f"作曲 {chart_song.composer}")
        if chart_song.arranger:
            credits.append(f"编曲 {chart_song.arranger}")
        sheet = build_sheet(load_score(path.read_bytes()))
        ctx = base_context(
            manager, region, "查谱面", difficulty.color,
            page_width=sheet["page_width"],
        )
        ctx.update(
            song=chart_song,
            difficulty=difficulty,
            difficulty_name=DIFFICULTY_NAMES.get(difficulty.key, difficulty.label),
            sheet=sheet,
            prefix="",
            credit_line=" · ".join(credits),
        )
        return ctx

    if fixture.is_file():
        chart_song = next(
            (s for s in dataset.songs if s.display_id == 1), dataset.songs[0]
        )
        expert = next((d for d in chart_song.difficulties if d.key == "expert"), None)
        if expert is not None:
            context = chart_context(chart_song, expert, fixture)
            await render(
                "chart_ex",
                "chart_preview.html",
                context,
                width=context["page_width"],
                image_type="jpeg",
                scale=LIST_SCALE,
            )
        easy = next((d for d in chart_song.difficulties if d.key == "easy"), None)
        easy_fixture = DATA / "charts" / "chart_0001_0001_00.json"
        if easy is not None and easy_fixture.is_file():
            context = chart_context(chart_song, easy, easy_fixture)
            await render(
                "chart_ez",
                "chart_preview.html",
                context,
                width=context["page_width"],
                image_type="jpeg",
                scale=LIST_SCALE,
            )
        # A longer chart renders wider, never taller: 17 columns at the same
        # fixed sheet height.
        dense_fixture = DATA / "charts" / "chart_0050_0050_03.json"
        dense_song = next((s for s in dataset.songs if s.display_id == 50), None)
        if dense_song is not None and dense_fixture.is_file():
            dense = next(
                (d for d in dense_song.difficulties if d.key == "expert"),
                dense_song.difficulties[-1],
            )
            context = chart_context(dense_song, dense, dense_fixture)
            await render(
                "chart_dense",
                "chart_preview.html",
                context,
                width=context["page_width"],
                image_type="jpeg",
                scale=LIST_SCALE,
            )
        # Near-overlapping notes raise px/beat instead of dropping notes; this
        # chart is taller than the others but still one complete image.
        spaced_fixture = DATA / "charts" / "chart_0114_0114_03.json"
        spaced_song = next((s for s in dataset.songs if s.display_id == 114), None)
        if spaced_song is not None and spaced_fixture.is_file():
            spaced = next(
                (d for d in spaced_song.difficulties if d.key == "expert"),
                spaced_song.difficulties[-1],
            )
            context = chart_context(spaced_song, spaced, spaced_fixture)
            await render(
                "chart_spacing",
                "chart_preview.html",
                context,
                width=context["page_width"],
                image_type="jpeg",
                scale=LIST_SCALE,
            )

    ctx = base_context(manager, region, "查歌", KIND_ACCENTS["song"])
    ctx["query"] = ""
    await render_list("song_list", "song_list.html", ctx, "song", dataset.songs)

    # Card
    card = dataset.cards[0]
    ctx = base_context(manager, region, "查卡 · 卡牌详情", card.band_color)
    ctx["card"] = card
    ctx["item"] = card
    await render("card_detail", "card_detail.html", ctx)

    # A lower-rarity card has a shorter level cap and one skill slot fewer.
    plain_card = min(dataset.cards, key=lambda card: card.rarity)
    ctx = base_context(manager, region, "查卡 · 卡牌详情", plain_card.band_color)
    ctx["card"] = plain_card
    ctx["item"] = plain_card
    await render("card_detail_r", "card_detail.html", ctx)

    ctx = base_context(manager, region, "查卡", KIND_ACCENTS["card"])
    ctx["query"] = ""
    await render_list("card_list", "card_list.html", ctx, "card", dataset.cards)

    # Support
    support = dataset.supports[0]
    ctx = base_context(manager, region, "查支援卡 · 详情", support.band_color)
    ctx["support"] = support
    ctx["item"] = support
    await render("support_detail", "support_detail.html", ctx)

    # Another card to check a gekisou support skill and its level table.
    gekisou_support = next(
        (
            entry
            for entry in dataset.supports
            if any(skill.label.startswith("激奏支援") for skill in entry.skills)
        ),
        support,
    )
    ctx = base_context(
        manager, region, "查支援卡 · 详情", gekisou_support.band_color
    )
    ctx["support"] = gekisou_support
    ctx["item"] = gekisou_support
    await render("support_detail_g", "support_detail.html", ctx)

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

    # Events: current event detail and the full list.
    current = next(
        (event for event in dataset.events if event.status == "ongoing"),
        dataset.events[0] if dataset.events else None,
    )
    if current is not None:
        ctx = base_context(
            manager,
            region,
            "查活动 · 活动详情",
            KIND_ACCENTS["event"],
            page_width=EVENT_WIDTH,
        )
        ctx["event"] = current
        ctx["item"] = current
        await render("event_detail", "event_detail.html", ctx, width=EVENT_WIDTH)

        ctx = base_context(
            manager, region, "活动一览", KIND_ACCENTS["event"], page_width=EVENT_WIDTH
        )
        ctx["resolved"] = resolve(
            dataset.events, "", page_size=LIST_PAGE_SIZE, kind="event"
        )
        ctx["columns"] = list_columns("event", len(dataset.events))
        await render(
            "event_list",
            "event_list.html",
            ctx,
            width=EVENT_WIDTH,
            image_type="jpeg",
            scale=LIST_SCALE,
        )

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

    # Help / command guide (static content, no dataset needed)
    ctx = base_context(manager, region, "帮助", KIND_ACCENTS["help"])
    await render("help", "help.html", ctx, image_type="jpeg", scale=LIST_SCALE)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "tw"))
