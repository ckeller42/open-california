"""Guard: ``docs/glossary.md`` (the Sphinx ``glossary`` directive) is the one glossary.

A hand-written "Glossary" section elsewhere drifts from it, and a duplicate term in the
directive is ambiguous for ``{term}`` / ``:term:`` links. ``sphinx -W`` already fails on a link
to a term that does not exist. Pure Python, no Sphinx import.
"""

import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GLOSSARY = ROOT / "docs" / "glossary.md"

_HEADING = re.compile(r"^(#+\s.*|[^\s].*\n[-=~^\"]{3,})$", re.M)
# A hand-written definition ("**term** — ...", "**term**: ...") and a second glossary directive.
_DEFINITION = re.compile(r"\*\*[^*]+\*\*\s*(—|:|-)\s")
_DIRECTIVE = re.compile(r"^```\{glossary\}|^\.\. glossary::", re.M)


def _doc_files():
    files = (
        list(ROOT.glob("*.md")) + list((ROOT / "docs").rglob("*.md")) + list((ROOT / "docs").rglob("*.rst"))
    )
    return [f for f in files if "_build" not in f.parts and f != GLOSSARY]


def _glossary_sections(text):
    """Yield the body of every section whose heading mentions "Glossary"."""
    heads = list(_HEADING.finditer(text))
    for i, m in enumerate(heads):
        if "glossary" in m.group(0).lower():
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            yield text[m.end() : end]


def glossary_terms(text):
    """Return the term lines of the ``{glossary}`` fence in ``text``.

    :param text: the Markdown source of ``docs/glossary.md``.
    :returns: every term, in source order (several per definition for synonyms).
    """
    body = text.split("```{glossary}", 1)[1].split("```", 1)[0]
    return [
        ln.strip()
        for ln in body.splitlines()
        if ln.strip() and not ln[0].isspace() and not ln.startswith(":")
    ]


def test_one_glossary_only():
    offenders = []
    for f in _doc_files():
        text = f.read_text(encoding="utf-8")
        if _DIRECTIVE.search(text) or any(_DEFINITION.search(s) for s in _glossary_sections(text)):
            offenders.append(str(f.relative_to(ROOT)))
    assert not offenders, f"define terms in docs/glossary.md, not in: {offenders}"


def test_glossary_terms_unique():
    terms = glossary_terms(GLOSSARY.read_text(encoding="utf-8"))
    assert len(terms) > 20
    lower = [t.lower() for t in terms]
    dupes = sorted({t for t in terms if lower.count(t.lower()) > 1})
    assert not dupes, f"duplicate glossary terms: {dupes}"


def test_guard_catches_a_hand_written_glossary():
    sec = list(_glossary_sections("# X\n\n## Glossary (quick)\n\n**GATT char** — a BLE attribute.\n## Y\n"))
    assert sec and _DEFINITION.search(sec[0])
    pointer = list(_glossary_sections("## 12. Glossary\n\nDefined in the [glossary](docs/glossary.md).\n"))
    assert pointer and not _DEFINITION.search(pointer[0])


# Definitions no page links yet, with the reason. Keep this short: link a term where the docs
# use it rather than growing the list.
ORPHAN_ALLOW: dict[str, str] = {}

_TERM_REF = re.compile(r"(?:\{term\}|:term:)`(?:[^`<]*<([^>`]+)>|([^`]+))`")
_TERM_URL = re.compile(r"glossary\.html#term-([\w-]+)")


def _slug(term):
    """Sphinx's ``term-`` anchor for ``term``, lower-cased, for matching absolute links."""
    ascii_ = unicodedata.normalize("NFKD", term).encode("ascii", "ignore").decode()
    return re.sub(r"[^\w]+", "-", ascii_).strip("-").lower()


def glossary_entries(text):
    """Group the term lines of the ``{glossary}`` fence by shared definition.

    :param text: the Markdown source of ``docs/glossary.md``.
    :returns: one list of synonym terms per definition, in source order.
    """
    body = text.split("```{glossary}", 1)[1].split("```", 1)[0]
    entries, cur = [], []
    for ln in body.splitlines():
        if ln.strip() and not ln[0].isspace() and not ln.startswith(":"):
            cur.append(ln.strip())
        elif cur:
            entries.append(cur)
            cur = []
    return entries + ([cur] if cur else [])


def referenced_terms(text):
    """Return the lower-cased targets of every ``{term}``/``:term:`` role and glossary-URL anchor.

    :param text: a Markdown or reStructuredText page.
    :returns: a set of lower-cased term names and ``term-`` anchor slugs.
    """
    refs = {(a or b).strip().lower() for a, b in _TERM_REF.findall(text)}
    return refs | {s.lower() for s in _TERM_URL.findall(text)}


def test_every_glossary_entry_is_linked():
    """Every definition is linked from some page (through any synonym), or allow-listed."""
    refs = set()
    for f in _doc_files():
        refs |= referenced_terms(f.read_text(encoding="utf-8"))
    orphans = [
        e[0]
        for e in glossary_entries(GLOSSARY.read_text(encoding="utf-8"))
        if e[0] not in ORPHAN_ALLOW and not any(t.lower() in refs or _slug(t) in refs for t in e)
    ]
    assert not orphans, f"glossary entries no page links to (add a {{term}} link or ORPHAN_ALLOW): {orphans}"


def test_orphan_guard_sees_roles_and_urls():
    refs = referenced_terms(
        "a {term}`held link <Persistent session>` b :term:`latch` [x](g/glossary.html#term-Flustermodus)"
    )
    assert {"persistent session", "latch", "flustermodus"} <= refs
    assert _slug("Flüstermodus") == "flustermodus"
    assert glossary_entries("```{glossary}\n:sorted:\n\nA\nB\n  def\n\nC\n  def\n```") == [["A", "B"], ["C"]]
