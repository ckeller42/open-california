"""CI gate ``docs-touched``: a PR that changes runtime code also changes the docs.

Runtime code is what a reader of the docs relies on: ``calictl/`` (Python + the web UI), the ESP
firmware (``firmware/components/``) and the shared C codec (``csrc/``). The docs are ``docs/``, any
Markdown file and ``protocol/signals.yaml`` (the catalog the generated views render). A change
that truly needs no docs (a refactor, a test-only fix) says so in the PR body with a line
``Docs: n/a — <reason>``.

    python3 -m tools.docs_touched <base-ref>      # PR_BODY in the environment

.. req:: A runtime change ships with its docs
   :id: R_DOCS_TOUCHED

   A PR that changes runtime code (calictl, the firmware, the C codec) also changes the docs, or
   its body names why none is needed (``Docs: n/a — <reason>``).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

CODE = re.compile(r"^(calictl/|firmware/components/|csrc/)")
DOCS = re.compile(r"^docs/|\.md$|^protocol/signals\.yaml$")
EXEMPT = re.compile(r"^\s*Docs:\s*n/?a\b.*\S", re.IGNORECASE | re.MULTILINE)


def verdict(paths: list[str], body: str) -> str | None:
    """Why the PR fails the gate, or ``None`` when it passes.

    :param paths: the files the PR changes (repo-relative)
    :param body: the PR description
    :returns: the failure message, or ``None``
    """
    code = [p for p in paths if CODE.match(p)]
    if not code or any(DOCS.search(p) for p in paths) or EXEMPT.search(body or ""):
        return None
    return (
        "runtime code changed without a docs change: %s%s\n"
        "Update the docs (docs/, a Markdown file or protocol/signals.yaml) in this PR, or add a line "
        "'Docs: n/a — <reason>' to the PR description." % (", ".join(code[:5]), " …" if len(code) > 5 else "")
    )


def main(argv=None) -> int:
    base = (argv or sys.argv[1:] or ["origin/main"])[0]
    out = subprocess.run(
        ["git", "diff", "--name-only", base + "...HEAD"], check=True, capture_output=True, text=True
    )
    msg = verdict(out.stdout.split(), os.environ.get("PR_BODY", ""))
    if msg:
        print("docs-touched: " + msg)
        return 1
    print("docs-touched: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
