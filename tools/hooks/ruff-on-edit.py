#!/usr/bin/env python3
"""PostToolUse hook (matcher ``Edit|Write``): ``ruff check --fix`` + ``ruff format`` the edited
``.py`` file at once, with the repo's ruff config, so a format or autofixable lint finding never
waits for the commit hook (or CI). What ruff cannot fix comes back as ``additionalContext``.

Reads the PostToolUse event JSON on stdin; silent for other files, without ruff, or on a malformed
event (never breaks the tool).
"""

import json
import os
import subprocess
import sys

try:
    path = (json.load(sys.stdin).get("tool_input") or {}).get("file_path") or ""
except Exception:
    sys.exit(0)
if not path.endswith(".py") or not os.path.isfile(path):
    sys.exit(0)

RUFF = [sys.executable, "-m", "ruff"]
try:
    left = subprocess.run(
        RUFF + ["check", "--fix", "--quiet", "--output-format", "concise", path],
        capture_output=True,
        text=True,
        timeout=60,
    )
    subprocess.run(RUFF + ["format", "--quiet", path], capture_output=True, timeout=60)
except (OSError, subprocess.SubprocessError):
    sys.exit(0)
if "No module named ruff" in left.stderr:
    sys.exit(0)
if left.returncode and left.stdout.strip():
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": "ruff (ruff-on-edit hook) still reports in %s:\n%s"
                    % (path, "\n".join(left.stdout.strip().splitlines()[:20])),
                }
            }
        )
    )
