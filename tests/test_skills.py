"""Skill-description formatter tests, ported from MoeNotes' skills.test.ts."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bdon.skills import (  # noqa: E402
    SkillTextContext,
    compact_description,
    format_description,
    group_effects_by_level,
)

DUMMY_EFFECT = {
    "id": 1,
    "level": 1,
    "activationTimeSecond": 5,
    "effectValue": 1250,
    "maxEffectValue": 2000,
    "skillEffectType": 1000,
    "skillConditionGroup": 10,
    "skillTriggerConditionGroup": 20,
    "skillCumulativeConditionID": 30,
    "effectLimitCount": 3,
    "effectExecuteLimitCount": 4,
    "skillTargetIDs": [101],
}

DUMMY_EFFECT_2 = {
    "id": 2,
    "level": 1,
    "activationTimeSecond": 0,
    "effectValue": 8000,
    "skillEffectType": 2000,
    "skillConditionGroup": 11,
    "effectLimitCount": 1,
    "effectExecuteLimitCount": 2,
    "skillTargetIDs": [],
}

CONDITION_SETS = {
    10: [{"id": 1, "group": 10, "conditionIds": [100, 101]}],
    11: [{"id": 2, "group": 11, "conditionIds": [102, 103]}],
    20: [{"id": 3, "group": 20, "conditionIds": [200, 201]}],
}

CONDITIONS = {
    100: {"id": 100, "conditionValues": [15, 30]},
    101: {"id": 101, "conditionValues": [700]},
    102: {"id": 102, "conditionValues": [50], "conditionTargetIDs": [1]},
    103: {"id": 103, "conditionValues": [80], "conditionTargetIDs": [2]},
    200: {"id": 200, "conditionValues": [25]},
    201: {"id": 201, "conditionValues": [10]},
}

CUMULATIVE = {30: {"id": 30, "conditionValues": [100], "maxCumulativeCount": 5}}

TARGETS = {
    1: {"id": 1, "characterID": 10, "bandID": 0},
    2: {"id": 2, "characterID": 0, "bandID": 5},
}

CONTEXT = SkillTextContext(
    condition_sets=CONDITION_SETS,
    conditions=CONDITIONS,
    cumulative=CUMULATIVE,
    targets=TARGETS,
    band_names={5: "MyGO!!!!!"},
    character_names={10: "高松 燈"},
)


def fmt(template: str, effects=None) -> str:
    return format_description(template, effects or [DUMMY_EFFECT], CONTEXT)


def test_time_and_fractional_values() -> None:
    assert fmt("{effects[0].time:F1}秒間 スコア<color=#66FF8C>{effects[0].value/100:F1}%UP</color>") == (
        "5.0秒間 スコア12.5%UP"
    )


def test_integer_values_and_division() -> None:
    assert fmt("提升{effects[0].value}点 / {effects[0].value/1000:F2}") == "提升1250点 / 1.25"


def test_integral_floats_lose_the_decimal_point() -> None:
    effect = {**DUMMY_EFFECT, "effectValue": 2.0}
    assert fmt("获得量提升{effects[0].value}", [effect]) == "获得量提升2"


def test_limit_counts() -> None:
    assert fmt("次数：{effects[0].limitCount}回、最大{effects[0].EffectExecuteLimitCount}回") == (
        "次数：3回、最大4回"
    )


def test_conditions_with_double_and_single_index() -> None:
    template = (
        "值1={effects[0].con[0][0].values[0]} "
        "值2={effects[0].con[0].values1[0]} "
        "乘法={effects[0].con[0][0].values[0]*10:F1}"
    )
    assert fmt(template) == "值1=15 值2=15 乘法=150.0"


def test_trigger_conditions() -> None:
    template = "达成{effects[0].tCon[0][1].values[0]}回, 触发{effects[0].tCon[0].values1[0]}"
    assert fmt(template) == "达成10回, 触发25"


def test_cumulative_conditions_allow_spaces() -> None:
    template = "累计{effects[0]. cCon. values [0]}回，最大{effects[0].cCon.maxCount}次"
    assert fmt(template) == "累计100回，最大5次"


def test_target_names_from_characters_and_bands() -> None:
    template = (
        "若为「{effects[1].con[0][0].targets[0].name}」成员"
        "或「{effects[1].con[0][1].targets[0].name}」成员"
    )
    assert fmt(template, [DUMMY_EFFECT, DUMMY_EFFECT_2]) == "若为「高松 燈」成员或「MyGO!!!!!」成员"


def test_ternaries_use_effect_time() -> None:
    first = '{0 < effects[0].time ? "COMBO激奏开始后" : ""}'
    second = '{0 < effects[0].time ? effects[0].time ~ "秒内" : "COMBO激奏期间"}'
    third = '{0 <effects[0].time ? "for " ~ effects[0].time ~ " sec." : "During COMBO GEKISO"}'
    assert fmt(first) == "COMBO激奏开始后"
    assert fmt(second) == "5秒内"
    assert fmt(third) == "for 5 sec."
    assert fmt(first, [DUMMY_EFFECT_2]) == ""
    assert fmt(second, [DUMMY_EFFECT_2]) == "COMBO激奏期间"
    assert fmt(third, [DUMMY_EFFECT_2]) == "During COMBO GEKISO"


def test_newlines_and_colour_tags() -> None:
    template = "Line 1\\n<color=#FF0000>Line 2</color>\\nLine 3"
    assert fmt(template) == "Line 1\nLine 2\nLine 3"


def test_unknown_tokens_empty_the_description() -> None:
    assert fmt("未知占位符 {effects[99].nonexistent}") == ""


def test_group_effects_by_level_sorts_levels_and_rows() -> None:
    groups = group_effects_by_level(
        [
            {"id": 5, "level": 2},
            {"id": 2, "level": 1},
            {"id": 1, "level": 2},
        ]
    )
    assert [(level, [row["id"] for row in rows]) for level, rows in groups] == [
        (1, [2]),
        (2, [1, 5]),
    ]
    assert group_effects_by_level([]) == [(1, [])]


def test_compact_description_joins_phrase_breaks() -> None:
    assert compact_description("MyGO!!!!!成员的\n表演值提升12.0%") == (
        "MyGO!!!!!成员的表演值提升12.0%"
    )
    assert compact_description("LUCK激奏期间\n抽选条累积量提升100.0%") == (
        "LUCK激奏期间，抽选条累积量提升100.0%"
    )
    assert compact_description("获得量提升2\nBAD以下LIFE减少效果减轻20.0%") == (
        "获得量提升2，BAD以下LIFE减少效果减轻20.0%"
    )
    assert compact_description("若为「A」成员\n若为「B」成员") == (
        "若为「A」成员；若为「B」成员"
    )
    assert compact_description("只有一行") == "只有一行"
