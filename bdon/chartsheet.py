"""Builds the 2D chart-sheet SVG for the 查谱面 preview.

Geometry is a direct port of luoxiadesu/moenotes-chart-renderer's ``layout.rs``
(MIT, https://github.com/luoxiadesu/moenotes-chart-renderer): 64 px per beat,
10 px per lane, one column per ~24 beats, and every column shares the
tallest column's track height. Time flows UP inside a column; columns run left
to right, so reading starts at the bottom-left and ends at the top-right. The
sheet's WIDTH grows with the number of columns while its HEIGHT stays fixed,
which is the original's pagination-free multi-column layout.

The left gutter carries two columns of text: the bar number and, closer to the
lanes, the note value to the next rhythm event (``/4`` = quarter note, ``/8`` =
eighth, ``/16`` = sixteenth, ...).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from .chart import (
    ALPHA_FADE_IN,
    ALPHA_FADE_OUT,
    OP_FLICK,
    OP_NORMAL,
    OP_SLIDE_BEGIN,
    OP_SLIDE_BEGIN_FLICK,
    OP_SLIDE_BEGIN_TRACE,
    OP_SLIDE_END,
    OP_SLIDE_END_FLICK,
    OP_SLIDE_END_TRACE,
    ChartError,
    Note,
    Score,
    bar_ticks,
    eased,
)

LANE_COUNT = 24
PPQ = 480

# -- upstream layout.rs constants ---------------------------------------------
# Wider than upstream's 28 px to fit two sub-columns: note values next to the
# lanes and bar numbers 33 px further out, so the two never overlap.
LEFT_GUTTER = 68.0
RIGHT_GUTTER = 60.0
COLUMN_GAP = 12.0
LANE_PX = 10.0
TRACK_WIDTH = LANE_COUNT * LANE_PX
COLUMN_WIDTH = LEFT_GUTTER + TRACK_WIDTH + RIGHT_GUTTER + COLUMN_GAP
BASE_PPB = 64.0
MAX_PPB = 160.0
TARGET_BEATS = 24.0
NOTE_HEIGHT = 8.0
ARROW_HEIGHT = 10.0
PAD = 26.0

# The SVG carries only the column band and its captions; the page chrome
# (header card, stats, legend, footer) lives in the HTML template. The top pad
# leaves room for the caption above the track and for packed rail labels that
# upstream allows to rise 12 px above it.
TOP_PAD = 54.0
BOTTOM_PAD = 38.0
# .page horizontal padding (20 + 20) + chart-panel padding/border (16 + 16 + 2).
PAGE_CHROME = 74.0
MIN_PAGE_WIDTH = 1080

# Print-theme palette from the upstream renderer, slightly deepened for chat.
ROLE_COLORS = {
    "tap": "#166481",
    "slide": "#3E53A8",
    "slide_end": "#3E53A8",
    "connection": "#3E53A8",
    "flick": "#9B5E08",
    "flick_left": "#157650",
    "flick_right": "#AC345B",
    "trace": "#6F439E",
    "guide": "#705399",
}
CRITICAL_COLOR = "#814E09"
EVENT_COLORS = {
    "bpm": "#8E510F",
    "meter": "#574890",
    "skill": "#9B2F66",
    "call": "#0F6E5B",
    "fade": "#0F6E5B",
}
FEVER_RAIL = "#A88F5A"
GRID = {
    "band": "#F8F9FB",
    "lane": "#CFD6DF",
    "lane_fine": "#EEF0F4",
    "bar": "#8594A6",
    "beat": "#D9DFE7",
    "half": "#EDF0F4",
    "text": "#1E2937",
    "muted": "#505C6A",
}
BODY_SCALE = {"trace": 0.72, "connection": 0.53}

# Left-gutter rail: note values hug the lanes, bar numbers sit BAR_X further
# out. MIN_TICK_GAP drops a label that would collide with the previous one on
# very dense charts; the approved samples never hit it.
TICK_X = 5.0
BAR_X = 38.0
TICK_LINE_LENGTH = 24.0
MIN_TICK_GAP = 10.0

# Playable onsets: tap/flick and slide endpoints (trace slides included).
# Guides, hidden slides and connections are auxiliary, not rhythm events.
TICK_OPS = frozenset({
    OP_NORMAL, OP_SLIDE_BEGIN, OP_SLIDE_END, OP_FLICK,
    OP_SLIDE_BEGIN_FLICK, OP_SLIDE_END_FLICK,
    OP_SLIDE_BEGIN_TRACE, OP_SLIDE_END_TRACE,
})

# Standard note-value denominators (binary, dotted, triplets, quintuplets,
# septuplets). BDON charts carry humanised odd-tick gaps, so a raw fraction
# would print nonsense such as 79/1920; snap to the nearest value here instead.
NOTE_DENOMINATORS = (
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
    12, 14, 16, 18, 20, 24, 28, 32, 36, 40, 48, 56, 64, 72, 80, 96,
)


@dataclass
class SheetOptions:
    page_chrome: float = PAGE_CHROME
    min_page_width: int = MIN_PAGE_WIDTH


@dataclass
class Column:
    index: int
    start: int
    end: int
    first_bar: int
    last_bar: int
    x: float = 0.0


@dataclass
class SheetLayout:
    bars: list[tuple[int, int, int]]           # (tick, number, time_ms)
    columns: list[Column]
    ppb: float

    lane_px: float = LANE_PX
    note_height: float = NOTE_HEIGHT
    arrow_height: float = ARROW_HEIGHT
    track_height: float = 0.0

    @property
    def track_width(self) -> float:
        return LANE_COUNT * self.lane_px

    @property
    def width(self) -> float:
        return len(self.columns) * COLUMN_WIDTH + COLUMN_GAP

    @property
    def height(self) -> float:
        return TOP_PAD + self.track_height + BOTTOM_PAD

    def column_x(self, index: int) -> float:
        return COLUMN_GAP + index * COLUMN_WIDTH + LEFT_GUTTER

    def y(self, tick: float, column: Column) -> float:
        """Time flows up: the column's first tick sits at the bottom."""
        return (TOP_PAD + self.track_height
                - (tick - column.start) / PPQ * self.ppb)

    def owner(self, tick: int) -> int | None:
        for column in self.columns:
            if column.start <= tick < column.end:
                return column.index
        return None


@dataclass
class Annotation:
    tick: int
    label: str
    kind: str


def _fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _fmt_bpm(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text


def snap_note_value(value: float) -> Fraction:
    """Nearest standard note value (fraction of a whole note) to ``value``."""
    best = Fraction(0, 1)
    best_error = math.inf
    for denominator in NOTE_DENOMINATORS:
        numerator = round(value * denominator)
        if numerator <= 0:
            continue
        candidate = Fraction(numerator, denominator)
        error = abs(float(candidate) - value)
        if error < best_error - 1e-12:
            best, best_error = candidate, error
        elif abs(error - best_error) <= 1e-12 and candidate.denominator < best.denominator:
            best = candidate
    return best


def rhythm_ticks(score: Score) -> list[int]:
    """Sorted ticks of the playable onsets that get a note-value label."""
    seen: set[int] = set()
    ticks: list[int] = []
    for note in score.notes:
        if note.id in score.aliases or not note.visible or note.width <= 0:
            continue
        if note.op not in TICK_OPS or note.tick in seen:
            continue
        seen.add(note.tick)
        ticks.append(note.tick)
    ticks.sort()
    return ticks


def note_value_label(score: Score, tick: int, next_tick: int | None) -> str:
    """Note value from ``tick`` to the next rhythm event or the bar end.

    A gap longer than half a bar that crosses a bar line is cut at the end of
    the current bar, so long rests show the note's remaining value instead of
    the full gap.
    """
    sig_tick, _, _, length, _ = score.sig_at(tick)
    bar_start = sig_tick + ((tick - sig_tick) // length) * length
    bar_end = bar_start + length
    gap = None if next_tick is None else next_tick - tick
    if (
        gap is None
        or gap > length
        or (gap > length / 2 and next_tick is not None and next_tick >= bar_end)
    ):
        interval = bar_end - tick
    else:
        interval = gap
    if interval <= 0:
        return ""
    value = snap_note_value(interval / (4 * PPQ))
    numerator, denominator = value.numerator, value.denominator
    return f"{numerator}/{denominator}" if numerator != 1 else f"/{denominator}"


def _auto_spacing(visible: list[Note]) -> float:
    """Upstream Layout auto_spacing: raise px/beat until overlapping-lane notes
    clear each other, then cap at 160 px/beat."""
    spacing = BASE_PPB
    for i, a in enumerate(visible):
        for b in visible[i + 1:]:
            delta = b.tick - a.tick
            if delta > PPQ:
                break
            if delta <= 0 or not (a.left < b.right and b.left < a.right):
                continue
            ar, br = a.role() or "", b.role() or ""
            # Structural slide anchors may deliberately sit 1-2 ticks apart.
            if ar == "connection" and br == "connection" and delta <= 2:
                continue
            gap = NOTE_HEIGHT + 3.0
            if ar.startswith("flick"):
                center = (a.left + a.right) / 2
                half = min(a.width * LANE_PX, 24.0) / LANE_PX / 2
                if b.left < center + half and b.right > center - half:
                    gap += ARROW_HEIGHT + 1.5
            spacing = max(spacing, gap * PPQ / delta)
    return min(spacing, MAX_PPB)


def _balanced_cuts(bars: Sequence[tuple[int, int, int]], target: float) -> list[int]:
    """Port of upstream ``balanced_cuts``: DP over bar boundaries minimising
    per-column height variance for ``ceil(total / target)`` columns."""
    count = len(bars) - 1
    total = bars[count][0] - bars[0][0]
    columns = max(1, min(count, math.ceil(total / target)))
    columns = max(columns, math.ceil(count / 128))
    if columns * count > 1_000_000:
        cuts = [0]
        last = 0
        for part in range(1, columns):
            goal = total * part / columns
            lo = last + 1
            hi = count - (columns - part)
            end = min(range(lo, hi + 1), key=lambda a: abs(bars[a][0] - bars[0][0] - goal))
            cuts.append(end)
            last = end
        cuts.append(count)
        return cuts

    ideal = total / columns
    dp = [[math.inf] * (count + 1) for _ in range(columns + 1)]
    prev = [[0] * (count + 1) for _ in range(columns + 1)]
    dp[0][0] = 0.0
    for c in range(1, columns + 1):
        for end in range(c, count - (columns - c) + 1):
            for start in range(max(0, end - 128), end):
                if not math.isfinite(dp[c - 1][start]):
                    continue
                span = bars[end][0] - bars[start][0]
                cost = dp[c - 1][start] + (span - ideal) ** 2
                if cost < dp[c][end]:
                    dp[c][end] = cost
                    prev[c][end] = start
    cuts = [count]
    end = count
    for c in range(columns, 0, -1):
        end = prev[c][end]
        cuts.append(end)
    cuts.reverse()
    return cuts


def build_layout(score: Score, bars: list[tuple[int, int, int]]) -> SheetLayout:
    """Density raises px/beat; the column count follows the chart length."""
    visible = [n for n in score.notes
               if n.id not in score.aliases and n.role() and n.width > 0]
    visible.sort(key=lambda n: n.tick)
    ppb = _auto_spacing(visible)
    cuts = _balanced_cuts(bars, TARGET_BEATS * PPQ)
    columns = [
        Column(index=i, start=bars[a][0], end=bars[b][0],
               first_bar=bars[a][1], last_bar=bars[b - 1][1])
        for i, (a, b) in enumerate(itertools.pairwise(cuts))
    ]
    span = max((column.end - column.start) for column in columns)
    return SheetLayout(
        bars=bars,
        columns=columns,
        ppb=ppb,
        track_height=span / PPQ * ppb,
    )


# -- ribbon sampling -----------------------------------------------------------


def _sample_row(a: Note, b: Note, tick: int) -> tuple[int, float, float]:
    t = (tick - a.tick) / (b.tick - a.tick) if b.tick > a.tick else 0.0
    left = a.source_left + eased(t, a.ease_left) * (b.source_left - a.source_left)
    a_right = a.source_right
    b_right = b.source_right
    right = a_right + eased(t, a.ease_right) * (b_right - a_right)
    return tick, left, right


def _subdivide(a: Note, b: Note, first: tuple[int, float, float],
               second: tuple[int, float, float], lane_px: float, ppb: float,
               out: list[tuple[int, float, float]]) -> None:
    if second[0] - first[0] > 1:
        mid_tick = first[0] + (second[0] - first[0]) // 2
        mid = _sample_row(a, b, mid_tick)
        t = (mid_tick - first[0]) / (second[0] - first[0])
        error = max(
            abs(mid[1] - (first[1] + (second[1] - first[1]) * t)),
            abs(mid[2] - (first[2] + (second[2] - first[2]) * t)),
        ) * lane_px
        height = (second[0] - first[0]) / PPQ * ppb
        if error > 0.2 or height > 10.0:
            _subdivide(a, b, first, mid, lane_px, ppb, out)
            _subdivide(a, b, mid, second, lane_px, ppb, out)
            return
    out.append(second)


def line_segments(score: Score, layout: SheetLayout,
                  members: list[Note]) -> list[list[tuple[int, float, float]]]:
    """Sampled [left, right] rows per anchor pair, cut at column/BPM/meter bounds."""
    anchors = [m for m in members if not m.slide_along]
    segments: list[list[tuple[int, float, float]]] = []
    bounds = {column.start for column in layout.columns}
    bounds |= {column.end for column in layout.columns}
    bounds |= {tick for tick, _ in score.bpms}
    bounds |= {tick for tick, *_ in score.sigs}
    for a, b in itertools.pairwise(anchors):
        if b.tick < a.tick:
            raise ChartError("non-monotonic line anchors")
        if a.tick == b.tick:
            segments.append([(a.tick, a.source_left, a.source_right),
                             (b.tick, b.source_left, b.source_right)])
            continue
        cuts = sorted({a.tick, b.tick} | {t for t in bounds if a.tick < t < b.tick})
        segment = [_sample_row(a, b, cuts[0])]
        for tick in cuts[1:]:
            _subdivide(a, b, segment[-1], _sample_row(a, b, tick),
                       layout.lane_px, layout.ppb, segment)
        segments.append(segment)
    return segments


# -- SVG emission --------------------------------------------------------------


def _f(value: float) -> str:
    text = f"{value:.1f}"
    return text.removesuffix(".0")


def _text_units(text: str) -> float:
    return sum(0.6 if ord(char) < 0x2E80 else 1.0 for char in text) or 1.0


def _fit_label_size(text: str, width: float = RIGHT_GUTTER - 8.0,
                    preferred: float = 11.5, minimum: float = 8.0) -> float:
    return max(minimum, min(preferred, width / _text_units(text)))


def _pack_labels(desired: list[float], top: float, bottom: float,
                 gap: float) -> tuple[list[float], int]:
    """Upstream pack_labels: stable monotone packing with overflow count."""
    capacity = max(0, int((bottom - top) // gap))
    count = min(len(desired), capacity)
    ys: list[float] = []
    for value in desired[:count]:
        ys.append(max(value, ys[-1] + gap if ys else top))
    if ys and ys[-1] > bottom:
        following = bottom + gap
        for index in range(len(ys) - 1, -1, -1):
            ys[index] = min(ys[index], following - gap)
            following = ys[index]
    return ys, len(desired) - count


def _display_events(score: Score, layout: SheetLayout,
                    column: Column) -> list[Annotation]:
    """Upstream display_events: collapse dense tempo ramps into a range label."""
    entries: list[Annotation] = []
    for tick, value in score.bpms:
        if column.start <= tick < column.end:
            entries.append(Annotation(tick, _fmt_bpm(value), "bpm"))
    for tick, num, den, _, _ in score.sigs:
        if column.start <= tick < column.end:
            entries.append(Annotation(tick, f"{num}/{den}", "meter"))
    for tick in score.skill:
        if column.start <= tick < column.end:
            entries.append(Annotation(tick, "SKILL", "skill"))
    for tick, rhythms in score.calls:
        if column.start <= tick < column.end:
            label = "CALL " + "/".join(f"{value * 100:.0f}%" for value in rhythms)
            entries.append(Annotation(tick, label, "call"))
    for note in score.notes:
        if note.alpha not in (ALPHA_FADE_IN, ALPHA_FADE_OUT):
            continue
        if column.start <= note.tick < column.end:
            label = "FADE IN" if note.alpha == ALPHA_FADE_IN else "FADE OUT"
            entries.append(Annotation(note.tick, label, "fade"))
    entries.sort(key=lambda a: a.tick)

    bpms = [a for a in entries if a.kind == "bpm"]
    shown = [a for a in entries if a.kind != "bpm"]
    index = 0
    while index < len(bpms):
        end = index + 1
        while (end < len(bpms)
               and (bpms[end].tick - bpms[end - 1].tick) / PPQ * layout.ppb < 18.0):
            end += 1
        if end - index >= 4:
            shown.append(Annotation(
                bpms[index].tick,
                f"{bpms[index].label}→{bpms[end - 1].label}",
                "bpm",
            ))
        else:
            shown.extend(bpms[index:end])
        index = end
    shown.sort(key=lambda a: -a.tick)
    return shown


class SheetSvg:
    """Emits the chart area as one standalone <svg> document."""

    def __init__(self, score: Score, layout: SheetLayout) -> None:
        self.score = score
        self.layout = layout
        self.ticks = rhythm_ticks(score)
        self.parts: list[str] = []
        self.width = layout.width
        self.height = layout.height

    def build(self) -> str:
        self.parts.append(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{_f(self.width)}" '
            f'height="{_f(self.height)}" viewBox="0 0 {_f(self.width)} {_f(self.height)}" '
            'class="chart-svg">'
        )
        for column in self.layout.columns:
            self._column(column)
        self.parts.append("</svg>")
        return "".join(self.parts)

    def _column(self, column: Column) -> None:
        layout = self.layout
        column.x = layout.column_x(column.index)
        x = column.x
        track = layout.track_width
        top = TOP_PAD
        bottom = top + layout.track_height
        p = self.parts.append
        p(f'<rect x="{_f(x)}" y="{_f(top)}" width="{_f(track)}" '
          f'height="{_f(layout.track_height)}" fill="#ffffff"/>')
        # Alternating broad-lane bands; fine 24-lane divisions stay quiet.
        for band in (0, 2):
            p(f'<rect x="{_f(x + band * 6 * layout.lane_px)}" y="{_f(top)}" '
              f'width="{_f(6 * layout.lane_px)}" height="{_f(layout.track_height)}" '
              f'fill="{GRID["band"]}"/>')
        # Fever spans before the grid so lines stay crisp.
        for start, end in self.score.fever:
            lo, hi = max(start, column.start), min(end, column.end)
            if lo >= hi:
                continue
            y0, y1 = layout.y(hi, column), layout.y(lo, column)
            p(f'<rect x="{_f(x)}" y="{_f(y0)}" width="{_f(track)}" height="{_f(y1 - y0)}" '
              f'fill="#CA9B3C" fill-opacity="0.047"/>')
            p(f'<line x1="{_f(x - 3)}" y1="{_f(y0)}" x2="{_f(x - 3)}" y2="{_f(y1)}" '
              f'stroke="{FEVER_RAIL}" stroke-width="2"/>')
        # Lane lines.
        for lane in range(LANE_COUNT + 1):
            bold = lane % 6 == 0
            p(f'<line x1="{_f(x + lane * layout.lane_px)}" y1="{_f(top)}" '
              f'x2="{_f(x + lane * layout.lane_px)}" y2="{_f(bottom)}" '
              f'stroke="{GRID["lane"] if bold else GRID["lane_fine"]}" '
              f'stroke-width="{0.7 if bold else 0.35}"/>')
        # Bars, beats and bar numbers (numbers rise with the time axis).
        for tick, number, _ in layout.bars:
            if not (column.start <= tick <= column.end):
                continue
            y = layout.y(tick, column)
            major = number == column.first_bar or number % 4 == 1
            p(f'<line x1="{_f(x)}" y1="{_f(y)}" x2="{_f(x + track)}" y2="{_f(y)}" '
              f'stroke="{GRID["bar"]}" stroke-width="{1.15 if major else 0.8}"/>')
            cls = "bar-num major" if major else "bar-num"
            p(f'<text x="{_f(x - BAR_X)}" y="{_f(y + 4)}" text-anchor="end" '
              f'class="{cls}">{number:03d}</text>')
        for (t0, _, _), (t1, _, _) in itertools.pairwise(layout.bars):
            tick = t0 + PPQ // 2
            while tick < t1:
                if column.start <= tick < column.end:
                    y = layout.y(tick, column)
                    beat = (tick - t0) % PPQ == 0
                    p(f'<line x1="{_f(x)}" y1="{_f(y)}" x2="{_f(x + track)}" y2="{_f(y)}" '
                      f'stroke="{GRID["beat"] if beat else GRID["half"]}" stroke-width="0.5"/>')
                tick += PPQ // 2

        self._tick_labels(column)

        clip_id = f"chart-clip-{column.index}"
        p(f'<clipPath id="{clip_id}"><rect x="{_f(x)}" y="{_f(top - PAD)}" '
          f'width="{_f(track)}" height="{_f(layout.track_height + 2 * PAD)}"/></clipPath>')
        p(f'<g clip-path="url(#{clip_id})">')
        # Ribbons: guides first so slides sit on top.
        for guide in (True, False):
            for line in self.score.lines:
                if line.guide != guide:
                    continue
                self._ribbon(column, line.members(self.score.notes), guide)
        self._pairs(column)
        self._glyphs(column)
        p("</g>")

        self._annotations(column)
        self._captions(column)

    def _tick_labels(self, column: Column) -> None:
        """Note values in the left gutter, one per playable onset."""
        layout = self.layout
        last_y = math.inf
        for index, tick in enumerate(self.ticks):
            if layout.owner(tick) != column.index:
                continue
            next_tick = self.ticks[index + 1] if index + 1 < len(self.ticks) else None
            label = note_value_label(self.score, tick, next_tick)
            if not label:
                continue
            y = layout.y(tick, column)
            if last_y - y < MIN_TICK_GAP:
                continue
            last_y = y
            x = column.x
            self.parts.append(
                f'<line x1="{_f(x - TICK_LINE_LENGTH)}" y1="{_f(y)}" '
                f'x2="{_f(x - 2)}" y2="{_f(y)}" class="tick-line"/>')
            self.parts.append(
                f'<text x="{_f(x - TICK_X)}" y="{_f(y - 2)}" '
                f'text-anchor="end" class="tick-num">{label}</text>')

    def _ribbon(self, column: Column, members: list[Note], guide: bool) -> None:
        layout = self.layout
        color = ROLE_COLORS["guide"] if guide else ROLE_COLORS["slide"]
        for segment in line_segments(self.score, layout, members):
            rows = [r for r in segment if column.start <= r[0] <= column.end]
            if len(rows) < 2:
                continue
            if rows[0][0] == rows[-1][0]:
                y = layout.y(rows[0][0], column)
                x0 = column.x + rows[0][1] * layout.lane_px
                x1 = column.x + rows[-1][2] * layout.lane_px
                self.parts.append(
                    f'<line x1="{_f(x0)}" y1="{_f(y)}" x2="{_f(x1)}" y2="{_f(y)}" '
                    f'stroke="{color}" stroke-width="1"/>')
                continue
            inset = 0.0 if guide else 0.3
            lefts = [(tick, min(left + inset, (left + right) / 2))
                     for tick, left, right in rows]
            rights = [(tick, max(right - inset, (left + right) / 2))
                      for tick, left, right in rows]
            points: list[str] = []
            for tick, left in lefts:
                points.append(f'{_f(column.x + left * layout.lane_px)},{_f(layout.y(tick, column))}')
            for tick, right in reversed(rights):
                points.append(f'{_f(column.x + right * layout.lane_px)},{_f(layout.y(tick, column))}')
            dash = ' stroke-dasharray="3 3"' if guide else ""
            self.parts.append(
                f'<polygon points="{" ".join(points)}" fill="{color}" '
                f'fill-opacity="{0.07 if guide else 0.14}" stroke="none"/>')
            left_pts = " ".join(
                f'{_f(column.x + left * layout.lane_px)},{_f(layout.y(tick, column))}'
                for tick, left in lefts)
            right_pts = " ".join(
                f'{_f(column.x + right * layout.lane_px)},{_f(layout.y(tick, column))}'
                for tick, right in rights)
            for pts in (left_pts, right_pts):
                self.parts.append(
                    f'<polyline points="{pts}" fill="none" stroke="{color}" '
                    f'stroke-width="{0.7 if guide else 1}" '
                    f'stroke-opacity="{0.5 if guide else 0.8}"{dash}/>')

    def _pairs(self, column: Column) -> None:
        layout = self.layout
        notes = {note.id: note for note in self.score.notes}
        for id_a, id_b in self.score.pairs:
            a, b = notes[id_a], notes[id_b]
            if layout.owner(a.tick) != column.index:
                continue
            y = layout.y(a.tick, column)
            ax = column.x + (a.left + a.right) / 2 * layout.lane_px
            bx = column.x + (b.left + b.right) / 2 * layout.lane_px
            self.parts.append(
                f'<line x1="{_f(ax)}" y1="{_f(y)}" x2="{_f(bx)}" y2="{_f(y)}" '
                f'stroke="#62708A" stroke-opacity="0.55" stroke-width="0.8"/>')

    def _glyphs(self, column: Column) -> None:
        layout = self.layout
        notes = [n for n in self.score.visible_notes()
                 if layout.owner(n.tick) == column.index]
        # Bodies first, then arrows, then critical marks: same layering as upstream.
        for note in notes:
            self._note_body(note, column)
        for note in notes:
            if (note.role() or "").startswith("flick"):
                self._note_arrow(note, column)
        for note in notes:
            if note.critical:
                self._critical_mark(note, column)

    def _note_body(self, note: Note, column: Column) -> None:
        layout = self.layout
        role = note.role() or ""
        color = ROLE_COLORS.get(role, ROLE_COLORS["tap"])
        height = layout.note_height * BODY_SCALE.get(role, 1.0)
        width = note.width * layout.lane_px
        y = layout.y(note.tick, column)
        x = column.x + note.left * layout.lane_px + 0.5
        rect_width = max(width - 1.0, 0.5)
        self.parts.append(
            f'<rect x="{_f(x)}" y="{_f(y - height / 2)}" width="{_f(rect_width)}" '
            f'height="{_f(height)}" rx="1" fill="#ffffff"/>')
        self.parts.append(
            f'<rect x="{_f(x)}" y="{_f(y - height / 2)}" width="{_f(rect_width)}" '
            f'height="{_f(height)}" rx="1" fill="{color}" '
            f'fill-opacity="{0.12 if role == "connection" else 0.22}" '
            f'stroke="{color}" stroke-width="1.1"/>')
        if role == "trace":
            self.parts.append(
                f'<line x1="{_f(x + 2)}" y1="{_f(y)}" x2="{_f(x + rect_width - 2)}" y2="{_f(y)}" '
                f'stroke="{color}" stroke-width="0.8"/>')
        elif role != "connection":
            self.parts.append(
                f'<line x1="{_f(x + 1)}" y1="{_f(y - height / 2 + 1)}" '
                f'x2="{_f(x + rect_width - 1)}" y2="{_f(y - height / 2 + 1)}" '
                f'stroke="{color}" stroke-width="1.4"/>')

    def _note_arrow(self, note: Note, column: Column) -> None:
        layout = self.layout
        role = note.role() or "flick"
        color = ROLE_COLORS.get(role, ROLE_COLORS["flick"])
        height = layout.note_height * BODY_SCALE.get(role, 1.0)
        y = layout.y(note.tick, column)
        center = column.x + (note.left + note.right) / 2 * layout.lane_px
        width = note.width * layout.lane_px
        arrow_h = layout.arrow_height / 2 - 0.8
        # Arrows sit on the later-time side of the body; with time flowing up
        # that is above it on screen, matching the upstream sheet.
        cy = y - height / 2 - 1.5 - layout.arrow_height / 2
        w = min(max(width - 2, 2), 22)
        path: list[str] = []
        if role == "flick":
            path.append(f'M {_f(center - w / 2)},{_f(cy + arrow_h)} '
                        f'L {_f(center)},{_f(cy - arrow_h)} '
                        f'L {_f(center + w / 2)},{_f(cy + arrow_h)}')
        else:
            sign = -1 if role == "flick_left" else 1
            for offset in (-w * 0.24, w * 0.24):
                path.append(f'M {_f(center + offset - sign * w * 0.16)},{_f(cy - arrow_h)} '
                            f'L {_f(center + offset + sign * w * 0.16)},{_f(cy)} '
                            f'L {_f(center + offset - sign * w * 0.16)},{_f(cy + arrow_h)}')
        self.parts.append(
            f'<path d="{" ".join(path)}" fill="none" stroke="{color}" '
            f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>')

    def _critical_mark(self, note: Note, column: Column) -> None:
        layout = self.layout
        y = layout.y(note.tick, column)
        center = column.x + (note.left + note.right) / 2 * layout.lane_px
        r = 2.6
        points = (f'{_f(center)},{_f(y - r)} {_f(center + r)},{_f(y)} '
                  f'{_f(center)},{_f(y + r)} {_f(center - r)},{_f(y)}')
        self.parts.append(
            f'<polygon points="{points}" fill="{CRITICAL_COLOR}" stroke="#ffffff" '
            f'stroke-width="1.4"/>')

    def _annotations(self, column: Column) -> None:
        layout = self.layout
        entries = _display_events(self.score, layout, column)
        if not entries:
            return
        top = TOP_PAD
        bottom = TOP_PAD + layout.track_height
        desired = [layout.y(annotation.tick, column) + 3 for annotation in entries]
        # Keep the packed rail above the time labels at the column's bottom edge.
        ys, dropped = _pack_labels(desired, top - 12, bottom - 4, 15)
        event_x = column.x + layout.track_width
        label_x = event_x + 6
        for annotation, y in zip(entries, ys):
            color = EVENT_COLORS[annotation.kind]
            anchor = layout.y(annotation.tick, column)
            self.parts.append(
                f'<line x1="{_f(event_x)}" y1="{_f(anchor)}" '
                f'x2="{_f(event_x + 4)}" y2="{_f(y - 3)}" stroke="{color}" '
                f'stroke-width="0.6" stroke-opacity="0.8"/>')
            size = _fit_label_size(annotation.label)
            self.parts.append(
                f'<text x="{_f(label_x)}" y="{_f(y)}" class="anno" fill="{color}" '
                f'font-size="{_f(size)}">{annotation.label}</text>')
        if dropped:
            self.parts.append(
                f'<text x="{_f(event_x + 4)}" y="{_f(bottom + 31)}" class="anno-note" '
                f'fill="{GRID["muted"]}">↳ NOTES</text>')

    def _captions(self, column: Column) -> None:
        layout = self.layout
        top = TOP_PAD
        bottom = top + layout.track_height
        x = column.x
        start_time = self.score.position(column.start)[2] / 1000
        end_time = self.score.position(column.end)[2] / 1000
        self.parts.append(
            f'<text x="{_f(x)}" y="{_f(top - 34)}" class="col-label">'
            f'{column.first_bar:03d}—{column.last_bar:03d}</text>')
        self.parts.append(
            f'<text x="{_f(x)}" y="{_f(bottom + 20)}" class="time-label">'
            f'{_fmt_time(start_time)} ↑</text>')
        self.parts.append(
            f'<text x="{_f(x + layout.track_width)}" y="{_f(bottom + 20)}" '
            f'text-anchor="end" class="time-label">→ {_fmt_time(end_time)}</text>')


def build_sheet(score: Score, options: SheetOptions | None = None) -> dict:
    """Parses geometry and emits the SVG plus header stats for the template."""
    options = options or SheetOptions()
    # Upstream builds bar lines until the first one past max_tick, so events
    # after the last note still get their own bar.
    bars = bar_ticks(score, score.position(score.max_tick())[0] + 2)
    layout = build_layout(score, bars)
    sheet = SheetSvg(score, layout)
    svg = sheet.build()

    low, high = score.bpm_range()
    bpm_label = (f"{low:.0f}" if abs(high - low) < 0.01 else f"{low:.0f}–{high:.0f}") + " BPM"
    last_time = score.position(score.max_tick())[2] / 1000
    return {
        "svg": svg,
        "svg_width": round(sheet.width, 1),
        "svg_height": round(sheet.height, 1),
        "page_width": max(options.min_page_width,
                          math.ceil(sheet.width + options.page_chrome)),
        "bpm_label": bpm_label,
        "bars_count": len(bars) - 1,
        "duration_label": _fmt_time(last_time),
        "note_count": len(score.visible_notes()),
        "fever_count": len(score.fever),
        "columns": len(layout.columns),
        "lane_px": round(layout.lane_px, 1),
        "ppb": round(layout.ppb, 1),
        "legend": legend_samples(),
    }


def _mini_note(role: str, critical: bool = False) -> str:
    """A small legend glyph in its own coordinate system (24x22 viewbox)."""
    color = ROLE_COLORS.get(role, ROLE_COLORS["tap"])
    height = 9 * BODY_SCALE.get(role, 1.0)
    parts: list[str] = []
    parts.append(
        f'<rect x="2" y="{_f(11 - height / 2)}" width="20" height="{_f(height)}" rx="2" '
        f'fill="{color}" fill-opacity="0.16" stroke="{color}" stroke-width="1.4"/>')
    if role == "trace":
        parts.append(f'<line x1="4" y1="11" x2="20" y2="11" stroke="{color}" stroke-width="1.1"/>')
    elif role != "connection":
        parts.append(
            f'<line x1="4" y1="{_f(11 - height / 2 + 1.6)}" x2="20" y2="{_f(11 - height / 2 + 1.6)}" '
            f'stroke="{color}" stroke-width="1.8"/>')
    if role.startswith("flick"):
        cy = 11 - height / 2 - 2 - 3
        if role == "flick":
            parts.append(f'<path d="M 7,{_f(cy + 2.6)} L 12,{_f(cy - 2.6)} L 17,{_f(cy + 2.6)}" '
                         f'fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round"/>')
        else:
            sign = -1 if role == "flick_left" else 1
            for offset in (9.4, 14.6):
                parts.append(
                    f'<path d="M {_f(offset - sign * 2.3)},{_f(cy - 2.4)} L {_f(offset + sign * 2.3)},{_f(cy)} '
                    f'L {_f(offset - sign * 2.3)},{_f(cy + 2.4)}" fill="none" stroke="{color}" '
                    f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>')
    if critical:
        points = "12,11 15.6,14.6 12,18.2 8.4,14.6"
        parts.append(f'<polygon points="{points}" fill="{CRITICAL_COLOR}" stroke="#fff" stroke-width="1.2"/>')
    return f'<svg class="legend-glyph" viewBox="0 0 24 22" width="26" height="24">{"".join(parts)}</svg>'


def legend_samples() -> list[dict[str, str]]:
    return [
        {"label": "TAP", "glyph": _mini_note("tap")},
        {"label": "SLIDE", "glyph": _mini_note("slide")},
        {"label": "FLICK", "glyph": _mini_note("flick")},
        {"label": "TRACE", "glyph": _mini_note("trace")},
        {"label": "CRIT", "glyph": _mini_note("tap", critical=True)},
        {"label": "GUIDE", "glyph": _mini_note("connection").replace(
            ROLE_COLORS["connection"], ROLE_COLORS["guide"]).replace(
            'stroke-width="1.4"', 'stroke-width="1.4" stroke-dasharray="3 3"')},
        {"label": "FEVER", "glyph": (
            '<svg class="legend-glyph" viewBox="0 0 24 22" width="26" height="24">'
            f'<rect x="8" y="4" width="14" height="13" fill="{FEVER_RAIL}" fill-opacity="0.12"/>'
            f'<line x1="4" y1="4" x2="4" y2="17" stroke="{FEVER_RAIL}" stroke-width="2.2"/></svg>'
        )},
    ]
