"""Static tables shared by the Our Notes query plugin."""

from __future__ import annotations

from dataclasses import dataclass

PLUGIN_NAME = "astrbot_plugin_bdon"
# cn is the default prefix: it reads the same hk-tw-mo tables as tw/hk but shows
# Simplified Chinese text, which is what most of the bot's users read.
DEFAULT_PREFIX = "cn"

# Every list renders as one image: pagination was explicitly not wanted. The
# canvas is fixed so rows line up exactly and no page can grow wider than the
# viewport (which used to leave a band of blank background on the right).
LIST_PAGE_SIZE = 10_000
LIST_WIDTH = 1080
DETAIL_WIDTH = 1080
# Event pages are denser than a card detail; a narrower canvas gives the same
# text a larger on-screen size when the chat client scales the image to fit.
EVENT_WIDTH = 900
# The sticker list keeps its original dense seven-column canvas.
STAMP_LIST_WIDTH = 900
LIST_SCALE = "normal"
LIST_JPEG_QUALITY = 90

# Cards and songs number in the dozens; a no-keyword browse shows only the
# newest entries instead of a page several screens tall. Searching still covers
# the whole table.
BROWSE_LIMIT = 20
BROWSE_KINDS = {"song", "card"}


def list_columns(kind: str, count: int) -> int:
    """Columns for a result grid.

    Four columns is the cap for songs/cards: at 1080px that keeps each card
    around 250px wide, which leaves room for the larger chat-readable type.
    Support/comic art is 16:9 or square with more internal detail, so it caps
    at four as well. Small result sets use fewer columns, which keeps the cards
    the same physical size instead of stretching two results across the full
    canvas. Events are wide hero cards, so they use at most two columns.
    """
    if count <= 0:
        return 1
    if kind == "event":
        return min(2, max(1, count))
    if kind in {"support", "comic"}:
        return min(4, max(1, count))
    if count <= 3:
        return count
    if count <= 6:
        return 3
    return 4


# cn shows Simplified Chinese. tw/hk/kr/en show Traditional Chinese from the
# same MasterText rows; jp's rows only carry Japanese, so that prefix naturally
# falls through to Japanese. Switching script never needs another download.
SCRIPT_TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "simplified": (
        "_simplifiedChinese",
        "_traditionalChinese",
        "_japanese",
        "_english",
        "_korean",
    ),
    "traditional": (
        "_traditionalChinese",
        "_simplifiedChinese",
        "_japanese",
        "_english",
        "_korean",
    ),
}
SCRIPT_LABELS: dict[str, str] = {"simplified": "简体", "traditional": "繁体"}


@dataclass(frozen=True)
class RegionProfile:
    """One game server: where its data lives and how its assets are addressed."""

    key: str
    data_region: str
    prefixes: tuple[str, ...]
    label: str
    asset_prefix: str
    asset_language: str


REGIONS: dict[str, RegionProfile] = {
    "tw": RegionProfile(
        key="tw",
        data_region="hk-tw-mo",
        prefixes=("cn", "tw", "hk", "港台", "繁中", "台服", "港服", "港澳台"),
        label="港台服",
        asset_prefix="",
        asset_language="zh-Hant",
    ),
    "jp": RegionProfile(
        key="jp",
        data_region="jp",
        prefixes=("jp", "日服", "日"),
        label="日服",
        asset_prefix="jp",
        asset_language="ja",
    ),
    "kr": RegionProfile(
        key="kr",
        data_region="kr",
        prefixes=("kr", "韩服", "韓服", "韩", "韓"),
        label="韩服",
        asset_prefix="kr",
        asset_language="ko",
    ),
    "en": RegionProfile(
        key="en",
        data_region="en",
        prefixes=("en", "国际服", "國際服", "全球服", "global"),
        label="国际服",
        asset_prefix="en",
        asset_language="en",
    ),
}

REGION_ALIASES: dict[str, str] = {
    alias.lower(): key for key, profile in REGIONS.items() for alias in profile.prefixes
}
# cn is the only prefix that selects Simplified Chinese; tw/hk are the same
# server with its native Traditional Chinese text.
PREFIX_SCRIPTS: dict[str, str] = {
    alias: ("simplified" if alias == DEFAULT_PREFIX else "traditional")
    for alias in REGION_ALIASES
}


def resolve_region(prefix: str | None) -> tuple[str, str]:
    """Maps a command prefix to ``(server key, script)``.

    An empty or unknown prefix falls back to cn, i.e. the 港台服 tables rendered
    in Simplified Chinese.
    """
    key = (prefix or DEFAULT_PREFIX).strip().lower()
    if key not in REGION_ALIASES:
        key = DEFAULT_PREFIX
    return REGION_ALIASES[key], PREFIX_SCRIPTS[key]


# Attribute (card type / music type) labels and accents used by the game's UI.
ATTRIBUTES: dict[int, tuple[str, str, str]] = {
    1: ("红赤", "Red", "#E05A6B"),
    2: ("绀碧", "Blue", "#3D82D6"),
    3: ("翡翠", "Green", "#2FA46B"),
    4: ("山吹", "Yellow", "#D9A21B"),
    5: ("紫苑", "Purple", "#8E6BD6"),
}

MEMBER_RARITIES: dict[int, tuple[str, str]] = {
    2: ("R", "#7FA8C9"),
    3: ("SR", "#D9A441"),
    4: ("SSR", "#B86BD8"),
    20: ("生日", "#E05A7A"),
}

SUPPORT_RARITIES: dict[int, tuple[str, str]] = {
    2: ("R", "#7FA8C9"),
    3: ("SR", "#D9A441"),
    4: ("SSR", "#B86BD8"),
    10: ("特殊", "#3DBFA0"),
}

DIFFICULTIES: tuple[tuple[str, str, str, str], ...] = (
    ("easy", "EZ", "_easyID", "#2AA9C9"),
    ("normal", "NM", "_normalID", "#2FA46B"),
    ("hard", "HD", "_hardID", "#D9A21B"),
    ("expert", "EX", "_expertID", "#D9536F"),
)

# MasterLiveMusicComboReward._comboRateType: the combo share each tier pays on,
# in display order. A tier's combo count is ceil(chart notes × share).
COMBO_TIERS: tuple[tuple[int, int], ...] = ((0, 25), (1, 50), (2, 75), (3, 100))

# Tokens accepted as the difficulty argument of 查谱面. Stand-alone digits 1-4
# work too ("查谱面 1 ex" and "查谱面 ex 1" both pick expert).
CHART_DIFFICULTY_WORDS: dict[str, str] = {
    "ez": "easy", "easy": "easy", "简单": "easy", "簡單": "easy",
    "nm": "normal", "normal": "normal", "普通": "normal",
    "hd": "hard", "hard": "hard", "困难": "hard", "困難": "hard",
    "ex": "expert", "expert": "expert", "专家": "expert", "專家": "expert",
    "1": "easy", "2": "normal", "3": "hard", "4": "expert",
}
DIFFICULTY_NAMES: dict[str, str] = {
    "easy": "简单 EASY", "normal": "普通 NORMAL", "hard": "困难 HARD",
    "expert": "专家 EXPERT",
}

MUSIC_CATEGORY_FALLBACK: dict[int, str] = {
    1: "原创曲",
    2: "翻唱曲",
    3: "虚拟歌手",
    4: "其他",
}

# MasterEvent._eventType. Only the challenge-live format ships today.
EVENT_TYPES: dict[int, str] = {1: "挑战演出"}

# MasterLiveEvent*/MasterChallengeLiveEvent* score buckets.
SCORE_GRADES: dict[int, str] = {
    2: "D",
    3: "C",
    4: "B",
    5: "A",
    6: "S",
    7: "SS",
}

# Tables the plugin needs. Missing optional tables only remove detail fields.
REQUIRED_TABLES: tuple[str, ...] = (
    "MasterText",
    "MasterLiveMusic",
    "MasterLiveMusicScore",
    "MasterMemberCard",
    "MasterSupportCard",
    "MasterStamp",
    "MasterLoadingComics",
    "MasterCharacter",
    "MasterBand",
    "MasterEvent",
)

OPTIONAL_TABLES: tuple[str, ...] = (
    "MasterTag",
    "MasterLiveMusicCategory",
    "MasterLeaderSkill",
    "MasterLiveSkill",
    "MasterSupportSkill",
    "MasterGekisouSkill",
    "MasterGekisouSupportSkill",
    # Skill level tables: descriptions carry placeholders that the effect rows
    # fill in, so all five levels can be shown with their real values.
    "MasterLeaderSkillEffect",
    "MasterLiveSkillEffect",
    "MasterGekisouSkillEffect",
    "MasterSupportSkillEffect",
    "MasterGekisouSupportSkillEffect",
    "MasterSkillIcon",
    "MasterSkillConditionSet",
    "MasterSkillCondition",
    "MasterSkillCumulativeCondition",
    "MasterSkillTarget",
    # Card growth: level caps, training (awake) and awakening (rank) rates,
    # plus support-card limit-break steps and their skill levels.
    "MasterMemberCardLevel",
    "MasterMemberCardLevelLimit",
    "MasterMemberCardAwake",
    "MasterMemberCardRank",
    "MasterSupportCardLevel",
    "MasterSupportCardRank",
    "MasterEventEffect",
    "MasterEventAchievementReward",
    "MasterEventAchievementLoopReward",
    "MasterReward",
    "MasterItem",
    "MasterStoryChapter",
    "MasterLiveEventPoint",
    "MasterLiveEventReward",
    "MasterChallengeLiveEventPoint",
    "MasterChallengeLiveEventReward",
    # Song live rules: the per-song score-rank thresholds plus the shared
    # score / combo reward ladders the song row points at.
    "MasterLiveScoreRank",
    "MasterLiveMusicScoreReward",
    "MasterLiveMusicComboReward",
)

ALL_TABLES: tuple[str, ...] = REQUIRED_TABLES + OPTIONAL_TABLES

# Game command aliases. The first entry is the canonical name used in the UI.
COMMAND_ALIASES: dict[str, str] = {
    "查歌": "song",
    "查曲": "song",
    "查歌曲": "song",
    "歌曲": "song",
    "song": "song",
    "music": "song",
    "看歌": "song",
    "看歌曲": "song",
    "查卡": "card",
    "查牌": "card",
    "查卡牌": "card",
    "查卡面": "card",
    "看卡": "card",
    "看卡面": "card",
    "查支援卡": "support",
    "查支援": "support",
    "查snap": "support",
    "看支援卡": "support",
    "查活动": "event",
    "查活動": "event",
    "活动": "event",
    "活動": "event",
    "event": "event",
    "活动一览": "event_list",
    "活動一覽": "event_list",
    "活动列表": "event_list",
    "活動列表": "event_list",
    "查贴纸": "stamp",
    "查貼紙": "stamp",
    "查貼圖": "stamp",
    "查表情": "stamp",
    "查表情包": "stamp",
    "查漫画": "comic",
    "查漫畫": "comic",
    "查四格": "comic",
    "查曲绘": "jacket",
    "查曲繪": "jacket",
    "曲绘": "jacket",
    "曲繪": "jacket",
    "查谱面": "chart",
    "查譜面": "chart",
    "查谱": "chart",
    "查譜": "chart",
    "谱面": "chart",
    "譜面": "chart",
    "谱面预览": "chart",
    "譜面預覽": "chart",
    "谱面查询": "chart",
    "譜面查詢": "chart",
    # 铺 is the common typo of 谱; accept it so mistyped queries still work.
    "铺面": "chart",
    "鋪面": "chart",
    "查铺面": "chart",
    "查鋪面": "chart",
    "铺面查询": "chart",
    "鋪面查詢": "chart",
    "铺面预览": "chart",
    "鋪面預覽": "chart",
    "查图鉴": "catalog",
    "查圖鑑": "catalog",
    "图鉴": "catalog",
    "圖鑑": "catalog",
    "帮助": "help",
    "說明": "help",
    "说明": "help",
    "help": "help",
}

# Commands that return the raw card art instead of a rendered page. The value
# is the dataset kind the command reads from.
ART_COMMANDS: dict[str, str] = {
    "card": "card",
    "scard": "support",
    "卡面": "card",
    "支援卡面": "support",
}

KIND_LABELS: dict[str, str] = {
    "song": "歌曲",
    "card": "卡牌",
    "support": "支援卡",
    "stamp": "贴纸",
    "comic": "漫画",
    "chart": "谱面",
    "event": "活动",
    "event_list": "活动一览",
    "catalog": "图鉴",
    "help": "帮助",
}

KIND_COMMANDS: dict[str, str] = {
    "song": "查歌",
    "card": "查卡",
    "support": "查支援卡",
    "stamp": "查贴纸",
    "comic": "查漫画",
    "chart": "查谱面",
    "event": "查活动",
    "event_list": "活动一览",
    "catalog": "查图鉴",
    "help": "帮助",
}

SYNC_COMMANDS: tuple[str, ...] = ("同步数据", "同步masterdata")

DEFAULT_DATA_SOURCES: tuple[str, ...] = (
    "https://cdn.jsdelivr.net/gh/StarMoe-org/moenotes-masterdata@main",
    "https://gcore.jsdelivr.net/gh/StarMoe-org/moenotes-masterdata@main",
    "https://fastly.jsdelivr.net/gh/StarMoe-org/moenotes-masterdata@main",
    "https://raw.githubusercontent.com/StarMoe-org/moenotes-masterdata/main",
)

DEFAULT_ASSET_BASE = "https://assets.bdon.moe"
