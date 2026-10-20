"""Keyword search for the query commands.

Lists are returned whole (pagination is not shown to users); the page arguments
remain only as a safety valve for a table larger than ``page_size``.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence


PAGE_RE = re.compile(
    r"(?:^|\s)(?:-p\s*(\d+)|第\s*(\d+)\s*页|页\s*(\d+)|#p(\d+))\s*$",
    re.IGNORECASE,
)

# Near-misses people type: romaji without macrons, swapped vowels, extra
# trailing characters. Anything at or above this ratio counts as a match.
FUZZY_RATIO = 0.78

# Character names live in one crowded romaji namespace, so a plain ratio is a
# poor discriminator: "tomori" scores 0.77 against the other characters' names
# "timoris" / "mortis" / "amoris", and "saki" scores 0.75 against "taki". A fuzzy
# hit therefore has to beat the *other* words that also resemble the query by a
# clear margin, otherwise it is a coincidence and the item is dropped. Names
# that share a spelling family ("amoris", "doloris", "mortis", "timoris") can
# never clear the margin against each other, which is exactly the intent.
RIVAL_MARGIN = 0.09


def normalise(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _strip_marks(value: str) -> str:
    """Drops macrons and tone marks so 'rāna' matches 'rana'."""
    return (
        value.replace("ā", "a")
        .replace("ī", "i")
        .replace("ū", "u")
        .replace("ē", "e")
        .replace("ō", "o")
    )


def parse_page(query: str) -> tuple[str, int]:
    """Strips a trailing ``-p2`` / ``第2页`` / ``页2`` and returns the 1-based page."""
    match = PAGE_RE.search(query or "")
    if not match:
        return (query or "").strip(), 1
    page = next((int(group) for group in match.groups() if group), 1)
    return (query[: match.start()].strip(), max(1, page))


@dataclass
class Resolved:
    mode: str  # "detail" | "list"
    query: str = ""
    item: Any | None = None
    items: list[Any] = field(default_factory=list)
    total: int = 0
    page: int = 1
    pages: int = 1
    suggestions: list[Any] = field(default_factory=list)
    exact: bool = False


def _display_id(item: Any) -> str:
    """The number shown on the card; songs are 100001+ in MasterData."""
    try:
        return str(item.display_id)
    except AttributeError:
        return str(item.id)


def _terms(item: Any) -> list[str]:
    """Every searchable word for one entry: title, aliases, band, role, ID."""
    title = getattr(item, "title_for_search", "")
    haystack = getattr(item, "search_text", "")
    raw = f"{title} {haystack} {_display_id(item)} {getattr(item, 'id', '')}"
    words = [normalise(word) for word in re.split(r"[\s,、/|]+", raw) if word.strip()]
    # Deduplicate while keeping the first (title) occurrence first.
    seen: set[str] = set()
    unique = []
    for word in words:
        if word not in seen:
            seen.add(word)
            unique.append(word)
    return unique


def _similarity(term: str, query: str) -> float:
    return difflib.SequenceMatcher(None, _strip_marks(term), _strip_marks(query)).ratio()


def _term_score(term: str, query: str) -> int:
    """How well one searchable word answers the query. 0 means no match."""
    if not term or not query:
        return 0
    if term == query:
        return 1000
    # A term that starts with the query is the strongest partial match; this
    # covers CJK names and romanised aliases alike ("灯" -> "灯的表情1").
    if term.startswith(query):
        return 800 - (len(term) - len(query))
    if query in term:
        # A short romanised alias ("rts", "obl") is often just a fragment of an
        # English word ("curtsies", "hearts"). Only accept an interior hit when
        # the query is long enough that coincidence is implausible.
        if len(query) <= 4 and term.isascii() and not _word_boundary(query, term):
            return 0
        return 500 + len(query) * 4
    # Near-miss spelling: macrons dropped, one character off, or a typo. This is
    # the tier that mis-fires on character names, so it only counts when no other
    # known word resembles the query at least as closely.
    ratio = _similarity(term, query)
    if ratio < FUZZY_RATIO:
        return 0
    if _has_rival(term, query, ratio):
        return 0
    return int(400 * ratio)


def _word_boundary(query: str, term: str) -> bool:
    """True when ``query`` sits on a separator inside ``term``.

    MasterData titles arrive as whitespace-joined words, so a hit that starts
    right after a separator is a real word ("rts" in "rts special") while one
    buried inside a longer word is a coincidence.
    """
    index = term.find(query)
    if index <= 0:
        return True
    return not term[index - 1].isalnum()


def _score(item: Any, query: str, tokens: list[str]) -> int:
    terms = _terms(item)
    # Multi-word queries ("Ave Mujica", "高松灯 生日") have to be scored per token:
    # comparing the whole phrase against one word never matches, because terms are
    # already split on whitespace. The full phrase still wins when some item does
    # carry it verbatim.
    best = max((_term_score(term, query) for term in terms), default=0)
    if tokens and len(tokens) > 1:
        per_token = max(
            (_term_score(term, token) for token in tokens for term in terms), default=0
        )
        best = max(best, per_token)
        haystack = normalise(getattr(item, "search_text", ""))
        term_set = set(terms)
        matched = sum(1 for token in tokens if token in haystack or token in term_set)
        if matched == 0:
            return 0
        if matched < len(tokens):
            return best // 2
        best += 200 + len(tokens) * 10
    return best


def _has_rival(term: str, query: str, ratio: float) -> bool:
    """True when another known word matches the query at least as closely.

    ``rivals`` is the vocabulary of the current query (every searchable word of
    every candidate item), built once per ``resolve`` call. Without it a fuzzy
    hit has to be judged in a vacuum, which is what lets one character's romaji
    name answer a query for another.
    """
    rivals = _RIVAL_WORDS.get()
    if not rivals:
        return False
    for other in rivals:
        if other == term or len(other) < 3:
            continue
        if _similarity(other, query) - ratio > RIVAL_MARGIN:
            return True
    return False


class _RivalWords:
    """Per-query rival vocabulary, swapped in by ``resolve``."""

    def __init__(self) -> None:
        self.words: tuple[str, ...] = ()

    def set(self, words: Iterable[str]) -> None:
        self.words = tuple(words)

    def get(self) -> tuple[str, ...]:
        return self.words


_RIVAL_WORDS = _RivalWords()


def _rival_vocabulary(items: Sequence[Any]) -> set[str]:
    """Distinct lowercased words of every candidate, for rival detection."""
    words: set[str] = set()
    for item in items:
        for term in _terms(item):
            if len(term) >= 3:
                words.add(term)
    return words


def _fuzzy_suggestions(items: Sequence[Any], query: str, limit: int = 5) -> list[Any]:
    titles = [getattr(item, "title_for_search", "") for item in items]
    lowered = [title.lower() for title in titles]
    matches = difflib.get_close_matches(query.lower(), lowered, n=limit, cutoff=0.35)
    picked: list[Any] = []
    for match in matches:
        index = lowered.index(match)
        picked.append(items[index])
    if picked:
        return picked
    # A short prefix is often enough for CJK titles; otherwise show nothing
    # rather than unrelated newest entries.
    return [
        item
        for item in items
        if normalise(getattr(item, "title_for_search", "")).startswith(query.lower())
    ][:limit]


def resolve(
    items: Sequence[Any],
    raw_query: str,
    page_size: int = 10_000,
    kind: str = "",
) -> Resolved:
    query, page = parse_page(raw_query or "")
    # NFKC folds full-width digits/letters typed with a Chinese IME, and a
    # leading "#" is how the list shows IDs, so "查卡 #64" resolves like "查卡 64".
    query = unicodedata.normalize("NFKC", query).strip().lstrip("#＃").strip()
    page_size = max(1, int(page_size))
    total_items = len(items)

    if not query:
        pages = max(1, (total_items + page_size - 1) // page_size)
        page = min(page, pages)
        start = (page - 1) * page_size
        return Resolved(
            mode="list",
            items=list(items[start : start + page_size]),
            total=total_items,
            page=page,
            pages=pages,
        )

    # An exact ID always opens the detail view.
    if query.isdigit():
        exact = next(
            (
                item
                for item in items
                if str(item.id) == query or _display_id(item) == query
            ),
            None,
        )
        if exact is not None:
            return Resolved(mode="detail", query=query, item=exact, total=1, exact=True)

    tokens = [token for token in normalise(query).split(" ") if token]
    # Publish the candidate vocabulary before scoring so the fuzzy tier can tell
    # a real near-miss from two unrelated words that happen to look alike.
    _RIVAL_WORDS.set(_rival_vocabulary(items))
    scored = []
    for item in items:
        value = _score(item, normalise(query), tokens)
        if value > 0:
            scored.append((value, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))

    if not scored:
        return Resolved(
            mode="list",
            query=query,
            total=0,
            page=1,
            pages=1,
            suggestions=_fuzzy_suggestions(items, query),
        )

    # A unique exact-title hit is more useful as a detail page than as a one-item grid.
    top_score, top_item = scored[0]
    title_exact = normalise(getattr(top_item, "title_for_search", "")) == normalise(query)
    unique_top = len(scored) == 1 or scored[1][0] < top_score
    if unique_top and (len(scored) == 1 or title_exact):
        return Resolved(
            mode="detail",
            query=query,
            item=top_item,
            total=len(scored),
            exact=title_exact,
        )

    filtered = [item for _, item in scored]
    pages = max(1, (len(filtered) + page_size - 1) // page_size)
    page = min(page, pages)
    start = (page - 1) * page_size
    return Resolved(
        mode="list",
        query=query,
        items=filtered[start : start + page_size],
        total=len(filtered),
        page=page,
        pages=pages,
    )
