"""Normalises Our Notes masterdata tables into view models for the bot."""

from __future__ import annotations

import math
import re
import struct
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .aliases import character_aliases
from .assets import AssetBuilder
from .constants import (
    ATTRIBUTES,
    COMBO_TIERS,
    DIFFICULTIES,
    EVENT_TYPES,
    MEMBER_RARITIES,
    MUSIC_CATEGORY_FALLBACK,
    SCORE_GRADES,
    SCRIPT_TEXT_FIELDS,
    SUPPORT_RARITIES,
    RegionProfile,
)
from .icons import attribute_icon, rarity_icon
from .skills import (
    SkillTextContext,
    compact_description,
    format_description,
    group_effects_by_level,
)

CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
TAG_RE = re.compile(
    r"</?(?:color|size|b|i|u|s|br|ruby|rt|rb|space|link|material|emoji|sprite|"
    r"font|align|indent|line-height|width|height)(?:=[^>]*)?>",
    re.IGNORECASE,
)
PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}")
DATE_FORMATS = ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y-%m-%d")
# MasterLiveMusic _gekisouMission1..3: the mission type of each gekisou section.
GEKISOU_MISSION_LABELS = {1: "连击", 2: "幸运", 3: "Just 数"}


def rows(table: Any) -> list[dict[str, Any]]:
    if isinstance(table, list):
        return table
    if isinstance(table, dict):
        data = table.get("_allData")
        if isinstance(data, list):
            return data
    return []


def clean_text(value: str) -> str:
    value = TAG_RE.sub("", value or "")
    # Skill descriptions nest their format arguments (``{effects[0].con[0][0]...}``);
    # strip the innermost placeholders first, then any braces left behind.
    for _ in range(4):
        updated = PLACEHOLDER_RE.sub("…", value)
        if updated == value:
            break
        value = updated
    value = value.replace("{", "").replace("}", "").replace("\r", "")
    value = re.sub(r"(?:…\s*)+", "…", value)
    # Placeholder-only lines are formatting scaffolding, not content.
    value = "\n".join(line for line in value.splitlines() if line.strip(" …\t"))
    value = re.sub(r"^…[\s，,。]*", "", value)
    return re.sub(r"[ \t]+\n", "\n", value).strip()


def parse_date(value: str) -> datetime | None:
    value = (value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def format_date(value: str) -> str:
    parsed = parse_date(value)
    return parsed.strftime("%Y-%m-%d") if parsed else (value or "")


def format_level(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "-")
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


def _percent(value: float) -> str:
    """Formats a bonus rate without trailing zeros: 30.0 -> "30"."""
    rounded = round(float(value), 1)
    return str(int(rounded)) if rounded.is_integer() else f"{rounded:.1f}"


@dataclass
class Band:
    id: int
    name: str
    color: str
    search_names: str = ""


def _short_id(value: int) -> int:
    """The in-game number players see: songs are 100001+, stamps 1000000001+.

    The raw MasterData ID stays the query key; only the display shrinks.
    """
    if value >= 1_000_000_000:
        return value - 1_000_000_000
    if value >= 100_000:
        return value - 100_000
    return value


@dataclass
class Character:
    id: int
    name: str
    short_name: str
    band_id: int
    band_name: str
    color: str
    face_url: str
    face_fallback: str
    search_names: str = ""


@dataclass
class Difficulty:
    key: str
    label: str
    color: str
    level: int
    display_level: str
    notes: int
    score_id: int = 0
    # MasterLiveMusicScore._musicScoreTextFileName, e.g. "0001/0001_03"; the
    # score JSON lives at Live/MusicScore/<chart_key>/<file>.json.
    chart_key: str = ""


@dataclass
class ScoreRank:
    """One row of the song's live score ladder (the D row is always 0 and dropped)."""

    label: str
    required: int
    battle: int
    reward: str = ""


@dataclass
class ComboTier:
    """One combo-share row of the song's combo ladder on its hardest chart."""

    label: str
    combo: int
    reward: str = ""


@dataclass
class SkillLevel:
    level: int
    description: str


@dataclass
class Skill:
    label: str
    name: str
    description: str
    levels: list[SkillLevel] = field(default_factory=list)
    skill_id: int = 0
    icon_url: str = ""
    icon_fallback: str = ""


@dataclass
class CardGrowth:
    """The card at its maximum build: top level, training and awakening."""

    max_level: int
    max_awake: int
    max_rank: int
    leader_skill_level: int
    performance: int
    technic: int
    visual: int
    total_power: int


@dataclass
class SupportGrowth:
    max_level: int
    max_rank: int
    support_skill_level: int
    gekisou_skill_level: int


@dataclass
class Song:
    id: int
    title: str
    title_ja: str
    phonetic: str
    band_id: int
    band_name: str
    band_color: str
    vocalists: list[Character]
    type_label: str
    type_color: str
    categories: list[str]
    tags: list[str]
    difficulties: list[Difficulty]
    jacket_url: str
    jacket_fallback: str
    composer: str
    lyricist: str
    arranger: str
    start_at: str
    start_dt: datetime | None
    is_new: bool
    sort_order: int
    search_text: str = ""
    type_icon: str = ""
    gekisou_missions: list[str] = field(default_factory=list)
    score_ranks: list[ScoreRank] = field(default_factory=list)
    combo_tiers: list[ComboTier] = field(default_factory=list)

    @property
    def title_for_search(self) -> str:
        return self.title

    @property
    def display_id(self) -> int:
        """Short in-game number shown in the UI; songs start at 100001."""
        return self.id - 100000 if self.id >= 100000 else self.id


@dataclass
class Card:
    id: int
    asset_id: int
    title: str
    character_name: str
    character_id: int
    band_id: int
    band_name: str
    band_color: str
    rarity: int
    rarity_label: str
    rarity_color: str
    type_label: str
    type_color: str
    performance: int
    technic: int
    visual: int
    total_power: int
    art_url: str
    art_fallback: str
    thumb_url: str
    thumb_fallback: str
    face_url: str
    face_fallback: str
    skills: list[Skill]
    start_at: str
    start_dt: datetime | None
    is_new: bool
    search_text: str = ""
    rarity_icon: str = ""
    type_icon: str = ""
    growth: CardGrowth | None = None

    @property
    def title_for_search(self) -> str:
        return self.title

    @property
    def display_id(self) -> int:
        """Short in-game number shown in the UI."""
        return _short_id(self.id)


@dataclass
class SupportCard:
    id: int
    asset_id: int
    title: str
    name: str
    characters: list[Character]
    band_id: int
    band_name: str
    band_color: str
    rarity: int
    rarity_label: str
    rarity_color: str
    type_label: str
    type_color: str
    performance: int
    technic: int
    visual: int
    total_power: int
    art_url: str
    art_fallback: str
    thumb_url: str
    thumb_fallback: str
    skills: list[Skill]
    start_at: str
    start_dt: datetime | None
    is_new: bool
    search_text: str = ""
    rarity_icon: str = ""
    type_icon: str = ""
    growth: SupportGrowth | None = None
    diary: str = ""

    @property
    def title_for_search(self) -> str:
        return self.title

    @property
    def display_id(self) -> int:
        """Short in-game number shown in the UI."""
        return _short_id(self.id)


@dataclass
class Stamp:
    id: int
    name: str
    characters: list[Character]
    band_id: int
    band_name: str
    band_color: str
    image_url: str
    image_fallback: str
    category: int
    start_at: str
    start_dt: datetime | None
    search_text: str = ""

    @property
    def title_for_search(self) -> str:
        return self.name

    @property
    def display_id(self) -> int:
        """Short in-game number shown in the UI."""
        return _short_id(self.id)


@dataclass
class Comic:
    id: int
    name: str
    characters: list[Character]
    band_id: int
    band_name: str
    band_color: str
    image_url: str
    image_fallback: str
    order: int
    start_at: str
    start_dt: datetime | None
    search_text: str = ""

    @property
    def title_for_search(self) -> str:
        return self.name

    @property
    def display_id(self) -> int:
        """Short in-game number shown in the UI."""
        return _short_id(self.id)


@dataclass
class EventBonus:
    """One row of the event bonus table: a card, band or attribute."""

    kind: str  # "member" | "support" | "band" | "attribute"
    name: str
    sub: str
    values: list[float]
    color: str = "#0072C2"
    icon: str = ""
    image_url: str = ""
    image_fallback: str = ""
    detail_id: int = 0

    @property
    def rate_label(self) -> str:
        """Rank 1 and max-awakening rates, e.g. ``+30% → +50%``."""
        if not self.values:
            return ""
        return f"+{_percent(self.values[0])}% → +{_percent(self.values[-1])}%"


@dataclass
class EventReward:
    """One cumulative point milestone."""

    point: int
    labels: list[str]
    icons: list[str]

    @property
    def point_label(self) -> str:
        return f"{self.point:,} pt"


@dataclass
class EventGradeReward:
    """One D–SS row of the live / challenge performance table."""

    label: str
    points: int
    item_name: str
    item_count: int


@dataclass
class Event:
    id: int
    name: str
    event_type: int
    type_label: str
    start_at: str
    end_at: str
    display_end_at: str
    start_dt: datetime | None
    end_dt: datetime | None
    display_end_dt: datetime | None
    logo_url: str
    logo_fallback: str
    background_url: str
    background_fallback: str
    banner_url: str
    banner_fallback: str
    item_name: str
    item_icon: str
    song: Song | None
    story_name: str
    story_text: str
    member_bonuses: list[EventBonus]
    support_bonuses: list[EventBonus]
    rule_bonuses: list[EventBonus]
    point_rewards: list[EventReward]
    loop_label: str
    live_rewards: list[EventGradeReward]
    challenge_rewards: list[EventGradeReward]
    search_text: str = ""

    @property
    def title_for_search(self) -> str:
        return self.name

    @property
    def display_id(self) -> int:
        return self.id

    @property
    def period_label(self) -> str:
        if self.start_at and self.end_at:
            return f"{self.start_at} – {self.end_at}"
        return self.start_at or self.end_at or "—"

    @property
    def status(self) -> str:
        now = datetime.now()
        if self.start_dt and now < self.start_dt:
            return "upcoming"
        if self.end_dt and now > self.end_dt:
            return "ended"
        if self.start_dt and self.end_dt:
            return "ongoing"
        return "unknown"

    @property
    def status_label(self) -> str:
        return {
            "ongoing": "进行中",
            "upcoming": "即将开始",
            "ended": "已结束",
        }.get(self.status, "活动")

    @property
    def status_class(self) -> str:
        return {
            "ongoing": "event-live",
            "upcoming": "event-soon",
            "ended": "event-over",
        }.get(self.status, "event-over")

    @property
    def days_label(self) -> str:
        now = datetime.now()
        if self.status == "ongoing" and self.end_dt:
            days = max(0, math.ceil((self.end_dt - now).total_seconds() / 86400))
            return "最后一天" if days == 0 else f"剩余 {days} 天"
        if self.status == "upcoming" and self.start_dt:
            days = max(1, math.ceil((self.start_dt - now).total_seconds() / 86400))
            return f"{days} 天后开始"
        return "活动已结束"


@dataclass
class Dataset:
    region: RegionProfile
    songs: list[Song]
    cards: list[Card]
    supports: list[SupportCard]
    stamps: list[Stamp]
    comics: list[Comic]
    events: list[Event]
    bands: dict[int, Band]
    characters: dict[int, Character]
    script: str = "simplified"

    def items_for(self, kind: str) -> list[Any]:
        return {
            "song": self.songs,
            "card": self.cards,
            "support": self.supports,
            "stamp": self.stamps,
            "comic": self.comics,
            "event": self.events,
        }[kind]


@dataclass
class _SkillDefinition:
    id: int
    name: str
    template: str
    fallback_description: str
    icon_url: str = ""
    icon_fallback: str = ""


@dataclass
class _SkillLibrary:
    """Skill definitions, their effect rows and the shared condition lookups."""

    definitions: dict[str, dict[int, _SkillDefinition]]
    effects: dict[str, dict[int, list[dict[str, Any]]]]
    context: SkillTextContext

    def build(self, label: str, kind: str, skill_id: Any) -> Skill | None:
        try:
            numeric = int(skill_id or 0)
        except (TypeError, ValueError):
            numeric = 0
        if numeric <= 0:
            return None
        definition = self.definitions.get(kind, {}).get(numeric)
        if definition is None:
            return None
        rows = self.effects.get(kind, {}).get(numeric, [])
        levels = (
            [
                SkillLevel(
                    level,
                    compact_description(
                        format_description(definition.template, level_rows, self.context)
                    ),
                )
                for level, level_rows in group_effects_by_level(rows)
            ]
            if rows
            else []
        )
        # The max level is what players compare; keep it as the summary. Without
        # effect tables (older/partial region data) fall back to the cleaned
        # template instead of showing one level with missing numbers.
        description = (
            levels[-1].description if levels else definition.fallback_description
        )
        return Skill(
            label=label,
            name=definition.name or f"技能 #{numeric}",
            description=description,
            levels=levels,
            skill_id=numeric,
            icon_url=definition.icon_url,
            icon_fallback=definition.icon_fallback,
        )


def _f32(value: float) -> float:
    return struct.unpack("f", struct.pack("f", value))[0]


def _scale_by_rate(maximum: int, rate: int) -> int:
    """The client's FloorToInt((float)(rate * max) / 1e4f)."""
    return math.floor(_f32(_f32(rate * maximum) / 10000))


def _scale_by_rate_factor(maximum: int, rate: int) -> int:
    """The client's awake term, which multiplies in the other order."""
    return math.floor(_f32(_f32(rate / 10000) * _f32(maximum)))


class DatasetBuilder:
    def __init__(
        self,
        region: RegionProfile,
        asset_base: str,
        script: str = "simplified",
    ) -> None:
        self.region = region
        self.script = script if script in SCRIPT_TEXT_FIELDS else "simplified"
        self.text_fields = SCRIPT_TEXT_FIELDS[self.script]
        self.assets = AssetBuilder(asset_base, region, self.script)

    def build(self, tables: dict[str, Any]) -> Dataset:
        text_rows = rows(tables.get("MasterText"))
        self.texts = {str(row.get("_id")): row for row in text_rows if row.get("_id")}

        self.bands = self._build_bands(rows(tables.get("MasterBand")))
        self.characters = self._build_characters(rows(tables.get("MasterCharacter")))
        scores = {
            int(row["_id"]): row
            for row in rows(tables.get("MasterLiveMusicScore"))
            if row.get("_id") is not None
        }
        categories = self._build_categories(rows(tables.get("MasterLiveMusicCategory")))
        tags = {
            int(row["_id"]): (
                self.text(str(row.get("_nameTextID") or "")),
                self.all_text(str(row.get("_nameTextID") or "")),
            )
            for row in rows(tables.get("MasterTag"))
            if row.get("_id") is not None
        }
        skill_icons = self._build_skill_icons(rows(tables.get("MasterSkillIcon")))
        skills = _SkillLibrary(
            definitions={
                "leader": self._build_skill_table(
                    rows(tables.get("MasterLeaderSkill")), skill_icons
                ),
                "live": self._build_skill_table(
                    rows(tables.get("MasterLiveSkill")), skill_icons
                ),
                "support": self._build_skill_table(
                    rows(tables.get("MasterSupportSkill")), skill_icons
                ),
                "gekisou": self._build_skill_table(
                    rows(tables.get("MasterGekisouSkill")), skill_icons
                ),
                "gekisou_support": self._build_skill_table(
                    rows(tables.get("MasterGekisouSupportSkill")), skill_icons
                ),
            },
            effects={
                "leader": self._effect_index(
                    rows(tables.get("MasterLeaderSkillEffect")), "_leaderSkillID"
                ),
                "live": self._effect_index(
                    rows(tables.get("MasterLiveSkillEffect")), "_liveSkillID"
                ),
                "support": self._effect_index(
                    rows(tables.get("MasterSupportSkillEffect")), "_supportSkillID"
                ),
                "gekisou": self._effect_index(
                    rows(tables.get("MasterGekisouSkillEffect")), "_gekisouSkillID"
                ),
                "gekisou_support": self._effect_index(
                    rows(tables.get("MasterGekisouSupportSkillEffect")),
                    "_gekisouSupportSkillID",
                ),
            },
            context=SkillTextContext(
                condition_sets=self._condition_sets(
                    rows(tables.get("MasterSkillConditionSet"))
                ),
                conditions=self._index(
                    rows(tables.get("MasterSkillCondition"))
                ),
                cumulative=self._index(
                    rows(tables.get("MasterSkillCumulativeCondition"))
                ),
                targets=self._index(rows(tables.get("MasterSkillTarget"))),
                band_names={band.id: band.name for band in self.bands.values()},
                character_names={
                    character.id: character.name
                    for character in self.characters.values()
                },
            ),
        )
        growth_tables = {
            "levels": rows(tables.get("MasterMemberCardLevel")),
            "level_limits": rows(tables.get("MasterMemberCardLevelLimit")),
            "awakes": rows(tables.get("MasterMemberCardAwake")),
            "ranks": rows(tables.get("MasterMemberCardRank")),
            "support_levels": rows(tables.get("MasterSupportCardLevel")),
            "support_ranks": rows(tables.get("MasterSupportCardRank")),
        }

        cards = self._build_cards(
            rows(tables.get("MasterMemberCard")), skills, growth_tables
        )
        supports = self._build_supports(
            rows(tables.get("MasterSupportCard")), skills, growth_tables
        )
        stamps = self._build_stamps(rows(tables.get("MasterStamp")))
        # Songs come after the reward sources: their live rules (score ranks,
        # score / combo rewards) resolve item names through the same tables.
        resolve_reward = self._make_reward_resolver(
            rows(tables.get("MasterItem")), cards, supports, stamps
        )
        songs = self._build_songs(
            rows(tables.get("MasterLiveMusic")),
            scores,
            categories,
            tags,
            resolve_reward,
            rows(tables.get("MasterLiveScoreRank")),
            rows(tables.get("MasterLiveMusicScoreReward")),
            rows(tables.get("MasterLiveMusicComboReward")),
        )
        comics = self._build_comics(rows(tables.get("MasterLoadingComics")))
        events = self._build_events(
            rows(tables.get("MasterEvent")),
            rows(tables.get("MasterEventEffect")),
            rows(tables.get("MasterEventAchievementReward")),
            rows(tables.get("MasterEventAchievementLoopReward")),
            rows(tables.get("MasterReward")),
            rows(tables.get("MasterItem")),
            rows(tables.get("MasterStoryChapter")),
            rows(tables.get("MasterLiveEventPoint")),
            rows(tables.get("MasterLiveEventReward")),
            rows(tables.get("MasterChallengeLiveEventPoint")),
            rows(tables.get("MasterChallengeLiveEventReward")),
            resolve_reward,
            songs,
            cards,
            supports,
        )

        return Dataset(
            region=self.region,
            songs=songs,
            cards=cards,
            supports=supports,
            stamps=stamps,
            comics=comics,
            events=events,
            bands=self.bands,
            characters=self.characters,
            script=self.script,
        )

    # -- text ------------------------------------------------------------------
    def text(self, text_id: str) -> str:
        if not text_id:
            return ""
        row = self.texts.get(text_id)
        if not row:
            return text_id
        for field_name in self.text_fields:
            value = (row.get(field_name) or "").strip()
            if value and value != text_id:
                return clean_text(value)
        return text_id

    def raw_text(self, text_id: str, field_name: str) -> str:
        row = self.texts.get(text_id) or {}
        return clean_text((row.get(field_name) or "").strip())

    def template_text(self, text_id: str) -> str:
        """The untrimmed template, keeping the ``{effects[...]}`` placeholders."""
        if not text_id:
            return ""
        row = self.texts.get(text_id) or {}
        for field_name in self.text_fields:
            value = (row.get(field_name) or "").strip()
            if value and value != text_id:
                return value
        return ""

    def all_text(self, text_id: str) -> str:
        """Every language of a row, for cross-language search."""
        row = self.texts.get(text_id) or {}
        values = []
        for field_name in (
            "_japanese",
            "_english",
            "_simplifiedChinese",
            "_traditionalChinese",
            "_korean",
        ):
            value = clean_text((row.get(field_name) or "").strip())
            if value and value != text_id and value not in values:
                values.append(value)
        return " ".join(values)

    # -- lookups ---------------------------------------------------------------
    def _build_bands(self, data: Iterable[dict[str, Any]]) -> dict[int, Band]:
        result: dict[int, Band] = {}
        for row in data:
            band_id = int(row.get("_id") or 0)
            if not band_id:
                continue
            result[band_id] = Band(
                id=band_id,
                name=self.text(str(row.get("_nameTextID") or "")),
                color=(row.get("_mainColorCode") or "#0072C2").strip(),
                search_names=self.all_text(str(row.get("_nameTextID") or "")),
            )
        return result

    def _build_characters(
        self, data: Iterable[dict[str, Any]]
    ) -> dict[int, Character]:
        result: dict[int, Character] = {}
        for row in data:
            character_id = int(row.get("_id") or 0)
            if not character_id:
                continue
            band_id = int(row.get("_bandID") or 0)
            band = self.bands.get(band_id)
            face, face_fb = self.assets.character_face(character_id)
            name_text_id = str(row.get("_nameTextID") or "")
            short_text_id = str(row.get("_shortNameTextID") or "")
            result[character_id] = Character(
                id=character_id,
                name=self.text(name_text_id),
                short_name=self.text(short_text_id),
                band_id=band_id,
                band_name=band.name if band else "",
                color=(row.get("_mainColorCode") or "#0072C2").strip(),
                face_url=face,
                face_fallback=face_fb,
                search_names=(
                    f"{self.all_text(name_text_id)} {self.all_text(short_text_id)}"
                    f" {' '.join(character_aliases(character_id))}"
                ).strip(),
            )
        return result

    def _build_categories(
        self, data: Iterable[dict[str, Any]]
    ) -> dict[int, tuple[str, str]]:
        result: dict[int, tuple[str, str]] = {}
        for row in data:
            text_key = str(row.get("_textKey") or "")
            pair = (self.text(text_key), self.all_text(text_key))
            for category_id in row.get("_musicCategories") or []:
                result[int(category_id)] = pair
        return result

    def _build_skill_icons(
        self, data: Iterable[dict[str, Any]]
    ) -> dict[int, tuple[str, str]]:
        return {
            int(row.get("_id") or 0): self.assets.skill_icon(
                str(row.get("_normalIconAssetName") or "")
            )
            for row in data
            if row.get("_id")
        }

    def _build_skill_table(
        self,
        data: Iterable[dict[str, Any]],
        icons: Mapping[int, tuple[str, str]],
    ) -> dict[int, _SkillDefinition]:
        result: dict[int, _SkillDefinition] = {}
        for row in data:
            skill_id = int(row.get("_id") or 0)
            if not skill_id:
                continue
            icon_url, icon_fallback = icons.get(
                int(row.get("_skillIconID") or 0), ("", "")
            )
            text_id = str(row.get("_descriptionTextFormatID") or "")
            result[skill_id] = _SkillDefinition(
                id=skill_id,
                name=self.text(str(row.get("_nameTextID") or "")),
                # Keep the placeholders: the formatter fills them per level.
                template=self.template_text(text_id),
                fallback_description=compact_description(self.text(text_id)),
                icon_url=icon_url,
                icon_fallback=icon_fallback,
            )
        return result

    @staticmethod
    def _index(data: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        return {
            int(row.get("_id") or 0): row
            for row in data
            if row.get("_id") is not None
        }

    @staticmethod
    def _effect_index(
        data: Iterable[dict[str, Any]], field_name: str
    ) -> dict[int, list[dict[str, Any]]]:
        result: dict[int, list[dict[str, Any]]] = {}
        for row in data:
            skill_id = int(row.get(field_name) or 0)
            if skill_id:
                result.setdefault(skill_id, []).append(row)
        return result

    @staticmethod
    def _condition_sets(
        data: Iterable[dict[str, Any]]
    ) -> dict[int, list[dict[str, Any]]]:
        result: dict[int, list[dict[str, Any]]] = {}
        for row in data:
            result.setdefault(int(row.get("_group") or 0), []).append(row)
        return result

    def _characters(self, ids: Iterable[int]) -> list[Character]:
        result = []
        for character_id in ids or []:
            character = self.characters.get(int(character_id))
            if character:
                result.append(character)
        return result

    def _band(self, band_id: int) -> Band | None:
        return self.bands.get(band_id)

    # -- songs -----------------------------------------------------------------
    def _build_songs(
        self,
        data: Iterable[dict[str, Any]],
        scores: dict[int, dict[str, Any]],
        categories: dict[int, tuple[str, str]],
        tags: dict[int, tuple[str, str]],
        resolve_reward: Callable[[dict[str, Any]], tuple[str, str, int]],
        score_rank_rows: Iterable[dict[str, Any]],
        score_reward_rows: Iterable[dict[str, Any]],
        combo_rows: Iterable[dict[str, Any]],
    ) -> list[Song]:
        ranks_by_group: dict[int, list[dict[str, Any]]] = {}
        for entry in score_rank_rows:
            ranks_by_group.setdefault(int(entry.get("_group") or 0), []).append(entry)
        score_rewards_by_group: dict[int, dict[int, list[str]]] = {}
        for entry in score_reward_rows:
            name, _, count = resolve_reward(entry)
            score_rewards_by_group.setdefault(int(entry.get("_group") or 0), {}).setdefault(
                int(entry.get("_liveScoreRank") or 0), []
            ).append(f"{name} ×{count}" if name and count > 1 else name)
        combos_by_group: dict[int, dict[int, dict[int, list[dict[str, Any]]]]] = {}
        for entry in combo_rows:
            tier_value = entry.get("_comboRateType")
            tier = int(tier_value) if tier_value is not None else -1
            combos_by_group.setdefault(int(entry.get("_group") or 0), {}).setdefault(
                int(entry.get("_difficulty") or 0), {}
            ).setdefault(tier, []).append(entry)
        difficulty_index = {
            key: index for index, (key, _label, _field, _color) in enumerate(DIFFICULTIES)
        }
        result: list[Song] = []
        now = datetime.now()
        for row in data:
            song_id = int(row.get("_id") or 0)
            if not song_id:
                continue
            band_ids = [int(value) for value in row.get("_bandIDs") or []]
            band = self._band(band_ids[0]) if band_ids else None
            band_name = band.name if band else self.text(str(row.get("_bandNameTextID") or ""))
            jacket, jacket_fb = self.assets.jacket(str(row.get("_jacketAssetName") or ""))
            difficulties = []
            for key, label, field_name, color in DIFFICULTIES:
                score_id = int(row.get(field_name) or 0)
                score = scores.get(score_id)
                if not score:
                    continue
                difficulties.append(
                    Difficulty(
                        key=key,
                        label=label,
                        color=color,
                        level=int(score.get("_musicScoreLevel") or 0),
                        display_level=format_level(
                            score.get("_musicScoreDisplayLevel")
                            or score.get("_musicScoreLevel")
                        ),
                        notes=int(score.get("_fullComboCount") or 0),
                        score_id=score_id,
                        chart_key=str(score.get("_musicScoreTextFileName") or ""),
                    )
                )
            music_type = int(row.get("_musicType") or 0)
            type_label, _, type_color = ATTRIBUTES.get(
                music_type, ("", "", "#0072C2")
            )
            type_icon = attribute_icon(music_type)
            category_pairs = [
                categories.get(int(category_id))
                for category_id in row.get("_musicCategories") or []
            ]
            category_labels = [
                pair[0] if pair else MUSIC_CATEGORY_FALLBACK.get(int(category_id), "")
                for category_id, pair in zip(
                    row.get("_musicCategories") or [], category_pairs
                )
            ]
            category_search = [pair[1] for pair in category_pairs if pair]
            tag_pairs = [
                tags.get(int(tag_id))
                for tag_id in row.get("_bestMusicTagIDs") or []
            ]
            tag_labels = [
                pair[0]
                for pair in tag_pairs
                if pair and pair[0] and pair[0] != band_name
            ]
            tag_search = [pair[1] for pair in tag_pairs if pair]
            vocalists = self._characters(row.get("_vocalCharacterIDs") or [])
            title = self.text(str(row.get("_titleTextID") or ""))
            title_ja = self.raw_text(str(row.get("_titleTextID") or ""), "_japanese")
            phonetic = self.text(str(row.get("_phoneticTextID") or ""))
            start_at = str(row.get("_startAt") or "")
            start_dt = parse_date(start_at)
            is_new = bool(
                start_dt and now - timedelta(days=30) <= start_dt <= now + timedelta(days=30)
            )
            composer = self.text(str(row.get("_composerTextID") or ""))
            lyricist = self.text(str(row.get("_lyricistTextID") or ""))
            arranger = self.text(str(row.get("_arrangerTextID") or ""))
            mission_counts: dict[int, int] = {}
            for index in (1, 2, 3):
                mission = int(row.get(f"_gekisouMission{index}") or 0)
                if mission in GEKISOU_MISSION_LABELS:
                    mission_counts[mission] = mission_counts.get(mission, 0) + 1
            gekisou_missions = [
                GEKISOU_MISSION_LABELS[mission]
                + (f" ×{count}" if count > 1 else "")
                for mission, count in mission_counts.items()
            ]
            score_ranks: list[ScoreRank] = []
            rewards = score_rewards_by_group.get(
                int(row.get("_scoreRankRewardGroup") or 0), {}
            )
            for entry in sorted(
                ranks_by_group.get(int(row.get("_liveScoreRankGroup") or 0), []),
                key=lambda entry: int(entry.get("_liveScoreRank") or 0),
            ):
                required = int(entry.get("_requiredScore") or 0)
                # The D row always requires 0 and pays nothing; the page hides it.
                if required <= 0:
                    continue
                rank_id = int(entry.get("_liveScoreRank") or 0)
                score_ranks.append(
                    ScoreRank(
                        label=SCORE_GRADES.get(rank_id, str(rank_id)),
                        required=required,
                        battle=int(entry.get("_battleLiveRequiredScore") or 0),
                        reward="、".join(rewards.get(rank_id) or []),
                    )
                )
            combo_tiers: list[ComboTier] = []
            if difficulties:
                hardest = difficulties[-1]
                hardest_tiers = combos_by_group.get(
                    int(row.get("_comboRewardGroup") or 0), {}
                ).get(difficulty_index.get(hardest.key, -1), {})
                for tier_code, percent in COMBO_TIERS:
                    entries = hardest_tiers.get(tier_code) or []
                    if not entries:
                        continue
                    labels = []
                    for entry in entries:
                        name, _, count = resolve_reward(entry)
                        labels.append(
                            f"{name} ×{count}" if name and count > 1 else name
                        )
                    combo_tiers.append(
                        ComboTier(
                            label="全连" if percent >= 100 else f"{percent}%",
                            combo=math.ceil(hardest.notes * percent / 100),
                            reward="、".join(labels),
                        )
                    )
            search_parts = [
                title,
                title_ja,
                phonetic,
                self.all_text(str(row.get("_titleTextID") or "")),
                band_name,
                self.all_text(str(row.get("_bandNameTextID") or "")),
                band.search_names if band else "",
                str(song_id),
                composer,
                lyricist,
                arranger,
                *category_labels,
                *category_search,
                *tag_labels,
                *tag_search,
                *(character.name for character in vocalists),
                *(character.short_name for character in vocalists),
                *(character.search_names for character in vocalists),
            ]
            result.append(
                Song(
                    id=song_id,
                    title=title or f"#{song_id}",
                    title_ja=title_ja,
                    phonetic=phonetic,
                    band_id=band.id if band else (band_ids[0] if band_ids else 0),
                    band_name=band_name,
                    band_color=band.color if band else "#0072C2",
                    vocalists=vocalists,
                    type_label=type_label,
                    type_color=type_color,
                    type_icon=type_icon,
                    categories=[value for value in category_labels if value],
                    tags=[value for value in tag_labels if value],
                    difficulties=difficulties,
                    jacket_url=jacket,
                    jacket_fallback=jacket_fb,
                    composer=composer,
                    lyricist=lyricist,
                    arranger=arranger,
                    start_at=format_date(start_at),
                    start_dt=start_dt,
                    is_new=is_new,
                    sort_order=int(row.get("_sortOrder") or song_id),
                    search_text=" ".join(search_parts).lower(),
                    gekisou_missions=gekisou_missions,
                    score_ranks=score_ranks,
                    combo_tiers=combo_tiers,
                )
            )
        result.sort(key=lambda song: (song.sort_order, song.id))
        return result

    # -- member cards ----------------------------------------------------------
    def _build_cards(
        self,
        data: Iterable[dict[str, Any]],
        skills: _SkillLibrary,
        growth_tables: Mapping[str, list[dict[str, Any]]],
    ) -> list[Card]:
        result: list[Card] = []
        now = datetime.now()
        for row in data:
            card_id = int(row.get("_id") or 0)
            if not card_id:
                continue
            rarity = int(row.get("_rarity") or 0)
            if rarity not in MEMBER_RARITIES:
                continue
            character_id = int(row.get("_characterID") or 0)
            character = self.characters.get(character_id)
            band = self._band(character.band_id) if character else None
            rarity_label, rarity_color = MEMBER_RARITIES[rarity]
            card_type = int(row.get("_cardType") or 0)
            type_label, _, type_color = ATTRIBUTES.get(
                card_type, ("", "", "#0072C2")
            )
            type_icon = attribute_icon(card_type)
            title = self.text(str(row.get("_subtitleTextID") or ""))
            character_name = self.text(str(row.get("_nameTextID") or "")) or (
                character.name if character else ""
            )
            asset_id = int(row.get("_assetID") or card_id)
            art, art_fb = self.assets.card_full(asset_id)
            thumb, thumb_fb = self.assets.card_thumb(asset_id)
            face, face_fb = self.assets.character_face(character_id)
            card_skills = [
                skill
                for skill in (
                    skills.build("队长技能", "leader", row.get("_leaderSkillID")),
                    skills.build("Live 技能", "live", row.get("_liveSkillID")),
                    skills.build("激奏技能", "gekisou", row.get("_gekisouSkillID")),
                )
                if skill is not None
            ]
            performance = int(row.get("_performancePowerMax") or 0)
            technic = int(row.get("_technicPowerMax") or 0)
            visual = int(row.get("_visualPowerMax") or 0)
            growth = self._build_card_growth(
                row, performance, technic, visual, growth_tables
            )
            start_at = str(row.get("_startAt") or "")
            start_dt = parse_date(start_at)
            is_new = bool(
                start_dt and now - timedelta(days=30) <= start_dt <= now + timedelta(days=30)
            )
            search_parts = [
                title,
                self.all_text(str(row.get("_subtitleTextID") or "")),
                character_name,
                self.all_text(str(row.get("_nameTextID") or "")),
                character.name if character else "",
                character.short_name if character else "",
                character.search_names if character else "",
                band.name if band else "",
                band.search_names if band else "",
                rarity_label,
                type_label,
                str(card_id),
            ]
            result.append(
                Card(
                    id=card_id,
                    asset_id=asset_id,
                    title=title or f"卡牌 #{card_id}",
                    character_name=character_name,
                    character_id=character_id,
                    band_id=band.id if band else 0,
                    band_name=band.name if band else "",
                    band_color=band.color if band else "#0072C2",
                    rarity=rarity,
                    rarity_label=rarity_label,
                    rarity_color=rarity_color,
                    rarity_icon=rarity_icon("card", rarity),
                    type_label=type_label,
                    type_color=type_color,
                    type_icon=type_icon,
                    performance=performance,
                    technic=technic,
                    visual=visual,
                    total_power=performance + technic + visual,
                    art_url=art,
                    art_fallback=art_fb,
                    thumb_url=thumb,
                    thumb_fallback=thumb_fb,
                    face_url=face,
                    face_fallback=face_fb,
                    skills=card_skills,
                    start_at=format_date(start_at),
                    start_dt=start_dt,
                    is_new=is_new,
                    search_text=" ".join(search_parts).lower(),
                    growth=growth,
                )
            )
        # Release order, newest first: the list is a release timeline, not a
        # rarity ranking (the no-keyword browse already shows the newest 20).
        result.sort(
            key=lambda card: (card.start_dt or datetime.min, card.id), reverse=True
        )
        return result

    # -- support cards ---------------------------------------------------------
    def _build_supports(
        self,
        data: Iterable[dict[str, Any]],
        skills: _SkillLibrary,
        growth_tables: Mapping[str, list[dict[str, Any]]],
    ) -> list[SupportCard]:
        result: list[SupportCard] = []
        now = datetime.now()
        for row in data:
            support_id = int(row.get("_id") or 0)
            if not support_id:
                continue
            rarity = int(row.get("_rarity") or 0)
            if rarity not in SUPPORT_RARITIES:
                continue
            characters = self._characters(row.get("_characterIDs") or [])
            band = self._band(characters[0].band_id) if characters else None
            rarity_label, rarity_color = SUPPORT_RARITIES[rarity]
            card_type = int(row.get("_cardType") or 0)
            type_label, _, type_color = ATTRIBUTES.get(
                card_type, ("", "", "#0072C2")
            )
            type_icon = attribute_icon(card_type)
            title = self.text(str(row.get("_descriptionTextID") or ""))
            name = self.text(str(row.get("_nameTextID") or ""))
            diary = self.text(str(row.get("_diaryTextID") or ""))
            if diary.strip().lower() in {"coming soon", "敬请期待", "敬請期待"}:
                diary = ""
            asset_id = int(row.get("_assetID") or support_id)
            art, art_fb = self.assets.support_full(asset_id)
            thumb, thumb_fb = self.assets.support_thumb(asset_id)
            support_card_skills = [
                skill
                for skill in (
                    skills.build("支援技能 1", "support", row.get("_supportSkillId01")),
                    skills.build("支援技能 2", "support", row.get("_supportSkillId02")),
                    skills.build(
                        "激奏支援 1",
                        "gekisou_support",
                        row.get("_gekisouSupportSkillId01"),
                    ),
                    skills.build(
                        "激奏支援 2",
                        "gekisou_support",
                        row.get("_gekisouSupportSkillId02"),
                    ),
                )
                if skill is not None
            ]
            performance = int(row.get("_performancePowerMax") or 0)
            technic = int(row.get("_technicPowerMax") or 0)
            visual = int(row.get("_visualPowerMax") or 0)
            growth = self._build_support_growth(row, growth_tables)
            start_at = str(row.get("_startAt") or "")
            start_dt = parse_date(start_at)
            is_new = bool(
                start_dt and now - timedelta(days=30) <= start_dt <= now + timedelta(days=30)
            )
            search_parts = [
                title,
                self.all_text(str(row.get("_descriptionTextID") or "")),
                name,
                self.all_text(str(row.get("_nameTextID") or "")),
                *(character.name for character in characters),
                *(character.short_name for character in characters),
                *(character.search_names for character in characters),
                band.name if band else "",
                band.search_names if band else "",
                rarity_label,
                type_label,
                str(support_id),
            ]
            result.append(
                SupportCard(
                    id=support_id,
                    asset_id=asset_id,
                    title=title or f"支援卡 #{support_id}",
                    name=name,
                    characters=characters,
                    band_id=band.id if band else 0,
                    band_name=band.name if band else "",
                    band_color=band.color if band else "#0072C2",
                    rarity=rarity,
                    rarity_label=rarity_label,
                    rarity_color=rarity_color,
                    rarity_icon=rarity_icon("support", rarity),
                    type_label=type_label,
                    type_color=type_color,
                    type_icon=type_icon,
                    performance=performance,
                    technic=technic,
                    visual=visual,
                    total_power=performance + technic + visual,
                    art_url=art,
                    art_fallback=art_fb,
                    thumb_url=thumb,
                    thumb_fallback=thumb_fb,
                    skills=support_card_skills,
                    start_at=format_date(start_at),
                    start_dt=start_dt,
                    is_new=is_new,
                    search_text=" ".join(search_parts).lower(),
                    growth=growth,
                    diary=diary,
                )
            )
        # Release order, newest first: the list is a release timeline, not a
        # rarity ranking (the no-keyword browse already shows the newest 20).
        result.sort(
            key=lambda card: (card.start_dt or datetime.min, card.id), reverse=True
        )
        return result

    # -- stamps ----------------------------------------------------------------
    def _build_stamps(self, data: Iterable[dict[str, Any]]) -> list[Stamp]:
        result: list[Stamp] = []
        for row in data:
            stamp_id = int(row.get("_id") or 0)
            if not stamp_id:
                continue
            characters = self._characters(row.get("_characterIds") or [])
            band = self._band(characters[0].band_id) if characters else None
            name = self.text(str(row.get("_nameTextId") or ""))
            image, image_fb = self.assets.stamp(str(row.get("_stampAsset") or ""))
            start_at = str(row.get("_startAt") or "")
            search_parts = [
                name,
                self.all_text(str(row.get("_nameTextId") or "")),
                *(character.name for character in characters),
                *(character.short_name for character in characters),
                *(character.search_names for character in characters),
                band.name if band else "",
                band.search_names if band else "",
                str(stamp_id),
            ]
            result.append(
                Stamp(
                    id=stamp_id,
                    name=name or f"贴纸 #{stamp_id}",
                    characters=characters,
                    band_id=band.id if band else 0,
                    band_name=band.name if band else "",
                    band_color=band.color if band else "#0072C2",
                    image_url=image,
                    image_fallback=image_fb,
                    category=int(row.get("_stampCategory") or 0),
                    start_at=format_date(start_at),
                    start_dt=parse_date(start_at),
                    search_text=" ".join(search_parts).lower(),
                )
            )
        result.sort(key=lambda stamp: stamp.id)
        return result

    # -- loading comics --------------------------------------------------------
    def _build_comics(self, data: Iterable[dict[str, Any]]) -> list[Comic]:
        result: list[Comic] = []
        for row in data:
            comic_id = int(row.get("_id") or 0)
            if not comic_id:
                continue
            characters = self._characters(row.get("_characterIds") or [])
            band = self._band(characters[0].band_id) if characters else None
            image, image_fb = self.assets.comic(str(row.get("_imageAsset") or ""))
            names = "、".join(character.name for character in characters[:3])
            name = f"四格漫画 #{comic_id}"
            if names:
                name = f"{name} · {names}"
            start_at = str(row.get("_startAt") or "")
            search_parts = [
                name,
                str(comic_id),
                *(character.name for character in characters),
                *(character.short_name for character in characters),
                *(character.search_names for character in characters),
                band.name if band else "",
                band.search_names if band else "",
            ]
            result.append(
                Comic(
                    id=comic_id,
                    name=name,
                    characters=characters,
                    band_id=band.id if band else 0,
                    band_name=band.name if band else "",
                    band_color=band.color if band else "#0072C2",
                    image_url=image,
                    image_fallback=image_fb,
                    order=int(row.get("_order") or comic_id),
                    start_at=format_date(start_at),
                    start_dt=parse_date(start_at),
                    search_text=" ".join(search_parts).lower(),
                )
            )
        result.sort(key=lambda comic: (comic.order, comic.id))
        return result

    # -- rewards ---------------------------------------------------------------
    def _make_reward_resolver(
        self,
        item_rows: Iterable[dict[str, Any]],
        cards: list[Card],
        supports: list[SupportCard],
        stamps: list[Stamp],
    ) -> Callable[[dict[str, Any]], tuple[str, str, int]]:
        """Resolves one MasterReward-shaped row into (name, icon, count).

        Shared by the event rewards and the song live rules (score / combo
        rewards), which carry the same ``_resourceType/_resourceId/_resourceCount``
        fields.
        """
        item_by_id = {
            int(row.get("_id") or 0): row
            for row in item_rows
            if row.get("_id") is not None
        }
        card_by_id = {card.id: card for card in cards}
        support_by_id = {card.id: card for card in supports}
        stamp_by_id = {stamp.id: stamp for stamp in stamps}

        def resolve(reward: dict[str, Any]) -> tuple[str, str, int]:
            resource_type = int(reward.get("_resourceType") or 0)
            resource_id = int(reward.get("_resourceId") or 0)
            count = int(reward.get("_resourceCount") or 0)
            name = ""
            icon = ""
            if resource_type == 1:
                item = item_by_id.get(resource_id)
                if item:
                    name = self.text(str(item.get("_nameTextId") or ""))
                    image_path = str(item.get("_imagePath") or "")
                    if image_path:
                        icon = self.assets.item(image_path)[0]
            elif resource_type == 2:
                card = card_by_id.get(resource_id)
                if card:
                    name = (
                        f"{card.character_name} · {card.title}"
                        if card.character_name
                        else card.title
                    )
                    icon = card.thumb_url
            elif resource_type == 3:
                card = support_by_id.get(resource_id)
                if card:
                    people = "、".join(
                        character.name for character in card.characters[:2]
                    )
                    name = f"{people} · {card.title}" if people else card.title
                    icon = card.thumb_url
            elif resource_type == 9:
                stamp = stamp_by_id.get(resource_id)
                if stamp:
                    name = stamp.name
                    icon = stamp.image_url
            if not name:
                name = f"奖励 #{resource_id}" if resource_id else "奖励"
            return name, icon, count

        return resolve

    # -- events ----------------------------------------------------------------
    def _build_events(
        self,
        data: Iterable[dict[str, Any]],
        effects: Iterable[dict[str, Any]],
        achievements: Iterable[dict[str, Any]],
        loop_rewards: Iterable[dict[str, Any]],
        reward_table: Iterable[dict[str, Any]],
        item_rows: Iterable[dict[str, Any]],
        story_rows: Iterable[dict[str, Any]],
        live_points: Iterable[dict[str, Any]],
        live_rewards: Iterable[dict[str, Any]],
        challenge_points: Iterable[dict[str, Any]],
        challenge_rewards: Iterable[dict[str, Any]],
        resolve_reward: Callable[[dict[str, Any]], tuple[str, str, int]],
        songs: list[Song],
        cards: list[Card],
        supports: list[SupportCard],
    ) -> list[Event]:
        item_by_id = {
            int(row.get("_id") or 0): row
            for row in item_rows
            if row.get("_id") is not None
        }
        reward_by_id = {
            int(row.get("_id") or 0): row
            for row in reward_table
            if row.get("_id") is not None
        }
        story_by_id = {
            int(row.get("_id") or 0): row
            for row in story_rows
            if row.get("_id") is not None
        }
        song_by_id = {song.id: song for song in songs}
        card_by_id = {card.id: card for card in cards}
        support_by_id = {card.id: card for card in supports}

        effects_by_event: dict[int, list[dict[str, Any]]] = {}
        for row in effects:
            effects_by_event.setdefault(int(row.get("_eventId") or 0), []).append(row)
        achievements_by_event: dict[int, list[dict[str, Any]]] = {}
        for row in achievements:
            achievements_by_event.setdefault(int(row.get("_eventId") or 0), []).append(row)
        loops_by_event: dict[int, list[dict[str, Any]]] = {}
        for row in loop_rewards:
            loops_by_event.setdefault(int(row.get("_eventId") or 0), []).append(row)

        def reward_text(reward: dict[str, Any]) -> str:
            name, _, count = resolve_reward(reward)
            return f"{name} ×{count}" if count > 1 else name

        def grade_rows(
            point_rows: Iterable[dict[str, Any]],
            reward_rows: Iterable[dict[str, Any]],
            group: int,
            reward_group: int,
        ) -> list[EventGradeReward]:
            points = {
                int(row.get("_scoreRank") or 0): int(row.get("_value") or 0)
                for row in point_rows
                if int(row.get("_group") or 0) == group
            }
            rewards: dict[int, tuple[str, int]] = {}
            for row in reward_rows:
                if int(row.get("_eventGroup") or 0) != reward_group:
                    continue
                rank = int(row.get("_scoreRank") or 0)
                name, _, count = resolve_reward(row)
                rewards[rank] = (name, count)
            return [
                EventGradeReward(
                    label=SCORE_GRADES.get(rank, str(rank)),
                    points=points[rank],
                    item_name=rewards.get(rank, ("", 0))[0],
                    item_count=rewards.get(rank, ("", 0))[1],
                )
                for rank in sorted(points)
            ]

        result: list[Event] = []
        for row in data:
            event_id = int(row.get("_id") or 0)
            if not event_id:
                continue

            name_id = str(row.get("_nameTextId") or "")
            name = self.text(name_id) or f"活动 #{event_id}"
            event_type = int(row.get("_eventType") or 0)
            type_label = EVENT_TYPES.get(event_type, "活动")

            start_at = str(row.get("_startAt") or "")
            end_at = str(row.get("_endAt") or "")
            display_end_at = str(row.get("_displayEndAt") or "")
            start_dt = parse_date(start_at)
            end_dt = parse_date(end_at)
            display_end_dt = parse_date(display_end_at)

            logo, logo_fb = self.assets.event_logo(str(row.get("_logoAsset") or ""))
            background, background_fb = self.assets.event_background(
                str(row.get("_backgroundAsset") or "")
            )
            banner, banner_fb = self.assets.event_banner(
                str(row.get("_bannerAsset") or "")
            )
            if not banner:
                banner, banner_fb = background, background_fb

            event_item = item_by_id.get(int(row.get("_eventItemId") or 0)) or {}
            item_name = self.text(str(event_item.get("_nameTextId") or ""))
            item_icon = self.assets.item(str(event_item.get("_imagePath") or ""))[0]

            song = song_by_id.get(int(row.get("_musicId") or 0))

            story = story_by_id.get(int(row.get("_storyChapterId") or 0)) or {}
            story_name_id = str(story.get("_nameTextId") or "")
            story_name = self.text(story_name_id)
            story_text = self.text(str(story.get("_descriptionTextId") or ""))
            # Some chapters repeat the title as the first description line.
            if story_name and story_text.startswith(story_name):
                story_text = story_text[len(story_name) :].lstrip(" \n")

            member_bonuses: list[EventBonus] = []
            support_bonuses: list[EventBonus] = []
            rule_bonuses: list[EventBonus] = []
            seen_member: set[int] = set()
            seen_support: set[int] = set()
            seen_rules: set[tuple[str, int]] = set()
            for effect in effects_by_event.get(event_id, []):
                values = [
                    round(int(effect.get(f"_rank{rank}EffectValue") or 0) / 100, 1)
                    for rank in range(1, 6)
                ]
                if not any(values):
                    continue
                member_id = int(effect.get("_memberCardId") or 0)
                support_id = int(effect.get("_supportCardId") or 0)
                band_id = int(effect.get("_bandId") or 0)
                card_type = int(effect.get("_cardType") or 0)
                if member_id and member_id not in seen_member:
                    seen_member.add(member_id)
                    card = card_by_id.get(member_id)
                    if card:
                        sub = " · ".join(
                            part
                            for part in (card.character_name, card.band_name)
                            if part
                        )
                        member_bonuses.append(
                            EventBonus(
                                kind="member",
                                name=card.title,
                                sub=sub,
                                values=values,
                                color=card.band_color,
                                icon=card.type_icon,
                                image_url=card.thumb_url,
                                image_fallback=card.thumb_fallback,
                                detail_id=card.id,
                            )
                        )
                    else:
                        member_bonuses.append(
                            EventBonus(
                                kind="member",
                                name=f"成员卡 #{member_id}",
                                sub="成员",
                                values=values,
                            )
                        )
                elif support_id and support_id not in seen_support:
                    seen_support.add(support_id)
                    card = support_by_id.get(support_id)
                    if card:
                        people = "、".join(
                            character.name for character in card.characters[:2]
                        )
                        sub = " · ".join(
                            part for part in (people, card.band_name) if part
                        )
                        support_bonuses.append(
                            EventBonus(
                                kind="support",
                                name=card.title,
                                sub=sub,
                                values=values,
                                color=card.band_color,
                                icon=card.type_icon,
                                image_url=card.thumb_url,
                                image_fallback=card.thumb_fallback,
                                detail_id=card.id,
                            )
                        )
                    else:
                        support_bonuses.append(
                            EventBonus(
                                kind="support",
                                name=f"支援卡 #{support_id}",
                                sub="支援",
                                values=values,
                            )
                        )
                elif band_id and ("band", band_id) not in seen_rules:
                    seen_rules.add(("band", band_id))
                    band = self.bands.get(band_id)
                    rule_bonuses.append(
                        EventBonus(
                            kind="band",
                            name=band.name if band else f"乐队 #{band_id}",
                            sub="乐队加成",
                            values=values,
                            color=band.color if band else "#0072C2",
                        )
                    )
                elif card_type and ("attribute", card_type) not in seen_rules:
                    seen_rules.add(("attribute", card_type))
                    attribute = ATTRIBUTES.get(card_type)
                    rule_bonuses.append(
                        EventBonus(
                            kind="attribute",
                            name=attribute[0] if attribute else f"属性 {card_type}",
                            sub="属性加成",
                            values=values,
                            color=attribute[2] if attribute else "#0072C2",
                            icon=attribute_icon(card_type),
                        )
                    )

            point_rewards: list[EventReward] = []
            for achievement in sorted(
                achievements_by_event.get(event_id, []),
                key=lambda entry: int(entry.get("_eventPoint") or 0),
            ):
                labels: list[str] = []
                icons: list[str] = []
                for reward_id in achievement.get("_rewardIds") or []:
                    reward = reward_by_id.get(int(reward_id))
                    if not reward:
                        continue
                    name_text, icon, count = resolve_reward(reward)
                    labels.append(
                        f"{name_text} ×{count}" if count > 1 else name_text
                    )
                    if icon:
                        icons.append(icon)
                if labels:
                    point_rewards.append(
                        EventReward(
                            point=int(achievement.get("_eventPoint") or 0),
                            labels=labels,
                            icons=icons,
                        )
                    )

            loop_label = ""
            for loop in loops_by_event.get(event_id, []):
                parts = [
                    reward_text(reward_by_id[int(reward_id)])
                    for reward_id in loop.get("_rewardIds") or []
                    if int(reward_id) in reward_by_id
                ]
                if not parts:
                    continue
                start = int(loop.get("_loopStartEventPoint") or 0)
                step = int(loop.get("_loopEventPoint") or 0)
                loop_label = f"{start:,} pt 后每 {step:,} pt：{'、'.join(parts)}"
                break

            search_parts = [
                name,
                self.all_text(name_id),
                str(event_id),
                type_label,
                item_name,
                self.all_text(str(event_item.get("_nameTextId") or "")),
                story_name,
                self.all_text(story_name_id),
                story_text,
                start_at,
                end_at,
                *(bonus.name for bonus in member_bonuses),
                *(bonus.sub for bonus in member_bonuses),
                *(bonus.name for bonus in support_bonuses),
                *(bonus.sub for bonus in support_bonuses),
                *(bonus.name for bonus in rule_bonuses),
            ]
            if song:
                search_parts.extend((song.title, song.search_text))

            result.append(
                Event(
                    id=event_id,
                    name=name,
                    event_type=event_type,
                    type_label=type_label,
                    start_at=format_date(start_at),
                    end_at=format_date(end_at),
                    display_end_at=format_date(display_end_at),
                    start_dt=start_dt,
                    end_dt=end_dt,
                    display_end_dt=display_end_dt,
                    logo_url=logo,
                    logo_fallback=logo_fb,
                    background_url=background,
                    background_fallback=background_fb,
                    banner_url=banner,
                    banner_fallback=banner_fb,
                    item_name=item_name,
                    item_icon=item_icon,
                    song=song,
                    story_name=story_name,
                    story_text=story_text,
                    member_bonuses=member_bonuses,
                    support_bonuses=support_bonuses,
                    rule_bonuses=rule_bonuses,
                    point_rewards=point_rewards,
                    loop_label=loop_label,
                    live_rewards=grade_rows(
                        live_points,
                        live_rewards,
                        int(row.get("_liveEventPointGroup") or 0),
                        int(row.get("_liveEventRewardGroup") or 0),
                    ),
                    challenge_rewards=grade_rows(
                        challenge_points,
                        challenge_rewards,
                        int(row.get("_challengeLiveEventPointGroup") or 0),
                        int(row.get("_challengeLiveEventRewardGroup") or 0),
                    ),
                    search_text=" ".join(search_parts).lower(),
                )
            )

        result.sort(
            key=lambda event: (event.start_dt or datetime.min, event.id),
            reverse=True,
        )
        return result

    def _build_card_growth(
        self,
        row: Mapping[str, Any],
        performance: int,
        technic: int,
        visual: int,
        tables: Mapping[str, list[dict[str, Any]]],
    ) -> CardGrowth | None:
        """Max level / training / awakening and the resulting parameters."""
        level_group = int(row.get("_memberCardLevelGroup") or 0)
        curve: dict[int, tuple[int, int, int]] = {
            int(entry.get("_level") or 0): (
                int(entry.get("_performanceRate") or 0),
                int(entry.get("_technicRate") or 0),
                int(entry.get("_visualRate") or 0),
            )
            for entry in tables.get("levels", [])
            if int(entry.get("_group") or 0) == level_group
        }
        if not curve:
            return None
        curve_max = max(curve)

        awake_group = int(row.get("_memberCardAwakeGroup") or 0)
        awake_rows = sorted(
            (
                entry
                for entry in tables.get("awakes", [])
                if int(entry.get("_group") or 0) == awake_group
            ),
            key=lambda entry: int(entry.get("_awakeCount") or 0),
        )
        if awake_rows:
            last_awake = awake_rows[-1]
            max_awake = int(last_awake.get("_awakeCount") or 0)
            awake_rates = (
                int(last_awake.get("_performanceRate") or 0),
                int(last_awake.get("_technicRate") or 0),
                int(last_awake.get("_visualRate") or 0),
            )
            limit = next(
                (
                    int(entry.get("_limitLevel") or 0)
                    for entry in tables.get("level_limits", [])
                    if int(entry.get("_rarity") or 0) == int(row.get("_rarity") or 0)
                    and int(entry.get("_awakeCount") or 0) == max_awake
                ),
                curve_max,
            )
            max_level = min(limit or curve_max, curve_max)
        else:
            max_awake = 0
            max_level = curve_max
            awake_rates = (0, 0, 0)

        rank_group = int(row.get("_memberCardRankGroup") or 0)
        rank_rows = sorted(
            (
                entry
                for entry in tables.get("ranks", [])
                if int(entry.get("_group") or 0) == rank_group
            ),
            key=lambda entry: int(entry.get("_rank") or 0),
        )
        if rank_rows:
            last_rank = rank_rows[-1]
            max_rank = int(last_rank.get("_rank") or 0)
            leader_skill_level = int(last_rank.get("_leaderSkillLevel") or 1)
            rank_rates = (
                int(last_rank.get("_performanceRate") or 0),
                int(last_rank.get("_technicRate") or 0),
                int(last_rank.get("_visualRate") or 0),
            )
        else:
            max_rank = 0
            leader_skill_level = 1
            rank_rates = (0, 0, 0)

        level_rates = curve.get(max_level) or curve[curve_max]
        base = (performance, technic, visual)
        final = tuple(
            _scale_by_rate(base[index], level_rates[index])
            + _scale_by_rate_factor(base[index], awake_rates[index])
            + _scale_by_rate(base[index], rank_rates[index])
            for index in range(3)
        )
        return CardGrowth(
            max_level=max_level,
            max_awake=max_awake,
            max_rank=max_rank,
            leader_skill_level=leader_skill_level,
            performance=final[0],
            technic=final[1],
            visual=final[2],
            total_power=sum(final),
        )

    @staticmethod
    def _build_support_growth(
        row: Mapping[str, Any],
        tables: Mapping[str, list[dict[str, Any]]],
    ) -> SupportGrowth | None:
        level_group = int(row.get("_supportCardLevelGroup") or 0)
        levels = [
            int(entry.get("_level") or 0)
            for entry in tables.get("support_levels", [])
            if int(entry.get("_group") or 0) == level_group
        ]
        if not levels:
            return None
        curve_max = max(levels)
        rank_group = int(row.get("_supportCardRankGroup") or 0)
        rank_rows = sorted(
            (
                entry
                for entry in tables.get("support_ranks", [])
                if int(entry.get("_group") or 0) == rank_group
            ),
            key=lambda entry: int(entry.get("_rank") or 0),
        )
        if not rank_rows:
            return SupportGrowth(
                max_level=curve_max,
                max_rank=0,
                support_skill_level=1,
                gekisou_skill_level=1,
            )
        last_rank = rank_rows[-1]
        return SupportGrowth(
            max_level=min(int(last_rank.get("_limitLevel") or curve_max), curve_max),
            max_rank=int(last_rank.get("_rank") or 0),
            support_skill_level=int(last_rank.get("_supportSkill01Level") or 1),
            gekisou_skill_level=int(last_rank.get("_gekisouSupportSkill01Level") or 1),
        )


def catalog_stats(dataset: Dataset) -> dict[str, Any]:
    """Aggregates for the 查图鉴 landing page."""
    bands = []
    for band in sorted(dataset.bands.values(), key=lambda value: value.id):
        bands.append(
            {
                "id": band.id,
                "name": band.name,
                "color": band.color,
                "songs": sum(1 for song in dataset.songs if song.band_id == band.id),
                "cards": sum(1 for card in dataset.cards if card.band_id == band.id),
                "supports": sum(
                    1 for card in dataset.supports if card.band_id == band.id
                ),
                "stamps": sum(1 for stamp in dataset.stamps if stamp.band_id == band.id),
                "comics": sum(1 for comic in dataset.comics if comic.band_id == band.id),
            }
        )
    max_songs = max((band["songs"] for band in bands), default=1) or 1
    for band in bands:
        band["song_ratio"] = round(band["songs"] / max_songs * 100, 1)

    member_rarities = []
    for rarity, (label, color) in MEMBER_RARITIES.items():
        member_rarities.append(
            {
                "label": label,
                "color": color,
                "count": sum(1 for card in dataset.cards if card.rarity == rarity),
            }
        )
    support_rarities = []
    for rarity, (label, color) in SUPPORT_RARITIES.items():
        support_rarities.append(
            {
                "label": label,
                "color": color,
                "count": sum(1 for card in dataset.supports if card.rarity == rarity),
            }
        )

    dated_songs = [song for song in dataset.songs if song.start_dt]
    dated_cards = [card for card in dataset.cards if card.start_dt]
    latest_songs = sorted(dated_songs, key=lambda song: song.start_dt, reverse=True)[:6]
    latest_cards = sorted(dated_cards, key=lambda card: card.start_dt, reverse=True)[:6]

    return {
        "counts": {
            "songs": len(dataset.songs),
            "cards": len(dataset.cards),
            "supports": len(dataset.supports),
            "stamps": len(dataset.stamps),
            "comics": len(dataset.comics),
            "characters": len(dataset.characters),
            "bands": len(dataset.bands),
        },
        "bands": bands,
        "member_rarities": member_rarities,
        "support_rarities": support_rarities,
        "latest_songs": latest_songs,
        "latest_cards": latest_cards,
        "latest_stamps": sorted(
            [stamp for stamp in dataset.stamps if stamp.start_dt],
            key=lambda stamp: stamp.start_dt,
            reverse=True,
        )[:6],
        "latest_comics": sorted(
            [comic for comic in dataset.comics if comic.start_dt],
            key=lambda comic: comic.start_dt,
            reverse=True,
        )[:6],
    }
