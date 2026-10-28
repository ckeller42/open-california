#!/usr/bin/env python3
"""PreToolUse hook (matcher ``Bash``): refuse a git command that skips the hooks (``--no-verify``,
``git commit -n``). The pre-commit hooks are the local copy of CI's lint/format/type gates; a
bypassed commit is how ruff-format, mypy and markdownlint failures still reached CI.

Reads the PreToolUse event JSON on stdin. Silent (exit 0) when clean; on a bypass it exits 2 with
the reason on stderr, which blocks the call and tells the model why. Fails open on a malformed
event (never breaks the tool).
"""

import json
import re
import shlex
import sys

GUARDED = {"commit", "push", "merge", "cherry-pick", "rebase", "am"}  # the ones with --no-verify


def bypasses(tokens):
    """Whether one simple command (its argv) is a git command with the hooks skipped."""
    if not tokens or tokens[0] != "git":
        return False
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):  # git's own options: -C <dir>, -c <k=v>
        i += 2 if tokens[i] in ("-C", "-c") else 1
    if i >= len(tokens) or tokens[i] not in GUARDED:
        return False
    sub, rest = tokens[i], tokens[i + 1 :]
    if "--no-verify" in rest:
        return True
    # commit's short -n, alone or in a cluster (-qn, -nm "msg"): the cluster's flags end at its first
    # m, whose message may be attached (-mnew); push -n is --dry-run, not a bypass
    return sub == "commit" and any(re.match(r"-[A-Za-z]", t) and "n" in t[1:].split("m", 1)[0] for t in rest)


try:
    command = (json.load(sys.stdin).get("tool_input") or {}).get("command") or ""
except Exception:
    sys.exit(0)

for part in re.split(r"&&|\|\||[;|\n]", command):
    try:
        tokens = shlex.split(part)
    except ValueError:
        tokens = part.split()
    if bypasses(tokens):
        sys.stderr.write(
            "Blocked by block-no-verify hook: `%s` skips the git hooks. They are the local copy of CI's "
            "gates (ruff, mypy, markdownlint, …); fix what they report and commit without "
            "--no-verify / -n.\n" % part.strip()
        )
        sys.exit(2)
