"""Parser for Our Notes (nnnotes) score files — a Python port of the subset of
``moenotes-chart-parser`` (C, v0.3.0, vendored in
luoxiadesu/moenotes-chart-renderer) that the 2D sheet preview needs.

Input is the chart JSON published at ``Live/MusicScore/<chartKey>/<file>.json``
(either the bare score object or the ``{"meta":…, "score":…}`` wrapper). The
model follows the C parser: 480 ticks per beat, 24 lanes, lane positions as
floats where a note occupies ``[left, left + width)``.

The slide-combo reconstruction and the hidden-flick infrastructure of the C
parser are intentionally not ported: the sheet draws masterdata's full-combo
count and real charts carry no ``line_indices`` slots.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

PPQ = 480
LANES = 24

# Operate types, mirroring moenotes_operate_type_t.
OP_NORMAL = 1
OP_SLIDE_BEGIN = 20
OP_SLIDE_CONNECTION = 21
OP_SLIDE_END = 22
OP_FLICK = 40
OP_SLIDE_BEGIN_FLICK = 41
OP_SLIDE_END_FLICK = 42
OP_TRACE = 60
OP_SLIDE_BEGIN_TRACE = 61
OP_SLIDE_END_TRACE = 62
OP_SLIDE_CONNECTION_TRACE = 63
OP_HIDDEN_SLIDE_BEGIN = 80
OP_HIDDEN_SLIDE_END = 82
OP_GUIDE_BEGIN = 100
OP_GUIDE_BEGIN_NORMAL = 101
OP_GUIDE_BEGIN_FLICK = 102
OP_GUIDE_END = 103
OP_GUIDE_BEGIN_TRACE = 104
OP_GUIDE_END_TRACE = 105
OP_HIDDEN = 122

# moenotes_ease_t values: LINEAR=0, OUT=1, IN=2.
EASE_LINEAR = 0
EASE_OUT = 1
EASE_IN = 2

DIRECTION_NORMAL = 0
DIRECTION_LEFT = 1
DIRECTION_RIGHT = 2

ALPHA_NONE = 0
ALPHA_FADE_IN = 1
ALPHA_FADE_OUT = 2

_JUDGEMENT_OPS = frozenset({OP_NORMAL, OP_SLIDE_BEGIN, OP_SLIDE_END, OP_FLICK,
                            OP_SLIDE_BEGIN_FLICK, OP_SLIDE_END_FLICK})
_ENDPOINT_OPS = frozenset({OP_SLIDE_BEGIN, OP_SLIDE_END, OP_SLIDE_BEGIN_FLICK,
                           OP_SLIDE_END_FLICK, OP_SLIDE_BEGIN_TRACE,
                           OP_SLIDE_END_TRACE, OP_HIDDEN_SLIDE_BEGIN,
                           OP_HIDDEN_SLIDE_END, OP_GUIDE_BEGIN,
                           OP_GUIDE_BEGIN_NORMAL, OP_GUIDE_BEGIN_FLICK,
                           OP_GUIDE_END, OP_GUIDE_BEGIN_TRACE,
                           OP_GUIDE_END_TRACE})

_EASE_NAMES = {"linear": EASE_LINEAR, "out": EASE_OUT, "in": EASE_IN}
_TYPE_NAMES = {"tap": 0, "flick": 1, "trace": 2, "long": 3, "guide": 4, "node": 5}
_DIRECTION_NAMES = {"up": DIRECTION_NORMAL, "left": DIRECTION_LEFT,
                    "right": DIRECTION_RIGHT}
_ALPHA_NAMES = {"none": ALPHA_NONE, "fadeIn": ALPHA_FADE_IN,
                "fadeOut": ALPHA_FADE_OUT}


class ChartError(ValueError):
    """Raised for score files the port refuses (same spirit as the C errors)."""


def even_round(value: float) -> int:
    """The parser's round-half-to-even, used for cumulative BPM times."""
    low = math.floor(value)
    frac = value - low
    add = frac > 0.5 or (frac == 0.5 and low % 2 != 0)
    return int(low) + (1 if add else 0)


def eased(t: float, ease: int) -> float:
    if ease == EASE_IN:
        return t * t
    if ease == EASE_OUT:
        return t * (2.0 - t)
    return t


@dataclass
class Note:
    id: int
    op: int
    tick: int
    bar: int = 0
    bar_progress: float = 0.0
    left: float = 0.0
    width: float = 0.0
    source_left: float = 0.0
    source_width: float = 0.0
    critical: bool = False
    visible: bool = True
    pos_auto: bool = False
    direction: int = DIRECTION_NORMAL
    ease_left: int = EASE_LINEAR
    ease_right: int = EASE_LINEAR
    alpha: int = ALPHA_NONE
    line_id: int = -1

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def source_right(self) -> float:
        return self.source_left + self.source_width

    @property
    def slide_along(self) -> bool:
        return self.pos_auto

    @property
    def position_key(self) -> float:
        return self.bar + self.bar_progress

    def role(self) -> str | None:
        """Drawing role, from moenotes-chart-renderer's Note::role()."""
        if self.op in (OP_NORMAL, OP_GUIDE_BEGIN_NORMAL):
            return "tap"
        if self.op == OP_SLIDE_BEGIN:
            return "slide"
        if self.op == OP_SLIDE_CONNECTION:
            return "connection"
        if self.op == OP_SLIDE_END:
            return "slide_end"
        if OP_FLICK <= self.op <= OP_SLIDE_END_FLICK or self.op == OP_GUIDE_BEGIN_FLICK:
            if self.direction == DIRECTION_LEFT:
                return "flick_left"
            if self.direction == DIRECTION_RIGHT:
                return "flick_right"
            return "flick"
        if OP_TRACE <= self.op <= OP_SLIDE_CONNECTION_TRACE or self.op in (
            OP_GUIDE_BEGIN_TRACE, OP_GUIDE_END_TRACE
        ):
            return "trace"
        return None


@dataclass
class Line:
    """One long/guide note: a contiguous slice of ``Score.notes``."""

    first: int
    count: int
    guide: bool

    def members(self, notes: list[Note]) -> list[Note]:
        return notes[self.first:self.first + self.count]


@dataclass
class Score:
    notes: list[Note] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    bpms: list[tuple[int, float]] = field(default_factory=list)   # (tick, bpm)
    bpm_times: list[int] = field(default_factory=list)            # aligned with bpms
    sigs: list[tuple[int, int, int, int, int]] = field(default_factory=list)
    # sigs entries: (tick, numerator, denominator, bar_length_ticks, bar)
    skill: list[int] = field(default_factory=list)
    fever: list[tuple[int, int]] = field(default_factory=list)
    calls: list[tuple[int, list[float]]] = field(default_factory=list)
    aliases: set[int] = field(default_factory=set)
    pairs: list[tuple[int, int]] = field(default_factory=list)

    # -- timeline ---------------------------------------------------------------
    def sig_at(self, tick: int) -> tuple[int, int, int, int, int]:
        current = self.sigs[0]
        for entry in self.sigs[1:]:
            if entry[0] <= tick:
                current = entry
            else:
                break
        return current

    def bpm_at(self, tick: int) -> tuple[int, float, int]:
        index = 0
        for i, (start, value) in enumerate(self.bpms):
            if start <= tick:
                index = i
            else:
                break
        start, value = self.bpms[index]
        return start, value, self.bpm_times[index]

    def position(self, tick: int) -> tuple[int, float, int]:
        """(bar number 0-based, bar progress 0..1, time in ms) on the tick clock."""
        sig_tick, _, _, length, sig_bar = self.sig_at(tick)
        bpm_tick, bpm_value, bpm_time = self.bpm_at(tick)
        delta = tick - bpm_tick
        time_ms = math.floor(bpm_time + delta * 60000.0 / (bpm_value * PPQ))
        bar = sig_bar + (tick - sig_tick) // length
        rhythm = (tick - sig_tick) % length
        return bar, rhythm / length, int(time_ms)

    def max_tick(self) -> int:
        ticks = [note.tick for note in self.notes]
        ticks.extend(tick for tick, _ in self.bpms)
        ticks.extend(tick for tick, *_ in self.sigs)
        ticks.extend(self.skill)
        ticks.extend(end for _, end in self.fever)
        ticks.extend(tick for tick, _ in self.calls)
        return max(ticks, default=0)

    def bar_count(self) -> int:
        bar = max((note.bar for note in self.notes), default=0)
        return bar + 1

    def bpm_range(self) -> tuple[float, float]:
        values = [value for _, value in self.bpms] or [120.0]
        return min(values), max(values)

    def visible_notes(self) -> list[Note]:
        return [note for note in self.notes
                if note.id not in self.aliases and note.role() is not None
                and note.width > 0.0]


def _geometry(note: Note, pos: float, width: float) -> None:
    note.left = float(pos)
    note.width = float(width)


def _interpolate(a: Note, b: Note, tick: int) -> tuple[float, float]:
    """Lane [left, right) at ``tick`` between anchors, eased by ``a``'s eases."""
    t = (tick - a.tick) / (b.tick - a.tick) if b.tick > a.tick else 0.0
    left = a.source_left + eased(t, a.ease_left) * (b.source_left - a.source_left)
    a_right = a.source_right
    b_right = b.source_right
    right = a_right + eased(t, a.ease_right) * (b_right - a_right)
    return left, right


def _line_op(guide: bool, node_type: int, k: int, count: int, note: Note) -> int:
    if guide:
        if k == 0:
            return OP_GUIDE_BEGIN
        if k == count - 1:
            return OP_GUIDE_END_TRACE if (note.visible and node_type == 2) else OP_GUIDE_END
        return OP_SLIDE_CONNECTION_TRACE if (note.pos_auto or note.visible) else OP_HIDDEN
    if k == 0:
        if not note.visible:
            return OP_HIDDEN_SLIDE_BEGIN
        if node_type == 1:
            return OP_SLIDE_BEGIN_FLICK
        if node_type == 2:
            return OP_SLIDE_BEGIN_TRACE
        return OP_SLIDE_BEGIN
    if k == count - 1:
        if not note.visible:
            return OP_HIDDEN_SLIDE_END
        if node_type == 1:
            return OP_SLIDE_END_FLICK
        if node_type == 2:
            return OP_SLIDE_END_TRACE
        return OP_SLIDE_END
    if note.pos_auto:
        return OP_SLIDE_CONNECTION
    if not note.visible:
        return OP_HIDDEN
    return OP_SLIDE_CONNECTION_TRACE if node_type == 2 else OP_SLIDE_CONNECTION


def _number(value: Any, default: float, lo: float, hi: float, label: str) -> float:
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ChartError(f"{label} must be a number")
    value = float(value)
    if not math.isfinite(value) or value < lo or value > hi:
        raise ChartError(f"{label} out of range")
    return value


def _read_scalar(raw: dict[str, Any], key: str, default: float, lo: float,
                 hi: float) -> float:
    return _number(raw.get(key), default, lo, hi, key)


def _read_note(raw: dict[str, Any]) -> Note:
    if not isinstance(raw, dict):
        raise ChartError("note must be an object")
    note = Note(id=-1, op=OP_NORMAL, tick=int(_read_scalar(raw, "t", 0, 0, 2**31 - 1)))
    note.critical = bool(raw.get("crit") or False)
    visible = raw.get("visible", True)
    if not isinstance(visible, bool):
        raise ChartError("visible must be a boolean")
    note.visible = visible
    pos = raw.get("pos", 0)
    if isinstance(pos, str):
        if pos != "auto":
            raise ChartError("pos must be a number or \"auto\"")
        note.pos_auto = True
        pos_value = 0.0
    else:
        pos_value = _read_scalar(raw, "pos", 0, -100000, 100000)
    width = _read_scalar(raw, "size", 6, 0, 100000)
    _geometry(note, pos_value, width)
    note.source_left, note.source_width = note.left, note.width

    ease = raw.get("ease")
    if isinstance(ease, list):
        if len(ease) != 2:
            raise ChartError("ease must have two entries")
        pair = ease
    else:
        pair = [ease, ease]
    for index, value in enumerate(pair):
        if value is None:
            value = "linear"
        if not isinstance(value, str) or value not in _EASE_NAMES:
            raise ChartError("unknown ease value")
        if index == 0:
            note.ease_left = _EASE_NAMES[value]
        else:
            note.ease_right = _EASE_NAMES[value]

    direction = raw.get("dir", "up")
    if direction is not None and not isinstance(direction, str):
        raise ChartError("dir must be a string")
    if direction not in _DIRECTION_NAMES:
        raise ChartError("unknown dir value")
    note.direction = _DIRECTION_NAMES[direction]

    alpha = raw.get("alpha", "none")
    if alpha is not None and not isinstance(alpha, str):
        raise ChartError("alpha must be a string")
    if alpha not in _ALPHA_NAMES:
        raise ChartError("unknown alpha value")
    note.alpha = _ALPHA_NAMES[alpha]
    return note


def _note_type(raw: dict[str, Any]) -> int:
    value = raw.get("type", "tap")
    if value is None:
        value = "tap"
    if not isinstance(value, str) or value not in _TYPE_NAMES:
        raise ChartError("unknown note type")
    return _TYPE_NAMES[value]


def _build_timeline(score: Score, events: dict[str, Any]) -> None:
    bpm_rows: list[tuple[int, float]] = []
    for row in events.get("bpm") or []:
        if not isinstance(row, dict):
            raise ChartError("bpm event must be an object")
        tick = int(_read_scalar(row, "t", 0, 0, 2**31 - 1))
        value = _number(row.get("bpm"), 120, 0.001, 100000, "bpm")
        bpm_rows.append((tick, value))
    bpm_rows.sort(key=lambda item: item[0])
    if not bpm_rows or bpm_rows[0][0] > 0:
        bpm_rows.insert(0, (0, 120.0))
    cumulative_ms = 0.0
    times = [0]
    for (prev_tick, prev_value), (tick, _) in itertools.pairwise(bpm_rows):
        cumulative_ms += (tick - prev_tick) * 60000.0 / (prev_value * PPQ)
        if not math.isfinite(cumulative_ms) or cumulative_ms > 2**31 - 1:
            raise ChartError("BPM timeline out of range")
        times.append(even_round(cumulative_ms))
    score.bpms = bpm_rows
    score.bpm_times = times

    sig_rows: list[tuple[int, int, int]] = []
    for row in events.get("sig") or []:
        if not isinstance(row, dict):
            raise ChartError("sig event must be an object")
        compact = row.get("sig")
        if compact is not None:
            if not isinstance(compact, list) or len(compact) != 2:
                raise ChartError("sig must be [numerator, denominator]")
            num_value, den_value = compact
        else:
            num_value, den_value = row.get("numerator"), row.get("denominator")
        tick = int(_read_scalar(row, "t", 0, 0, 2**31 - 1))
        num = int(_number(num_value, 4, 1, 1024, "numerator"))
        den = int(_number(den_value, 4, 1, 1920, "denominator"))
        sig_rows.append((tick, num, den))
    sig_rows.sort(key=lambda item: item[0])
    if not sig_rows or sig_rows[0][0] > 0:
        sig_rows.insert(0, (0, 4, 4))
    built: list[tuple[int, int, int, int, int]] = []
    bar = 0
    for index, (tick, num, den) in enumerate(sig_rows):
        if index:
            prev = built[index - 1]
            bar = prev[4] + (tick - prev[0]) // prev[3]
        built.append((tick, num, den, (PPQ * 4 * num) // den, bar))
    score.sigs = built


def _build_events(score: Score, events: dict[str, Any]) -> None:
    for value in events.get("skill") or []:
        score.skill.append(int(_number(value, 0, 0, 2**31 - 1, "skill tick")))
    fever: list[tuple[int, int]] = []
    for row in events.get("fever") or []:
        if not isinstance(row, list) or len(row) != 2:
            raise ChartError("fever event must be [start, end]")
        start = int(_number(row[0], 0, 0, 2**31 - 1, "fever start"))
        end = int(_number(row[1], start, start, 2**31 - 1, "fever end"))
        fever.append((start, end))
    fever.sort(key=lambda item: item[0])
    score.fever = fever
    for row in events.get("call") or []:
        if not isinstance(row, dict):
            raise ChartError("call event must be an object")
        tick = int(_read_scalar(row, "t", 0, 0, 2**31 - 1))
        timing = row.get("timing")
        if not isinstance(timing, list):
            raise ChartError("call timing must be an array")
        rhythms = [(index + 1) / len(timing) for index, flag in enumerate(timing)
                   if flag == 1]
        score.calls.append((tick, rhythms))


def _build_notes(score: Score, rows: list[Any]) -> None:
    if len(rows) > 1_000_000:
        raise ChartError("too many notes")
    refs: list[tuple[int, dict[str, Any]]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise ChartError("note must be an object")
        tick = raw.get("t")
        if tick is None and isinstance(raw.get("node"), list) and raw["node"]:
            tick = raw["node"][0].get("t")
        if tick is None or not isinstance(tick, (int, float)) or isinstance(tick, bool):
            raise ChartError("note is missing t")
        refs.append((int(tick), raw))
    refs.sort(key=lambda item: item[0])  # stable: source order breaks ties

    notes: list[Note] = score.notes
    lines: list[Line] = score.lines
    for _, raw in refs:
        note_type = _note_type(raw)
        if note_type not in (3, 4):
            note = _read_note(raw)
            note.id = len(notes)
            note.op = (OP_FLICK if note_type == 1
                       else OP_TRACE if note_type == 2 else OP_NORMAL)
            notes.append(note)
            continue

        nodes = raw.get("node")
        if not isinstance(nodes, list):
            raise ChartError("long/guide note must carry a node array")
        outer = raw.get("t") is not None
        count = len(nodes) + (1 if outer else 0)
        if count < 2:
            raise ChartError("long/guide note needs at least two nodes")
        first_index = len(notes)
        guide = note_type == 4
        for k in range(count):
            node_raw = raw if (outer and k == 0) else nodes[k - (1 if outer else 0)]
            if not isinstance(node_raw, dict):
                raise ChartError("node must be an object")
            node_type = _note_type(node_raw)
            note = _read_note(node_raw)
            note.id = len(notes)
            note.op = _line_op(guide, node_type, k, count, note)
            note.line_id = len(lines)
            notes.append(note)
        members = notes[first_index:first_index + count]

        for k, note in enumerate(members):
            if not note.pos_auto:
                continue
            left = k
            while left > 0 and members[left].pos_auto:
                left -= 1
            right = k
            while right + 1 < count and members[right].pos_auto:
                right += 1
            if k == 0 or k == count - 1:
                _geometry(note, members[0].left, members[0].width)
            else:
                lane, lane_right = _interpolate(members[left], members[right], note.tick)
                width = lane_right - lane
                if width < 0 or abs(lane) > 200000 or width > 200000:
                    raise ChartError("auto node geometry out of range")
                _geometry(note, lane, width)
        lines.append(Line(first=first_index, count=count, guide=guide))

    for note in notes:
        note.bar, note.bar_progress, _ = score.position(note.tick)


def _merge_graph(score: Score) -> None:
    """Guide starts consume a matching standalone note; shared endpoints merge."""
    def overlap_key(note: Note) -> tuple[int, int, int]:
        return (note.tick, even_round(note.source_left), even_round(note.source_width))

    for line in score.lines:
        if not line.guide:
            continue
        head = score.notes[line.first]
        if head.pos_auto:
            continue
        for note in score.notes:
            if note.line_id != -1 or note.id in score.aliases:
                continue
            if note.op not in (OP_NORMAL, OP_FLICK, OP_TRACE):
                continue
            if overlap_key(note) != overlap_key(head):
                continue
            head.op = (OP_GUIDE_BEGIN_NORMAL if note.op == OP_NORMAL
                       else OP_GUIDE_BEGIN_FLICK if note.op == OP_FLICK
                       else OP_GUIDE_BEGIN_TRACE)
            head.direction = note.direction
            score.aliases.add(note.id)
            break

    def bucket(note: Note) -> tuple[float, int, int]:
        return (note.position_key, even_round(note.left), even_round(note.width))

    seen: dict[tuple, int] = {}
    for note in score.notes:
        if note.op not in _ENDPOINT_OPS or note.id in score.aliases:
            continue
        key = (bucket(note), note.op, note.direction, note.critical,
               note.ease_left, note.ease_right)
        previous = seen.get(key)
        if previous is not None:
            score.aliases.add(note.id)
        else:
            seen[key] = note.id


def _build_pairs(score: Score) -> None:
    """Sequential same-time pairing of judgement notes (creation order)."""
    def same_position(a: Note, b: Note) -> bool:
        if a.bar != b.bar:
            return False
        x, y = a.bar_progress, b.bar_progress
        tolerance = max(1e-6 * max(abs(x), abs(y)), 1e-45)
        return abs(x - y) < tolerance

    previous: Note | None = None
    for note in sorted((n for n in score.notes if n.op in _JUDGEMENT_OPS
                        and n.id not in score.aliases),
                       key=lambda n: (n.tick, n.id)):
        if previous is not None and same_position(previous, note):
            score.pairs.append((previous.id, note.id))
        previous = note


def parse_score(data: Any) -> Score:
    """Parses a score JSON payload (dict) into a :class:`Score`."""
    if not isinstance(data, dict):
        raise ChartError("score must be a JSON object")
    if isinstance(data.get("score"), dict):
        data = data["score"]
    events = data.get("events") or {}
    if not isinstance(events, dict):
        raise ChartError("events must be an object")
    rows = data.get("notes") or []
    if not isinstance(rows, list):
        raise ChartError("notes must be an array")

    score = Score()
    _build_timeline(score, events)
    _build_events(score, events)
    _build_notes(score, rows)
    _merge_graph(score)
    _build_pairs(score)
    return score


def load_score(payload: bytes | str) -> Score:
    import json

    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise ChartError(f"谱面文件不是有效的 JSON：{exc}") from exc
    return parse_score(data)


def bar_ticks(score: Score, count: int) -> list[tuple[int, int, int]]:
    """Tick / bar-number / time-ms of the first ``count`` bar lines."""
    result: list[tuple[int, int, int]] = []
    for number in range(count):
        current = score.sigs[0]
        for entry in score.sigs[1:]:
            if entry[4] <= number:
                current = entry
            else:
                break
        tick = current[0] + (number - current[4]) * current[3]
        _, _, time_ms = score.position(tick)
        result.append((tick, number + 1, time_ms))
    return result
