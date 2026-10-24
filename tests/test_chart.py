"""Parser and sheet tests for the ported moenotes-chart-renderer preview.

The timeline / geometry expectations mirror the upstream
moenotes-chart-renderer's own unit tests so the port stays drop-in compatible.
"""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bdon.chart import (  # noqa: E402
    OP_GUIDE_BEGIN_NORMAL,
    OP_HIDDEN,
    OP_SLIDE_BEGIN,
    OP_SLIDE_CONNECTION,
    OP_SLIDE_END,
    ChartError,
    bar_ticks,
    load_score,
    parse_score,
)
from bdon.chartsheet import (  # noqa: E402
    build_layout,
    build_sheet,
    note_value_label,
    rhythm_ticks,
    snap_note_value,
)

FIXTURES = ROOT / "tests" / ".data" / "charts"


def test_timeline_defaults_and_eases() -> None:
    score = parse_score(
        {
            "events": {},
            "notes": [
                {
                    "type": "long",
                    "node": [
                        {"t": 0, "pos": 0, "size": 6, "ease": ["out", "in"]},
                        {"t": 240, "pos": "auto"},
                        {"t": 480, "pos": 0, "size": 12},
                    ],
                }
            ],
        }
    )
    # Default timeline: 120 BPM at tick 0, so tick 480 is 500 ms in.
    assert score.position(480)[2] == 500
    auto = next(note for note in score.notes if note.pos_auto)
    # Out-eased left stays put; the in-eased right edge reaches 6 + 0.25 * 6.
    assert abs(auto.left - 0.0) < 1e-6
    assert abs(auto.width - 7.5) < 1e-6
    assert [note.op for note in score.notes] == [
        OP_SLIDE_BEGIN,
        OP_SLIDE_CONNECTION,
        OP_SLIDE_END,
    ]


def test_meter_changes_drive_bar_lines() -> None:
    score = parse_score(
        {
            "events": {
                "sig": [{"t": 0, "sig": [3, 4]}, {"t": 2880, "sig": [4, 4]}]
            },
            "notes": [{"t": 4800}],
        }
    )
    ticks = [tick for tick, _, _ in bar_ticks(score, 5)]
    assert ticks == [0, 1440, 2880, 4800, 6720]
    # 5/8 mid-chart keeps its own bar length; a note on that boundary starts bar 3.
    mixed = parse_score(
        {
            "events": {"sig": [{"t": 0, "sig": [3, 4]}, {"t": 1440, "sig": [5, 8]}]},
            "notes": [{"t": 2640}],
        }
    )
    bar, progress, _ = mixed.position(2640)
    assert (bar, round(progress, 6)) == (2, 0.0)


def test_hidden_anchors_and_guide_consumption() -> None:
    score = parse_score(
        {
            "events": {},
            "notes": [
                {
                    "type": "long",
                    "node": [
                        {"t": 0, "pos": 0, "size": 4},
                        {"t": 480, "pos": 0, "size": 4, "visible": False},
                        {"t": 480, "pos": 12, "size": 4, "visible": False},
                        {"t": 960, "pos": 12, "size": 4},
                    ],
                }
            ],
        }
    )
    assert [note.op for note in score.notes] == [
        OP_SLIDE_BEGIN,
        OP_HIDDEN,
        OP_HIDDEN,
        OP_SLIDE_END,
    ]
    visible = [note.role() for note in score.visible_notes()]
    assert visible == ["slide", "slide_end"]

    guide = parse_score(
        {
            "events": {},
            "notes": [
                {
                    "type": "guide",
                    "node": [{"t": 0, "pos": 0, "size": 0}, {"t": 1920, "pos": 0, "size": 8}],
                },
                {"t": 0, "pos": 0, "size": 0},
            ],
        }
    )
    head = guide.notes[0]
    assert head.op == OP_GUIDE_BEGIN_NORMAL
    assert guide.aliases == {2}  # the standalone tap was consumed


def test_events_parse() -> None:
    score = parse_score(
        {
            "events": {
                "bpm": [{"t": 0, "bpm": 190.0}, {"t": 3840, "bpm": 171.0}],
                "sig": [{"t": 0, "sig": [4, 4]}],
                "skill": [1920],
                "fever": [[1920, 5760], [960, 1440]],
                "call": [{"t": 0, "timing": [1, 0, 1, 1]}],
            },
            "notes": [],
        }
    )
    assert score.bpm_range() == (171.0, 190.0)
    assert score.skill == [1920]
    assert score.fever == [(960, 1440), (1920, 5760)]  # sorted by start
    assert score.calls == [(0, [0.25, 0.75, 1.0])]
    # Cumulative BPM clock: 4 beats at 190 then a switch to 171 exactly on bar 3.
    assert score.position(1920)[2] == 1263
    assert score.position(3840)[2] == 2526
    assert score.position(4320)[2] == 2526 + 480 / 480 * 60000 // 171


def test_real_chart_fixture() -> None:
    path = FIXTURES / "chart_0002_0002_03.json"
    if not path.is_file():
        return
    score = load_score(path.read_bytes())
    assert len(score.notes) == 750
    assert score.bar_count() == 77
    assert score.bpm_range() == (204.0, 204.0)
    roles = {note.role() for note in score.visible_notes()}
    assert {"tap", "slide", "slide_end", "flick"} <= roles
    assert score.pairs  # same-time judgement pairs were linked

    layout = build_layout(score, bar_ticks(score, score.bar_count() + 1))
    # Upstream geometry: ~24 beats per column, fixed track height, wide sheet.
    # The 68 px left gutter (bar number + note value) makes each column 380 px.
    assert len(layout.columns) == 13
    assert layout.width == 4952
    assert layout.height < 1800
    # Every visible note lands inside a column.
    for note in score.visible_notes():
        assert layout.owner(note.tick) is not None

    sheet = build_sheet(score)
    assert sheet["svg"].startswith("<svg")
    assert "polygon" in sheet["svg"]
    assert 'class="tick-num"' in sheet["svg"]
    assert 'class="tick-line"' in sheet["svg"]
    assert sheet["bars_count"] == 77
    assert sheet["svg_width"] == 4952
    assert sheet["page_width"] == 5026


def test_note_value_labels() -> None:
    score = parse_score(
        {
            "events": {"sig": [{"t": 0, "sig": [4, 4]}]},
            "notes": [
                {"t": 0},
                {"t": 0},     # chord member: one label for the onset
                {"t": 480},   # quarter note later
                {"t": 720},   # eighth note later
                {"t": 840},   # sixteenth note later
                {"t": 1920},  # next bar line
            ],
        }
    )
    assert rhythm_ticks(score) == [0, 480, 720, 840, 1920]
    assert note_value_label(score, 0, 480) == "/4"
    assert note_value_label(score, 480, 720) == "/8"
    assert note_value_label(score, 720, 840) == "/16"
    # 840 -> 1920 crosses the bar line by more than half a bar, so the label
    # stops at the bar end: 1080 ticks = 9/16 of a whole note.
    assert note_value_label(score, 840, 1920) == "9/16"
    # The last onset has no successor; its bar holds a whole note.
    assert note_value_label(score, 1920, None) == "/1"
    # Humanised odd ticks snap to standard note values.
    assert snap_note_value(79 / 1920) == Fraction(1, 24)
    assert snap_note_value(401 / 1920) == Fraction(5, 24)


def test_invalid_score_rejected() -> None:
    for payload in ('{"events":{},"notes":[{"t":0,"type":"mystery"}]}',
                    '{"events":{"bpm":[{"t":0,"bpm":"fast"}]},"notes":[]}',
                    "legacy sus"):
        try:
            load_score(payload)
        except ChartError:
            continue
        raise AssertionError(f"payload accepted: {payload[:40]}")


def test_layout_spreads_columns() -> None:
    if not (FIXTURES / "chart_0050_0050_03.json").is_file():
        return
    score = load_score((FIXTURES / "chart_0050_0050_03.json").read_bytes())
    layout = build_layout(score, bar_ticks(score, score.bar_count() + 1))
    assert len(layout.columns) >= 10  # the long chart spreads out, never paginates
    assert layout.width > 4000
    assert layout.height < 2000
    starts = [column.start for column in layout.columns]
    assert starts == sorted(starts)
    for column in layout.columns:
        assert column.end > column.start


def test_dense_chart_raises_spacing_instead_of_truncating() -> None:
    if not (FIXTURES / "chart_0114_0114_03.json").is_file():
        return
    score = load_score((FIXTURES / "chart_0114_0114_03.json").read_bytes())
    bars = bar_ticks(score, score.position(score.max_tick())[0] + 2)
    layout = build_layout(score, bars)
    # Overlapping-lane notes force a higher px/beat, exactly like upstream;
    # the sheet gets taller for this chart but never drops notes or pages.
    assert layout.ppb > 64
    assert len(layout.columns) == 8
    assert layout.width == 3052
    assert layout.height < 4100


if __name__ == "__main__":
    for name, function in sorted(list(globals().items())):
        if name.startswith("test_") and callable(function):
            function()
            print(f"{name} passed")
    print("all chart tests passed")
