"""Reader pages carry no history and no internal markers (docs/building-the-docs.md "Writing reader pages")."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The rules' exact patterns. A reader page describes the present; history and proof live in the
# lab notes (docs/business-logic/) and the evidence ledger.
PATTERNS = {
    "bare issue/PR number": re.compile(r"(?<![\w/&#])#\d{2,4}\b"),
    "UNVERIFIED marker": re.compile(r"\bUNVERIFIED\b"),
    "MERGED_AMBIGUOUS marker": re.compile(r"\bMERGED_AMBIGUOUS\b"),
    "obfuscated app class": re.compile(r"\b[a-z]{1,2}/[a-z]{1,2}\.java\b"),
    "firmware commit hash": re.compile(r"\b(?:fw|firmware) `[0-9a-f]{7}`"),
    "ISO date": re.compile(r"\b20\d\d-\d\d-\d\d\b"),
}

# Reader pages that pass today. Add a page here once it passes.
CHECKED_PAGES = [
    "docs/api.md",
    "docs/building-the-docs.md",
    "docs/features.md",
    "docs/howto-android-phone-lab.md",
    "docs/howto-esp-first-flash.md",
    "docs/howto-pair-your-camper.md",
    "docs/index.md",
    "docs/protocol/README.md",
]


def violations(text: str) -> dict:
    """``{rule: count}`` for every rule the text breaks."""
    found = {name: len(pat.findall(text)) for name, pat in PATTERNS.items()}
    return {name: n for name, n in found.items() if n}


def test_reader_pages_follow_the_style_rules():
    """Every page in CHECKED_PAGES is free of issue numbers, status markers, class names, hashes and dates.

    .. test:: Reader pages carry no history or internal markers
       :id: T_DOCS_STYLE
       :links: R_DOCS_STYLE
    """
    bad = {p: v for p in CHECKED_PAGES if (v := violations((ROOT / p).read_text()))}
    assert not bad, "reader pages break docs/building-the-docs.md 'Writing reader pages': %s" % bad
