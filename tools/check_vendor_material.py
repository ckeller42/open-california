"""Guard: no VW/vendor material, real vehicle BLE MAC or VIN in the repo.

One source of truth for three places: the ``vendor-material`` pre-commit hook (checks the
staged files pre-commit passes in), ``tools/ci.sh vendor-check`` and the CI
``no-vendor-material`` job (both ``--all``: every tracked file). It refuses:

1. VW/vendor binaries or extracted assets by path (``*.apk/*.dex/*.jar/…``, ``decompile/``,
   ``manuals/``, ``ui/assets/svg/``, ``strings_resolved*``) — belt-and-braces over ``.gitignore``.
2. The real vehicle BLE MAC, matched by the unit's OUI prefix plus any suffix so this guard never
   embeds the full device MAC itself — use a placeholder / env var instead.
3. A VIN (PII): a 17-char VIN-alphabet token AND a ``WV2ZZZ``/``VIN`` context on the same line
   (WV2ZZZ is the generic VW world-manufacturer prefix, safe to name).

Stdlib-only and Python 3.9-compatible on purpose (it may run under a bare system ``python3``).

    python3 tools/check_vendor_material.py FILE...     # the given files (pre-commit)
    python3 tools/check_vendor_material.py --all       # every tracked file (CI, tools/ci.sh)

Exits non-zero, naming each offending path (and line, for MAC/VIN hits), on any violation.
"""

import re
import subprocess
import sys

PATH_RE = re.compile(
    r"\.(apk|xapk|dex|jar|aar|aab|cvr|pcap|pcapng|pklg|btsnoop)$"
    r"|(^|/)decompile/|(^|/)manuals/|ui/assets/svg/|strings_resolved",
    re.IGNORECASE,
)
MAC_RE = re.compile(rb"20:81:9a(:[0-9a-f]{2}){3}", re.IGNORECASE)
VIN_TOKEN_RE = re.compile(rb"\b[A-HJ-NPR-Z0-9]{17}\b")
VIN_CONTEXT_RE = re.compile(rb"WV2ZZZ|VIN", re.IGNORECASE)
# Content (MAC/VIN) checks skip the workflow directory, as the whole-tree CI grep always did.
CONTENT_EXEMPT = (".github/",)


def _tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], check=True, capture_output=True).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def check(paths):
    """Return a list of human-readable violations for ``paths`` (repo-relative)."""
    problems = []
    for path in paths:
        if PATH_RE.search(path):
            problems.append("vendor/binary material: %s" % path)
            continue
        if path.startswith(CONTENT_EXEMPT):
            continue
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except (FileNotFoundError, IsADirectoryError):
            continue  # deleted/renamed in the working tree, or a submodule entry
        for lineno, line in enumerate(data.splitlines(), 1):
            if MAC_RE.search(line):
                problems.append("real vehicle BLE MAC (OUI 20:81:9A:…): %s:%d" % (path, lineno))
            if VIN_TOKEN_RE.search(line) and VIN_CONTEXT_RE.search(line):
                problems.append("VIN (PII): %s:%d" % (path, lineno))
    return problems


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    paths = _tracked_files() if args == ["--all"] else args
    problems = check(paths)
    if problems:
        print(
            "vendor-material guard: BLOCKED — keep VW material, the vehicle MAC and VINs out of "
            "the repo (use a placeholder / env var):",
            file=sys.stderr,
        )
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
