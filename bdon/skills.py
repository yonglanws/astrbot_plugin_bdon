"""Formats skill description templates into per-level text.

Ported from MoeNotes' ``src/lib/cards/skills.ts`` (``formatSkillDescription``,
``groupEffectsByLevel``). MasterData stores one description template per skill
plus one effect row per level; the template's ``{effects[0].value/100:F1}``-style
placeholders carry the numbers, condition lookups and target names.

The port keeps upstream's evaluation order and its empty-string fallback for a
template whose target name cannot be resolved, so the bot shows the same text
as the source site. Rows are accepted with or without the leading underscore
the downloaded MasterData tables use.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# ``{effects[0].con[0][1].values[0]}`` etc. — see the substitutions below.
_TERNARY = re.compile(r"\{([^{}]+?)\?([^{}]+?):([^{}]+?)\}")
_TIME_TOKEN = re.compile(r"effects\[(\d+)\]\.time(?::F(\d+))?", re.IGNORECASE)
_TIME_CONDITION = re.compile(
    r"(?:0\s*<\s*effects\[(\d+)\]\.time|effects\[(\d+)\]\.time\s*>\s*0)",
    re.IGNORECASE,
)
_MULTIPLIED_CONDITION = re.compile(
    r"\{effects\[(\d+)\]\s*\.\s*(con|tCon)\[(\d+)\]\[(\d+)\]\s*\.\s*"
    r"values\s*\[(\d+)\]\s*\*\s*(\d+)(?:\s*:\s*F(\d+))?\}",
    re.IGNORECASE,
)
_TARGET_NAME = re.compile(
    r"\{effects\[(\d+)\]\s*\.\s*"
    r"(?:(con|tCon)\[(\d+)\]\[(\d+)\]\s*\.\s*)?"
    r"targets\s*\[(\d+)\]\s*\.\s*name\}",
    re.IGNORECASE,
)
_CONDITION_VALUE = re.compile(
    r"\{effects\[(\d+)\]\s*\.\s*(con|tCon)\[(\d+)\]\[(\d+)\]\s*\.\s*"
    r"values\s*\[(\d+)\](?:\s*:\s*F(\d+))?\}",
    re.IGNORECASE,
)
_FIRST_CONDITION_VALUE = re.compile(
    r"\{effects\[(\d+)\]\s*\.\s*(con|tCon)\[(\d+)\]\s*\.\s*"
    r"values1\s*\[(\d+)\](?:\s*:\s*F(\d+))?\}",
    re.IGNORECASE,
)
_CUMULATIVE_VALUE = re.compile(
    r"\{effects\[(\d+)\]\s*\.\s*cCon\s*\.\s*values\s*\[(\d+)\](?:\s*:\s*F(\d+))?\}",
    re.IGNORECASE,
)
_CUMULATIVE_MAX = re.compile(
    r"\{effects\[(\d+)\]\s*\.\s*cCon\s*\.\s*maxCount\}", re.IGNORECASE
)
_EXECUTE_LIMIT = re.compile(
    r"\{effects\[(\d+)\]\s*\.\s*EffectExecuteLimitCount\}", re.IGNORECASE
)
_EFFECT_LIMIT = re.compile(r"\{effects\[(\d+)\]\s*\.\s*limitCount\}", re.IGNORECASE)
_EFFECT_VALUE = re.compile(
    r"\{effects\[(\d+)\]\s*\.\s*(time|value|maxValue)"
    r"(?:\s*/\s*(\d+))?(?:\s*:\s*F(\d+))?\}",
    re.IGNORECASE,
)
_COLOR_TAG = re.compile(r"</?color(?:=[^>]+)?>", re.IGNORECASE)


@dataclass(frozen=True)
class SkillTextContext:
    """Lookup tables the description formatter needs beyond the effect rows."""

    condition_sets: Mapping[int, Sequence[Mapping[str, Any]]]
    conditions: Mapping[int, Mapping[str, Any]]
    cumulative: Mapping[int, Mapping[str, Any]]
    targets: Mapping[int, Mapping[str, Any]]
    band_names: Mapping[int, str]
    character_names: Mapping[int, str]


def _field(row: Mapping[str, Any], name: str) -> Any:
    if name in row:
        return row[name]
    return row.get("_" + name)


def number_text(value: Any, digits: str | None = None) -> str:
    """Matches JavaScript's ``String(number)`` / ``toFixed(digits)``."""
    if value is None:
        return ""
    if digits is None:
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    return f"{float(value):.{int(digits)}f}"


def group_effects_by_level(
    effects: Iterable[Mapping[str, Any]],
) -> list[tuple[int, list[Mapping[str, Any]]]]:
    """Groups effect rows by level, ascending; a skill without rows yields one empty level."""
    by_level: dict[int, list[Mapping[str, Any]]] = {}
    for row in effects:
        level = int(_field(row, "level") or 0)
        by_level.setdefault(level, []).append(row)
    if not by_level:
        return [(1, [])]
    return [
        (level, sorted(rows, key=lambda row: int(_field(row, "id") or 0)))
        for level, rows in sorted(by_level.items())
    ]


def format_description(
    template: str,
    effects: Sequence[Mapping[str, Any]],
    context: SkillTextContext,
) -> str:
    """Fills one level's effect values into the skill's description template."""
    if not template:
        return ""

    unresolved_target = False

    def target_name(target_id: int | None) -> str:
        nonlocal unresolved_target
        if target_id is None:
            unresolved_target = True
            return ""
        target = context.targets.get(target_id)
        if target:
            band_id = int(_field(target, "bandID") or 0)
            if band_id and band_id in context.band_names:
                return context.band_names[band_id]
            character_id = int(_field(target, "characterID") or 0)
            if character_id and character_id in context.character_names:
                return context.character_names[character_id]
        unresolved_target = True
        return ""

    def condition_group(effect: Mapping[str, Any], kind: str) -> int:
        name = "skillConditionGroup" if kind.lower() == "con" else "skillTriggerConditionGroup"
        return int(_field(effect, name) or 0)

    def condition_at(
        effect: Mapping[str, Any], kind: str, set_index: int, condition_index: int
    ) -> Mapping[str, Any] | None:
        group = condition_group(effect, kind)
        if not group:
            return None
        sets = context.condition_sets.get(group) or ()
        condition_set = sets[set_index] if set_index < len(sets) else None
        if not condition_set:
            return None
        condition_ids = _field(condition_set, "conditionIds") or ()
        if condition_index >= len(condition_ids):
            return None
        return context.conditions.get(int(condition_ids[condition_index]))

    def value_at(condition: Mapping[str, Any] | None, index: int) -> Any:
        values = _field(condition, "conditionValues") or () if condition else ()
        return values[index] if index < len(values) else None

    def effect_at(index: int) -> Mapping[str, Any] | None:
        return effects[index] if index < len(effects) else None

    parsed = template
    while _TERNARY.search(parsed):
        parsed = _TERNARY.sub(
            lambda match: _evaluate_ternary(
                match.group(1), match.group(2), match.group(3), effects
            ),
            parsed,
        )

    def multiplied(match: re.Match[str]) -> str:
        effect = effect_at(int(match.group(1)))
        if not effect:
            return ""
        condition = condition_at(
            effect, match.group(2), int(match.group(3)), int(match.group(4))
        )
        value = value_at(condition, int(match.group(5)))
        if value is None:
            return ""
        return number_text(value * int(match.group(6)), match.group(7))

    parsed = _MULTIPLIED_CONDITION.sub(multiplied, parsed)

    def target(match: re.Match[str]) -> str:
        effect = effect_at(int(match.group(1)))
        if not effect:
            return ""
        if match.group(2):
            condition = condition_at(
                effect, match.group(2), int(match.group(3)), int(match.group(4))
            )
            targets = _field(condition, "conditionTargetIDs") or () if condition else ()
        else:
            targets = _field(effect, "skillTargetIDs") or ()
        index = int(match.group(5))
        return target_name(int(targets[index]) if index < len(targets) else None)

    parsed = _TARGET_NAME.sub(target, parsed)

    def condition_value(match: re.Match[str]) -> str:
        effect = effect_at(int(match.group(1)))
        if not effect:
            return ""
        condition = condition_at(
            effect, match.group(2), int(match.group(3)), int(match.group(4))
        )
        return number_text(value_at(condition, int(match.group(5))), match.group(6))

    parsed = _CONDITION_VALUE.sub(condition_value, parsed)

    def first_condition_value(match: re.Match[str]) -> str:
        effect = effect_at(int(match.group(1)))
        if not effect:
            return ""
        condition = condition_at(effect, match.group(2), int(match.group(3)), 0)
        return number_text(value_at(condition, int(match.group(4))), match.group(5))

    parsed = _FIRST_CONDITION_VALUE.sub(first_condition_value, parsed)

    def cumulative_row(effect: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        if not effect:
            return None
        return context.cumulative.get(int(_field(effect, "skillCumulativeConditionID") or 0))

    def cumulative_value(match: re.Match[str]) -> str:
        row = cumulative_row(effect_at(int(match.group(1))))
        values = _field(row, "conditionValues") or () if row else ()
        index = int(match.group(2))
        return number_text(values[index] if index < len(values) else None, match.group(3))

    parsed = _CUMULATIVE_VALUE.sub(cumulative_value, parsed)

    def cumulative_max(match: re.Match[str]) -> str:
        row = cumulative_row(effect_at(int(match.group(1))))
        return str(_field(row, "maxCumulativeCount") or "") if row else ""

    parsed = _CUMULATIVE_MAX.sub(cumulative_max, parsed)

    def execute_limit(match: re.Match[str]) -> str:
        effect = effect_at(int(match.group(1)))
        return str(_field(effect, "effectExecuteLimitCount") or "") if effect else ""

    parsed = _EXECUTE_LIMIT.sub(execute_limit, parsed)

    def effect_limit(match: re.Match[str]) -> str:
        effect = effect_at(int(match.group(1)))
        return str(_field(effect, "effectLimitCount") or "") if effect else ""

    parsed = _EFFECT_LIMIT.sub(effect_limit, parsed)

    def effect_value(match: re.Match[str]) -> str:
        effect = effect_at(int(match.group(1)))
        if not effect:
            return ""
        field_name = match.group(2).lower()
        raw = _field(
            effect,
            {
                "time": "activationTimeSecond",
                "value": "effectValue",
                "maxvalue": "maxEffectValue",
            }[field_name],
        )
        value = None if raw is None else raw / int(match.group(3) or 1)
        return number_text(value, match.group(4))

    parsed = _EFFECT_VALUE.sub(effect_value, parsed)
    parsed = _COLOR_TAG.sub("", parsed)
    parsed = parsed.replace("\\n", "\n").strip()
    if unresolved_target or "{effects[" in parsed:
        return ""
    return parsed


def compact_description(text: str) -> str:
    """Joins template line breaks that only split a phrase.

    The game's templates wrap long descriptions by hand, often in the middle of
    a phrase ("…成员的\\n表演值提升…"). The level table has a full row per
    level, so those breaks waste several lines. A break after punctuation or a
    modifier stays a break; other breaks become a comma. Upstream keeps the raw
    newlines, so ``format_description`` still returns them unchanged.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 1:
        return text.strip()
    result = lines[0]
    for line in lines[1:]:
        if result.endswith(
            ("，", "。", "、", "；", "：", "！", "？", ",", ".", ";", ":", "的", "地", "得")
        ):
            result += line
        elif line.startswith(("若为", "即使", "对")):
            result += "；" + line
        elif line.startswith(("则", "并且", "且")):
            result += line
        else:
            result += "，" + line
    return result


def _evaluate_branch(
    branch: str, effects: Sequence[Mapping[str, Any]]
) -> str:
    parts = []
    for part in branch.split("~"):
        trimmed = part.strip()
        match = _TIME_TOKEN.search(trimmed)
        if match:
            effect = effects[int(match.group(1))] if int(match.group(1)) < len(effects) else None
            value = _field(effect, "activationTimeSecond") if effect else None
            if value is None:
                parts.append("")
            elif match.group(2) is not None:
                parts.append(f"{float(value):.{int(match.group(2))}f}")
            else:
                parts.append(number_text(value))
        else:
            parts.append(re.sub(r'^["\']|["\']$', "", trimmed).replace('\\"', '"'))
    return "".join(parts)


def _evaluate_ternary(
    condition: str,
    when_true: str,
    when_false: str,
    effects: Sequence[Mapping[str, Any]],
) -> str:
    result = False
    match = _TIME_CONDITION.search(condition)
    if match:
        index = int(match.group(1) or match.group(2))
        effect = effects[index] if index < len(effects) else None
        time = _field(effect, "activationTimeSecond") if effect else None
        if time is not None and 0 < time < 99999:
            result = True
    return _evaluate_branch(when_true if result else when_false, effects)
