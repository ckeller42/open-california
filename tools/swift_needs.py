"""Swift doc-comment tags -> sphinx-needs import JSON (stdlib only).

Reads ``/// - Requirement: R_A, R_B`` (one ``impl`` need per block) and ``/// - Test: T_X`` followed by
``/// - Verifies: R_A`` (one ``test`` need) in ``ios/**/*.swift``. Unknown requirement ids, a
``Requirement`` without ids, a test without ``Verifies`` and duplicate test ids are errors.
Only ``///`` comments are read (``/** */`` blocks are not used in ``ios/``).

    python3 -m tools.swift_needs --out docs/_build/ios-needs.json [--commit SHA]
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_URL = "https://github.com/ckeller42/open-california/blob"
TAG = re.compile(r"^\s*///\s*-\s*(Requirement|Test|Verifies):\s*(.*?)\s*$")
DOC = re.compile(r"^\s*///")
ID = re.compile(r"[A-Z][A-Z0-9_]*")
DEFINED = re.compile(r":id:\s*((?:R|AD)_[A-Z0-9_]+)")
FENCE = re.compile(r"^(`{3,})(.*)$")


def _without_code_fences(text):
    """Drop the lines inside plain fenced code blocks (MyST directive fences ``{...}`` stay)."""
    out, close, keep = [], None, True
    for line in text.splitlines():
        m = FENCE.match(line)
        if close is None and m:
            close, keep = m.group(1), m.group(2).startswith("{")
        elif close is not None and line.strip() == close:
            close = None
            continue
        if keep:
            out.append(line)
    return "\n".join(out)


def known_ids(repo=ROOT):
    """Requirement and decision ids defined in the docs (MyST) or in python docstrings.

    ``:id:`` lines inside plain fenced code examples in the docs are ignored.

    :param repo: the repository root.
    :returns: the set of ``R_*`` and ``AD_*`` ids.
    """
    ids = set()
    paths = [p for p in (repo / "docs").rglob("*.md") if "_build" not in p.parts]
    paths += [p for top in ("calictl", "tools") for p in (repo / top).rglob("*.py")]
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix == ".md":
            text = _without_code_fences(text)
        ids.update(DEFINED.findall(text))
    return ids


def scan(ios, known, repo=ROOT, commit="main"):
    """Parse every Swift file under ``ios``.

    :param ios: the directory with the Swift sources.
    :param known: the set of valid requirement and decision ids.
    :param repo: the repository root (source paths are relative to it).
    :param commit: the commit in the source URLs.
    :returns: ``(needs, errors)``: a dict of needs by id and a list of error strings.
    """
    needs, errors = {}, []

    def add(nid, kind, title, desc, links, rel, line):
        if nid in needs:
            errors.append("%s:%d: duplicate id %s" % (rel, line, nid))
            return
        for link in links:
            if link not in known:
                errors.append("%s:%d: unknown requirement id %s" % (rel, line, link))
        needs[nid] = {
            "id": nid,
            "type": kind,
            "title": title,
            "description": desc,
            "status": "open",
            "links": links,
            "source": "%s/%s/%s#L%d" % (REPO_URL, commit, rel, line),
        }

    for path in sorted(Path(ios).rglob("*.swift")):
        rel = path.relative_to(repo).as_posix()
        lines = path.read_text(encoding="utf-8").splitlines()
        stem = re.sub(r"[^A-Z0-9]+", "_", path.stem.upper())
        for i, text in enumerate(lines):
            m = TAG.match(text)
            if not m:
                continue
            kind, rest = m.groups()
            if kind == "Requirement":
                links = ID.findall(rest)
                if not links:
                    errors.append("%s:%d: '- Requirement:' names no id" % (rel, i + 1))
                add(
                    "I_IOS_%s_%d" % (stem, i + 1),
                    "impl",
                    "%s:%d" % (rel, i + 1),
                    "Implements " + rest,
                    links,
                    rel,
                    i + 1,
                )
            elif kind == "Test":
                verifies, j = None, i + 1
                while j < len(lines) and DOC.match(lines[j]):
                    v = TAG.match(lines[j])
                    if v and v.group(1) != "Verifies":
                        break  # the next tag starts another block
                    if v:
                        verifies = ID.findall(v.group(2))
                        break
                    j += 1
                if not verifies:
                    errors.append("%s:%d: test %s has no '- Verifies:' line" % (rel, i + 1, rest))
                    verifies = []
                add(rest, "test", rest, "Verifies " + ", ".join(verifies), verifies, rel, i + 1)
    return needs, errors


def to_json(needs):
    """The sphinx-needs ``needimport`` document.

    :param needs: the needs dict from :func:`scan`.
    :returns: the import document as a dict.
    """
    return {"project": "ios", "current_version": "1.0", "versions": {"1.0": {"needs": needs}}}


def _commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "main"


def main(argv=None):
    """Write the import JSON.

    :param argv: command-line arguments (default ``sys.argv``).
    :returns: the exit code: 1 on a tag error, a missing ``ios`` directory or zero needs.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--commit", default=None)
    ap.add_argument("--ios", default=str(ROOT / "ios"))
    args = ap.parse_args(argv)
    ios = Path(args.ios)
    if not ios.is_dir():
        print("swift_needs: no such directory: %s" % ios, file=sys.stderr)
        return 1
    needs, errors = scan(ios, known_ids(), commit=args.commit or _commit())
    if not needs and not errors:
        errors.append("no Requirement or Test tags found under %s" % ios)
    for e in errors:
        print("swift_needs: " + e, file=sys.stderr)
    if errors:
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(to_json(needs), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("swift_needs: %d needs -> %s" % (len(needs), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
