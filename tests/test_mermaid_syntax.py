"""Guard: mermaid diagrams in the Sphinx docs must not contain characters that mermaid.js
treats as syntax when they appear in free text.

WHY THIS EXISTS: `sphinx -b html -W` (the docs CI) does NOT validate mermaid — sphinxcontrib-mermaid
only emits the diagram source into the page, and mermaid.js parses it in the *browser*. So a diagram
that builds clean can still render as "Syntax error in text" on the live GitHub-Pages site. That is
exactly what shipped 2026-08-28: semicolons (mermaid's statement separator), a bare ``&`` (actor
conjunction), a bare ``>`` (arrow token), and a ``:`` inside a ``loop``/``opt`` label all broke the
sequence diagrams in `protocol-sequences.rst`. Nobody saw it until the repo went public and the docs
actually deployed.

This lint runs in the normal pytest CI (which gates merges) and fails BEFORE a broken diagram can
deploy. It is a targeted character lint, not a full mermaid parser — it catches the statement-separator
class of bug. Keep it stdlib-only so the whole suite still runs without third-party deps.
"""
import re
from pathlib import Path

_DOCS = Path(__file__).resolve().parent.parent / "docs" / "sphinx"

# arrow / cross / async tokens and inline HTML we must NOT flag when we look for stray < > & etc.
_ARROWS = re.compile(r"--?(>>|\)|->|x|>)|<br\s*/?>|&(amp|lt|gt|nbsp);")
# block keywords whose label is free text after the keyword (a ':' there breaks parsing)
_BLOCK_LABEL = re.compile(r"^\s*(loop|opt|alt|else|par|and|rect|critical|option|break)\b(.*)$")


def _mermaid_blocks(rst_text):
    """Yield (start_line, [body_lines]) for every ``.. mermaid::`` block in an RST document."""
    lines = rst_text.split("\n")
    i = 0
    while i < len(lines):
        if lines[i].strip() == ".. mermaid::":
            start = i + 1
            i += 1
            while i < len(lines) and lines[i].strip() == "":
                i += 1
            body = []
            while i < len(lines) and (lines[i].startswith("    ") or lines[i].strip() == ""):
                body.append((i + 1, lines[i]))
            # walk to the first non-indented, non-blank line
                i += 1
            yield start, body
        else:
            i += 1


def _offending(lineno, line):
    """Return a reason string if this mermaid body line carries a browser-breaking char, else None."""
    stripped = line.strip()
    if not stripped:
        return None
    # remove arrow/cross/async tokens + HTML entities so we only inspect the free text
    core = _ARROWS.sub("", line)
    if ";" in core:
        return "semicolon ';' (mermaid statement separator) — use an em dash or comma"
    if re.search(r"(^|\s)&(\s|$)", core):
        return "bare '&' (actor conjunction) — write 'and'"
    if re.search(r"[<>]", core):
        return "bare '<' or '>' (arrow token) — spell it out (e.g. 'greater than')"
    m = _BLOCK_LABEL.match(line)
    if m and ":" in m.group(2):
        return "':' inside a %s label — mermaid mis-parses it; rephrase without a colon" % m.group(1)
    return None


def test_docs_mermaid_blocks_have_no_browser_breaking_chars():
    """Every ``.. mermaid::`` block in docs/sphinx/*.rst must be free of characters mermaid.js
    rejects in free text — otherwise the diagram builds clean but renders as "Syntax error in text"
    on the live docs site (regression guard for the 2026-08-28 protocol-sequences breakage).
    """
    problems = []
    for rst in sorted(_DOCS.glob("*.rst")):
        for _start, body in _mermaid_blocks(rst.read_text()):
            for lineno, line in body:
                reason = _offending(lineno, line)
                if reason:
                    problems.append(f"{rst.name}:{lineno}: {reason}\n      {line.strip()}")
    assert not problems, (
        "mermaid diagram(s) contain characters that render as 'Syntax error in text' in the browser "
        "(sphinx -W cannot catch this — it only emits the source):\n" + "\n".join(problems)
    )
