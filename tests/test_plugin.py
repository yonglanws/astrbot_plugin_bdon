"""Import and command-parsing tests that do not require a running AstrBot."""

from __future__ import annotations

import asyncio
import logging
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))


def _install_astrbot_stubs() -> None:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = logging.getLogger("test.bdon")
    api.AstrBotConfig = dict

    event_module = types.ModuleType("astrbot.api.event")

    class AstrMessageEvent:  # noqa: D401 - stub
        pass

    class _Filter:
        def regex(self, pattern):
            def decorator(function):
                function.__regex_pattern__ = pattern
                return function

            return decorator

    event_module.AstrMessageEvent = AstrMessageEvent
    event_module.filter = _Filter()

    star_module = types.ModuleType("astrbot.api.star")

    class Star:
        def __init__(self, context, config=None):
            self.context = context

        async def initialize(self):
            return None

        async def terminate(self):
            return None

        async def html_render(self, tmpl, data, return_url=True, options=None, umo=None):
            return str(Path(__file__).resolve().parent / "output" / "mock-html-render.jpg")

    class Context:
        pass

    star_module.Star = Star
    star_module.Context = Context

    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.event": event_module,
            "astrbot.api.star": star_module,
        }
    )


_install_astrbot_stubs()

import importlib  # noqa: E402

plugin_main = importlib.import_module("astrbot_plugin_bdon.main")


class FakeEvent:
    def __init__(self, text: str, admin: bool = False):
        self.text = text
        self.admin = admin
        self.stopped = False
        self.results = []

    def get_message_str(self) -> str:
        return self.text

    def stop_event(self) -> None:
        self.stopped = True

    def is_admin(self) -> bool:
        return self.admin

    def plain_result(self, text: str):
        return ("plain", text)

    def image_result(self, path: str):
        return ("image", path)


def test_patterns() -> None:
    cases = {
        "查歌": ("", "查歌", ""),
        "cn查歌": ("cn", "查歌", ""),
        "cn查歌 迷星叫": ("cn", "查歌", "迷星叫"),
        "cn查歌迷星叫": ("cn", "查歌", "迷星叫"),
        "/jp查卡 高松燈": ("jp", "查卡", "高松燈"),
        "／cn查歌 迷星叫": ("cn", "查歌", "迷星叫"),
        "JP查卡 64": ("JP", "查卡", "64"),
        "hk查支援卡": ("hk", "查支援卡", ""),
        "tw查贴纸 -p2": ("tw", "查贴纸", "-p2"),
        "kr查漫画": ("kr", "查漫画", ""),
        "en查图鉴": ("en", "查图鉴", ""),
        "繁中查歌": ("繁中", "查歌", ""),
        "港台查歌": ("港台", "查歌", ""),
        "查活动": ("", "查活动", ""),
        "cn查活动 3": ("cn", "查活动", "3"),
        "活动一览": ("", "活动一览", ""),
        "查表情包 灯": ("", "查表情包", "灯"),
        "同步数据": ("", "同步数据", ""),
        "card 64": ("", "card", "64"),
        "scard 12": ("", "scard", "12"),
        "jpcard 64": ("jp", "card", "64"),
        "krscard 12": ("kr", "scard", "12"),
        "卡面 64": ("", "卡面", "64"),
        "cn支援卡面 12": ("cn", "支援卡面", "12"),
        "看卡面 64": ("", "看卡面", "64"),
        "cn看支援卡 12": ("cn", "看支援卡", "12"),
        "看歌曲 迷星叫": ("", "看歌曲", "迷星叫"),
        "歌曲 迷星叫": ("", "歌曲", "迷星叫"),
        "song 迷星叫": ("", "song", "迷星叫"),
        "music 迷星叫": ("", "music", "迷星叫"),
        "查牌 64": ("", "查牌", "64"),
        "活动": ("", "活动", ""),
        "event 3": ("", "event", "3"),
        "查曲绘 迷星叫": ("", "查曲绘", "迷星叫"),
        "曲绘 迷星叫": ("", "曲绘", "迷星叫"),
        "谱面 迷星叫 ex": ("", "谱面", "迷星叫 ex"),
        "谱面预览 迷星叫": ("", "谱面预览", "迷星叫"),
        "谱面查询 迷星叫": ("", "谱面查询", "迷星叫"),
        "查铺面 迷星叫": ("", "查铺面", "迷星叫"),
        "铺面查询 迷星叫": ("", "铺面查询", "迷星叫"),
        "铺面预览 迷星叫": ("", "铺面预览", "迷星叫"),
        "帮助": ("", "帮助", ""),
        "cn帮助": ("cn", "帮助", ""),
        "jp帮助": ("jp", "帮助", ""),
        "help": ("", "help", ""),
        "说明": ("", "说明", ""),
    }
    for text, expected in cases.items():
        match = plugin_main.COMMAND_RE.match(text)
        assert match is not None, f"did not match {text!r}"
        actual = (
            (match.group("region") or ""),
            match.group("cmd") or "",
            (match.group("args") or "").strip(),
        )
        assert actual == expected, f"{text!r}: {actual} != {expected}"
    assert plugin_main.COMMAND_RE.match("今天天气不错") is None
    print("pattern tests passed")


def test_icons() -> None:
    from astrbot_plugin_bdon.bdon.icons import available_icons

    resolved = available_icons()
    # 特殊 support cards deliberately have no mark: the only published file is
    # a plain R, which would mislabel them.
    assert resolved.pop("support/10") is False, resolved
    assert resolved and all(resolved.values()), resolved
    print("icon tests passed")


def test_layout() -> None:
    from astrbot_plugin_bdon.bdon.constants import list_columns

    # Four columns is the cap: it keeps cards near 250px so the enlarged type
    # stays readable instead of squeezing five small cards into one row.
    assert list_columns("card", 70) == 4
    assert list_columns("song", 9) == 4
    assert list_columns("song", 6) == 3
    assert list_columns("card", 3) == 3
    assert list_columns("support", 73) == 4
    assert list_columns("support", 2) == 2
    assert list_columns("comic", 25) == 4
    assert list_columns("event", 3) == 2
    assert list_columns("event", 1) == 1
    print("layout tests passed")


def test_plugin() -> None:
    plugin = plugin_main.BdonPlugin(None, {})
    # 港台服 is the only server behind the default prefix; cn selects Simplified
    # text while tw/hk select the server's native Traditional text.
    assert plugin_main.resolve_region(None) == ("tw", "simplified")
    assert plugin_main.resolve_region("cn") == ("tw", "simplified")
    assert plugin_main.resolve_region("tw") == ("tw", "traditional")
    assert plugin_main.resolve_region("hk") == ("tw", "traditional")
    assert plugin_main.resolve_region("jp") == ("jp", "traditional")
    assert plugin_main.resolve_region("kr") == ("kr", "traditional")
    assert plugin_main.resolve_region("en") == ("en", "traditional")
    assert plugin_main._display_prefix("cn") == ""
    assert plugin_main._display_prefix("") == ""
    assert plugin_main._display_prefix("tw") == "tw "

    async def run() -> None:
        output = ROOT / "tests" / "output" / "route-test.png"
        output.parent.mkdir(parents=True, exist_ok=True)

        # A bare 图鉴 is still too collision-prone and needs a prefix.
        event = FakeEvent("图鉴")
        results = [result async for result in plugin.on_bdon_message(event)]
        assert results == [] and not event.stopped

        # Non-admin sync is rejected.
        event = FakeEvent("同步数据", admin=False)
        results = [result async for result in plugin.on_bdon_message(event)]
        assert event.stopped and results[0][0] == "plain"

        # Exercise the real query routing against the downloaded sample data.
        data_dir = ROOT / "tests" / ".data"
        if (data_dir / "masterdata" / "hk-tw-mo" / "MasterLiveMusic.json").is_file():
            from astrbot_plugin_bdon.bdon.masterdata import MasterDataManager

            manager = MasterDataManager(
                data_dir,
                plugin.manager.asset_base,
                sources=plugin.manager.sources,
            )
            dataset = await manager.get_dataset("tw", "simplified")
            traditional = await manager.get_dataset("tw", "traditional")
            captured: list[tuple[str, dict]] = []
            widths: list[int] = []

            async def fake_get(
                region: str, script: str = "simplified", force_sync: bool = False
            ):
                return traditional if script == "traditional" else dataset

            async def fake_render(
                template: str,
                context: dict,
                width: int = 1080,
                image_type: str = "png",
                quality: int = 90,
                scale: str | None = None,
            ):
                captured.append((template, context))
                widths.append(width)
                path = output.parent / f"route-{len(captured)}.png"
                path.write_bytes(b"test")
                return path

            plugin.manager.get_dataset = fake_get  # type: ignore[assignment]
            plugin.manager.has_required_data = lambda region: True  # type: ignore[assignment]
            plugin.renderer.render = fake_render  # type: ignore[assignment]

            # Song details ask the (cached) chart for BPM / duration. Keep the
            # test offline by answering from the bundled fixture.
            from astrbot_plugin_bdon.bdon.chart import load_score

            chart_fixture = (
                ROOT / "tests" / ".data" / "charts" / "chart_0001_0001_03.json"
            )
            chart_score = (
                load_score(chart_fixture.read_bytes())
                if chart_fixture.is_file()
                else None
            )

            async def fake_chart_score(
                region: str,
                script: str,
                chart_key: str,
                timeout: int | None = None,
            ):
                if chart_score is None:
                    raise plugin_main.RenderError("offline")
                return chart_score

            plugin._load_chart_score = fake_chart_score  # type: ignore[assignment]

            cases = [
                ("查歌 迷星叫", "song_detail.html", "song"),
                ("cn查歌 迷星叫", "song_detail.html", "song"),
                ("cn查歌 MyGO", "song_list.html", None),
                ("cn查卡 64", "card_detail.html", "card"),
                ("cn查卡 #64", "card_detail.html", "card"),
                ("cn查卡 ６４", "card_detail.html", "card"),
                ("hk查卡 64", "card_detail.html", "card"),
                ("cn查支援卡", "support_list.html", None),
                ("cn查支援卡 1", "support_detail.html", "support"),
                ("看卡面 71", "card_detail.html", "card"),
                ("看支援卡 1", "support_detail.html", "support"),
                ("看歌曲 迷星叫", "song_detail.html", "song"),
                ("歌曲 迷星叫", "song_detail.html", "song"),
                ("music 迷星叫", "song_detail.html", "song"),
                ("查牌 64", "card_detail.html", "card"),
                ("活动", "event_detail.html", "event"),
                ("event 3", "event_detail.html", "event"),
                ("cn查活动", "event_detail.html", "event"),
                ("cn查活动 3", "event_detail.html", "event"),
                ("cn活动一览", "event_list.html", None),
                ("cn查贴纸", "stamp_list.html", None),
                ("tw查贴纸", "stamp_list.html", None),
                ("cn查漫画", "comic_list.html", None),
                ("cn查图鉴", "catalog.html", None),
                ("cn查图鉴 歌曲", "song_list.html", None),
                ("cn查图鉴 活动", "event_list.html", None),
                ("cn查曲绘 MyGO", "song_list.html", None),
                ("cn帮助", "help.html", None),
                ("help", "help.html", None),
            ]
            for text, template, key in cases:
                event = FakeEvent(text)
                results = [result async for result in plugin.on_bdon_message(event)]
                assert results and results[-1][0] == "image", (text, results)
                assert captured[-1][0] == template, (text, captured[-1][0])
                if key:
                    assert key in captured[-1][1], (text, captured[-1][1].keys())
                # The sticker grid keeps its original narrow canvas; event
                # pages use their own narrower canvas for larger effective text.
                expected_width = (
                    900
                    if template
                    in {"stamp_list.html", "event_detail.html", "event_list.html"}
                    else 1080
                )
                assert widths[-1] == expected_width, (text, widths[-1])
                if template.endswith("_list.html"):
                    assert captured[-1][1]["columns"] >= 1
                assert event.stopped

            assert captured[0][1]["script"] == "simplified"
            assert captured[0][1]["region_prefix"] == ""

            # The song page fills BPM / duration from the chart; card and
            # support pages show all five skill levels plus max-build growth.
            song_metrics = captured[0][1]["metrics"]
            assert song_metrics is not None
            assert song_metrics["bpm_label"] and song_metrics["duration_label"]
            card_context = next(
                context
                for template, context in captured
                if template == "card_detail.html"
            )
            assert card_context["card"].growth is not None
            assert card_context["card"].growth.total_power > card_context["card"].total_power
            assert len(card_context["card"].skills) == 3
            assert all(
                len(skill.levels) == 5 for skill in card_context["card"].skills
            )
            support_context = next(
                context
                for template, context in captured
                if template == "support_detail.html"
            )
            assert support_context["support"].growth is not None
            assert support_context["support"].skills
            assert all(
                len(skill.levels) == 5
                for skill in support_context["support"].skills
            )
            # Every skill in the downloaded tables has five levels, and each
            # level has its real numbers rather than a placeholder.
            for item in (*dataset.cards, *dataset.supports):
                for skill in item.skills:
                    assert len(skill.levels) == 5, (item.id, skill.label)
                    assert all(level.description for level in skill.levels), (
                        item.id,
                        skill.label,
                    )

            # 查谱面: argument parsing and offline-safe routes (the score
            # download itself is covered by tests/test_chart.py fixtures).
            parse = plugin_main.BdonPlugin._parse_chart_args
            assert parse("1 ex") == ("1", "expert")
            assert parse("ex 1") == ("1", "expert")
            assert parse("迷星叫 ez") == ("迷星叫", "easy")
            assert parse("#12") == ("12", None)
            assert parse("3") == ("3", None)  # a lone number is a song ID
            assert parse("1 4") == ("1", "expert")  # second digit = difficulty
            assert parse("") == ("", None)

            event = FakeEvent("查谱面")
            results = [result async for result in plugin.on_bdon_message(event)]
            assert event.stopped and results[0][0] == "plain"
            assert "用法" in results[0][1]

            event = FakeEvent("查谱面 不存在的歌zzz")
            results = [result async for result in plugin.on_bdon_message(event)]
            assert results[0][0] == "plain" and "没有找到" in results[0][1]

            # Lists still fit in one image. Cards and songs only browse the
            # newest 20 without a keyword; every other table stays complete.
            for text, shown, total in (
                ("cn查歌", 20, 91),
                ("cn查卡", 20, 71),
                ("cn查支援卡", 74, 74),
                ("cn查贴纸", 84, 84),
                ("cn查漫画", 25, 25),
            ):
                event = FakeEvent(text)
                results = [result async for result in plugin.on_bdon_message(event)]
                assert results and results[-1][0] == "image", (text, results)
                resolved = captured[-1][1]["resolved"]
                assert resolved.pages == 1 and resolved.total == shown, (
                    text,
                    resolved.total,
                    resolved.pages,
                )
                browse = captured[-1][1].get("browse")
                if total > shown:
                    assert browse and browse["total"] == total, (text, browse)
                else:
                    assert browse is None, (text, browse)

            # A keyword still searches the whole card/song table.
            event = FakeEvent("cn查歌 MyGO")
            results = [result async for result in plugin.on_bdon_message(event)]
            assert results and results[-1][0] == "image", results
            assert captured[-1][1]["resolved"].total > 20
            assert "browse" not in captured[-1][1]

            # cn and tw/hk share one server but render different scripts from the
            # same tables; the cache keeps both variants.
            assert dataset.script == "simplified"
            assert await manager.get_dataset("tw", "simplified") is dataset
            assert traditional.script == "traditional"
            assert any("灯" in stamp.name for stamp in dataset.stamps)
            assert any("燈" in stamp.name for stamp in traditional.stamps)
            # Sticker names follow the selected script instead of leaking the
            # traditional MasterText field into the default Simplified list.
            assert dataset.stamps[0].name == "灯的表情1"
            assert dataset.stamps[0].characters[0].name == "高松灯"
            assert all("貼圖" not in stamp.name for stamp in dataset.stamps)
            # Sticker artwork itself is localised: the Simplified dataset must
            # not send the Traditional image files.
            assert "/zh-Hans/" in dataset.stamps[0].image_url
            assert "/zh-Hant/" in traditional.stamps[0].image_url

            # Event data comes from MasterEvent plus its bonus/reward tables.
            assert len(dataset.events) == 3
            current_events = [event for event in dataset.events if event.status == "ongoing"]
            assert len(current_events) == 1
            current_event = current_events[0]
            assert current_event.name == "Shake Your 魂, Baby!!"
            assert current_event.song is not None
            assert current_event.member_bonuses and current_event.support_bonuses
            assert current_event.point_rewards and current_event.live_rewards

            # Song live rules: per-song score thresholds (C–SS, the zero-score
            # D row is dropped) and the shared combo ladder of the hardest
            # chart, whose combo counts are ceil of the note share.
            import math

            song_100015 = next(song for song in dataset.songs if song.id == 100015)
            assert [rank.label for rank in song_100015.score_ranks] == [
                "C", "B", "A", "S", "SS",
            ]
            assert all(rank.required > 0 for rank in song_100015.score_ranks)
            assert song_100015.score_ranks[-1].reward == "星钻 ×30"
            assert song_100015.score_ranks[-1].battle > song_100015.score_ranks[-1].required
            hardest = song_100015.difficulties[-1]
            assert [tier.label for tier in song_100015.combo_tiers] == [
                "25%", "50%", "75%", "全连",
            ]
            assert song_100015.combo_tiers[0].combo == math.ceil(hardest.notes * 25 / 100)
            assert song_100015.combo_tiers[-1].combo == hardest.notes == 1777
            assert song_100015.combo_tiers[-1].reward == "星钻 ×25"

            # Card and support lists follow release order, newest first — not
            # rarity, so 特殊 cards no longer jump to the top of the list.
            for entries in (dataset.cards, dataset.supports):
                dates = [entry.start_dt for entry in entries if entry.start_dt]
                assert dates == sorted(dates, reverse=True)

            # The tw prefix must reach the traditional dataset and keep the
            # prefix in its command hints.
            tw_context = next(
                context
                for template, context in captured
                if template == "stamp_list.html"
                and context.get("script") == "traditional"
            )
            assert tw_context["region_prefix"] == "tw "
            assert any("燈" in stamp.name for stamp in tw_context["resolved"].items)

            cn_stamp_context = next(
                context
                for template, context in captured
                if template == "stamp_list.html"
                and context.get("script") == "simplified"
            )
            assert all(
                "貼圖" not in stamp.name
                for stamp in cn_stamp_context["resolved"].items
            )

            # Raw-art commands bypass the renderer and send the asset URL.
            async def fake_fetch(url: str):
                return None

            plugin._fetch_image = fake_fetch  # type: ignore[assignment]

            first_card = dataset.cards[0]
            event = FakeEvent(f"card {first_card.id}")
            results = [result async for result in plugin.on_bdon_message(event)]
            assert results == [("image", first_card.art_url)], results
            assert event.stopped

            first_support = dataset.supports[0]
            event = FakeEvent(f"scard {first_support.id}")
            results = [result async for result in plugin.on_bdon_message(event)]
            assert results == [("image", first_support.art_url)], results
            assert event.stopped

            # A single sticker is sent as its source PNG; only multi-hit queries
            # render the sticker grid.
            first_stamp = dataset.stamps[0]
            event = FakeEvent(f"cn查贴纸 {first_stamp.id}")
            results = [result async for result in plugin.on_bdon_message(event)]
            assert results == [("image", first_stamp.image_url)], results
            assert event.stopped

            named_stamp = next(
                stamp for stamp in dataset.stamps if stamp.name == "灯的表情1"
            )
            event = FakeEvent("cn查贴纸 灯的表情1")
            results = [result async for result in plugin.on_bdon_message(event)]
            assert results == [("image", named_stamp.image_url)], results
            assert event.stopped

            # 查曲绘: an ID or a single hit sends the jacket image; the usage
            # hint and a miss are plain text, several hits render the list.
            jacket_song = dataset.songs[0]
            event = FakeEvent(f"查曲绘 {jacket_song.display_id}")
            results = [result async for result in plugin.on_bdon_message(event)]
            assert results == [
                ("image", jacket_song.jacket_url or jacket_song.jacket_fallback)
            ], results
            assert event.stopped

            event = FakeEvent(f"cn查曲绘 {jacket_song.id}")
            results = [result async for result in plugin.on_bdon_message(event)]
            assert results == [
                ("image", jacket_song.jacket_url or jacket_song.jacket_fallback)
            ], results
            assert event.stopped

            named_song = next(
                song
                for song in dataset.songs
                if song.title_for_search == "迷星叫"
            )
            event = FakeEvent(f"查曲绘 {named_song.title_for_search}")
            results = [result async for result in plugin.on_bdon_message(event)]
            assert results == [
                ("image", named_song.jacket_url or named_song.jacket_fallback)
            ], results
            assert event.stopped

            event = FakeEvent("查曲绘")
            results = [result async for result in plugin.on_bdon_message(event)]
            assert results[0][0] == "plain" and "用法" in results[0][1]

            event = FakeEvent("查曲绘 不存在的歌zzz")
            results = [result async for result in plugin.on_bdon_message(event)]
            assert results[0][0] == "plain" and "没有找到" in results[0][1]

            event = FakeEvent("查曲绘 999999")
            results = [result async for result in plugin.on_bdon_message(event)]
            assert results[0][0] == "plain" and "没有找到" in results[0][1]

            # The short in-game number shown in the UI also resolves.
            first_song = dataset.songs[0]
            event = FakeEvent(f"cn查歌 {first_song.display_id}")
            results = [result async for result in plugin.on_bdon_message(event)]
            assert results and results[-1][0] == "image", (text, results)
            assert captured[-1][1]["song"].id == first_song.id

            # Community aliases and near-miss spellings reach the search.
            from astrbot_plugin_bdon.bdon.search import resolve

            alias_cases = (
                ("灯", "card"),
                ("tomori", "card"),
                ("tmr", "card"),
                ("ana", "card"),
                ("rana", "card"),
                ("soyo", "card"),
            )
            for query, kind in alias_cases:
                result = resolve(dataset.items_for(kind), query, kind=kind)
                assert result.total > 0, (query, kind)

            for query, kind, expected in (
                ("好想成为人类", "support", "detail"),
                ("human", "support", "detail"),
            ):
                result = resolve(dataset.items_for(kind), query, kind=kind)
                assert result.mode == expected, (query, result.mode, result.total)

            sticker_hits = resolve(dataset.stamps, "灯", kind="stamp")
            assert sticker_hits.total == 2, sticker_hits.total
            assert all("灯" in stamp.name for stamp in sticker_hits.items)

            event_hits = resolve(dataset.events, "Shake", kind="event")
            assert event_hits.mode == "detail" and event_hits.item.id == 3
            assert resolve(dataset.events, "3", kind="event").item.id == 3

            # A comic detail query sends the original illustration, while the
            # bare list command still renders the grid.
            first_comic = dataset.comics[0]
            event = FakeEvent(f"cn查漫画 {first_comic.id}")
            results = [result async for result in plugin.on_bdon_message(event)]
            assert results == [("image", first_comic.image_url)], results
            assert event.stopped

            print("route tests passed:", len(captured))

        print("plugin tests passed")

    asyncio.run(run())


def test_builtin_renderer() -> None:
    from astrbot_plugin_bdon.bdon.render import AstrBotRenderService

    async def run() -> None:
        calls: list[tuple] = []

        async def fake_render(tmpl, data, return_url=True, options=None):
            calls.append((tmpl, data, return_url, options))
            return "fake-render.jpg"

        service = AstrBotRenderService(fake_render, timeout=120)
        result = await service.render(
            "<html><style>body{color:red}</style>{{title}}</html>",
            image_type="jpeg",
            quality=88,
        )
        assert result == "fake-render.jpg"
        tmpl, data, return_url, options = calls[0]
        assert return_url is False and data == {}
        assert options["type"] == "jpeg" and options["quality"] == 88
        # viewport_width/height are flat options; a nested "viewport" object is
        # silently ignored by the t2i service, which then pins the height to 720
        # and pads short pages with blank background.
        assert options["viewport_width"] == 1080
        assert options["viewport_height"] == 200
        assert "viewport" not in options
        assert options["full_page"] is True
        assert "body{color:red}" in tmpl
        assert "{{title}}" not in tmpl and "&#123;{title}}" in tmpl

        calls.clear()
        await service.render("<html></html>", scale="ultra")
        assert calls[0][3]["device_scale_factor_level"] == "ultra"

        # A service build without ultra support must fall back to high.
        attempts: list[str] = []

        async def flaky_render(tmpl, data, return_url=True, options=None):
            attempts.append(options["device_scale_factor_level"])
            if options["device_scale_factor_level"] == "ultra":
                raise RuntimeError("ultra unavailable")
            return "high.jpg"

        flaky_service = AstrBotRenderService(flaky_render)
        assert await flaky_service.render("<html></html>", scale="ultra") == "high.jpg"
        assert attempts == ["ultra", "high"], attempts

        async def broken_render(*args, **kwargs):
            raise RuntimeError("renderer down")

        class Fallback:
            async def render(
                self,
                html,
                width=1080,
                image_type="png",
                quality=90,
                scale="high",
            ):
                return "fallback.jpg"

        fallback_service = AstrBotRenderService(broken_render, fallback=Fallback())
        assert await fallback_service.render("<html></html>") == "fallback.jpg"

        missing_service = AstrBotRenderService(None)
        try:
            await missing_service.render("<html></html>")
        except Exception as exc:  # noqa: BLE001
            assert "html_render" in str(exc)
        else:
            raise AssertionError("missing built-in renderer should fail")

    asyncio.run(run())
    print("builtin renderer tests passed")


if __name__ == "__main__":
    test_patterns()
    test_builtin_renderer()
    test_icons()
    test_layout()
    test_plugin()
