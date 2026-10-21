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
    assert resolved and all(resolved.values()), resolved
    print("icon tests passed")


def test_layout() -> None:
    from astrbot_plugin_bdon.bdon.constants import list_columns

    assert list_columns("card", 70) == 5
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

            cases = [
                ("查歌 迷星叫", "song_detail.html", "song"),
                ("cn查歌 迷星叫", "song_detail.html", "song"),
                ("cn查歌 MyGO", "song_list.html", None),
                ("cn查卡 64", "card_detail.html", "card"),
                ("cn查卡 #64", "card_detail.html", "card"),
                ("cn查卡 ６４", "card_detail.html", "card"),
                ("hk查卡 64", "card_detail.html", "card"),
                ("cn查支援卡", "support_list.html", None),
                ("cn查活动", "event_detail.html", "event"),
                ("cn查活动 3", "event_detail.html", "event"),
                ("cn活动一览", "event_list.html", None),
                ("cn查贴纸", "stamp_list.html", None),
                ("tw查贴纸", "stamp_list.html", None),
                ("cn查漫画", "comic_list.html", None),
                ("cn查图鉴", "catalog.html", None),
                ("cn查图鉴 歌曲", "song_list.html", None),
                ("cn查图鉴 活动", "event_list.html", None),
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

            # Lists still fit in one image. Cards and songs only browse the
            # newest 20 without a keyword; every other table stays complete.
            for text, shown, total in (
                ("cn查歌", 20, 90),
                ("cn查卡", 20, 70),
                ("cn查支援卡", 73, 73),
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
