"""Rebuilds the MiSans subset embedded into rendered pages.

The t2i service renders a detached HTML document, so the plugin cannot point it
at a font file on disk; the font has to travel inside the page. Shipping the
full 7.8 MB face would bloat every render request, so this script keeps only the
glyphs the plugin can actually display:

* every literal character in the Jinja2 templates,
* every string that can reach a page from masterdata (titles, names, skill text),
* the character alias table,
* the punctuation, symbols and full-width forms the UI draws itself.

Usage (from the plugin directory):
    python tests/build_font.py

Writes res/misans-subset.ttf and res/misans-subset.b64.txt.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FONT = ROOT / "res" / "font.ttf"
OUTPUT_FONT = ROOT / "res" / "misans-subset.ttf"
OUTPUT_B64 = ROOT / "res" / "misans-subset.b64.txt"

TEMPLATE_DIR = ROOT / "bdon" / "templates"
ALIAS_FILE = ROOT / "res" / "character_aliases.json"
# render_samples.py caches masterdata here; the live plugin uses AstrBot's data
# directory, but both carry the same set of localised strings.
MASTERDATA_DIRS = [
    ROOT / "tests" / ".data" / "masterdata",
    ROOT / "data" / "masterdata",
]

# Characters the UI renders itself: digits, latin, ASCII punctuation, the
# symbol glyphs the templates and CSS use, and the CJK/kana/hangul blocks a
# future data update may introduce. Omitting a block here only means a future
# masterdata row falls back to the generic sans font until this is rerun.
BASE_CHARACTERS = (
    "0123456789"
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    " .,:;!?'\"()[]{}/\\|-_=+*&%$#@~`^<>"
    "★☆♪♫✦✧▣◈▤●○◎※†‡§¶©®™°±×÷←→↑↓āīūēō…—‘’“”"
)


def _walk(value: object, into: set[str]) -> None:
    if isinstance(value, str):
        into.update(value)
    elif isinstance(value, dict):
        for item in value.values():
            _walk(item, into)
    elif isinstance(value, list):
        for item in value:
            _walk(item, into)


def collect_characters() -> set[str]:
    characters: set[str] = set(BASE_CHARACTERS)

    for template in TEMPLATE_DIR.glob("*.html"):
        source = template.read_text(encoding="utf-8")
        # Drop Jinja syntax so only display text contributes glyphs.
        source = re.sub(r"\{%.*?%\}", "", source, flags=re.DOTALL)
        source = re.sub(r"\{\{.*?\}\}", "", source, flags=re.DOTALL)
        characters.update(source)

    if ALIAS_FILE.is_file():
        characters.update(ALIAS_FILE.read_text(encoding="utf-8"))

    for directory in MASTERDATA_DIRS:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            _walk(payload, characters)

    # Any character a game string could still introduce that the sweep above
    # missed: the common CJK punctuation and kana ranges stay in the subset.
    characters.update(chr(code) for code in range(0x3000, 0x303F))
    characters.update(chr(code) for code in range(0x3040, 0x30FF))
    characters.update(chr(code) for code in range(0xFF00, 0xFF5F))
    return characters


def build() -> tuple[int, int]:
    from fontTools import subset
    from fontTools.subset import Options, Subsetter

    characters = collect_characters()
    text = "".join(sorted(characters))

    options = Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.notdef_outline = True
    options.recalc_bounds = True
    options.recalc_timestamp = False
    options.drop_tables = ["DSIG"]

    font = subset.load_font(str(SOURCE_FONT), options)
    subsetter = Subsetter(options=options)
    subsetter.populate(text=text)
    subsetter.subset(font)
    subset.save_font(font, str(OUTPUT_FONT), options)

    encoded = base64.b64encode(OUTPUT_FONT.read_bytes()).decode("ascii")
    OUTPUT_B64.write_text(encoded, encoding="ascii")
    return len(text), len(encoded)


def main() -> int:
    if not SOURCE_FONT.is_file():
        print(f"missing source font: {SOURCE_FONT}", file=sys.stderr)
        return 1
    glyphs, encoded = build()
    print(f"{glyphs} characters -> {OUTPUT_FONT.name} ({OUTPUT_FONT.stat().st_size:,} bytes)")
    print(f"base64 payload: {encoded:,} chars (~{encoded // 1024} KB per render)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
