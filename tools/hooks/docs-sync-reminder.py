#!/usr/bin/env python3
"""PostToolUse hook — runtime code changed: name the docs that describe it, while the change is
fresh. The CI gate ``docs-touched`` (tools/docs_touched.py) fails a PR that changes runtime code
without any docs change; this reminder points at WHICH page, before the PR exists.
Reads the PostToolUse event JSON on stdin; prints additionalContext when it fires.
"""

import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

fp = (data.get("tool_input") or {}).get("file_path", "") or ""
# path fragment -> the docs that describe it (first match wins)
WHERE = (
    (
        "/firmware/components/",
        "docs/firmware.md (the section for this component) + an evidence-ledger row for a live finding",
    ),
    (
        "/calictl/webui/",
        "docs/ (the UI behaviour page, e.g. firmware.md for satellite screens) + strings.de.js for every new label",
    ),
    ("/csrc/", "docs/cross-language-codec page + docs/firmware.md if the satellite's behaviour changes"),
    (
        "/calictl/",
        "the docs/ page or docs/business-logic/ note for this module, and its docstring's .. req::",
    ),
)
for frag, docs in WHERE:
    if frag in fp and "/tests/" not in fp:
        msg = (
            "You changed runtime code (%s). Update the docs in the same PR: %s. "
            "CI's docs-touched check fails without a docs change unless the PR says "
            "'Docs: n/a — <reason>'." % (fp.split("/open-california/")[-1], docs)
        )
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}))
        break
