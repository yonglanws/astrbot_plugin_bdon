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

    Cards and songs cap at five columns; support/comic art is 16:9 or square
    with more internal detail, so it caps at four. Small result sets use fewer
    columns, which keeps the cards the same physical size instead of stretching
    two results across the full canvas. Events are wide hero cards, so they use
    at most two columns.
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
    if count <= 8:
        return 4
    return 5


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
)

ALL_TABLES: tuple[str, ...] = REQUIRED_TABLES + OPTIONAL_TABLES

# Game command aliases. The first entry is the canonical name used in the UI.
COMMAND_ALIASES: dict[str, str] = {
    "查歌": "song",
    "查曲": "song",
    "查歌曲": "song",
    "查卡": "card",
    "查卡牌": "card",
    "查卡面": "card",
    "查支援卡": "support",
    "查支援": "support",
    "查snap": "support",
    "查活动": "event",
    "查活動": "event",
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
    "查图鉴": "catalog",
    "查圖鑑": "catalog",
    "图鉴": "catalog",
    "圖鑑": "catalog",
    "帮助": "help",
    "說明": "help",
    "说明": "help",
    "help": "help",
    "邦多利帮助": "help",
    "邦邦帮助": "help",
    "bdon帮助": "help",
    "ournotes帮助": "help",
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
    "event": "查活动",
    "event_list": "活动一览",
    "catalog": "查图鉴",
}

SYNC_COMMANDS: tuple[str, ...] = ("同步数据", "同步masterdata")

DEFAULT_DATA_SOURCES: tuple[str, ...] = (
    "https://cdn.jsdelivr.net/gh/StarMoe-org/moenotes-masterdata@main",
    "https://gcore.jsdelivr.net/gh/StarMoe-org/moenotes-masterdata@main",
    "https://fastly.jsdelivr.net/gh/StarMoe-org/moenotes-masterdata@main",
    "https://raw.githubusercontent.com/StarMoe-org/moenotes-masterdata/main",
)

DEFAULT_ASSET_BASE = "https://assets.bdon.moe"
