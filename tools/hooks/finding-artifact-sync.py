#!/usr/bin/env python3
"""PostToolUse hook — a protocol/semantics FINDING usually needs to be mirrored into several
artifacts that do NOT update themselves. This fires when an interpretation/mapping file is
edited and reminds you to propagate the finding, so a change like the lamp-map correction
(2026-08-30) doesn't land in code while the GUI, docs, and evidence ledger still say the old thing.

Reads the PostToolUse event JSON on stdin; prints additionalContext when relevant.
"""
import json
import os
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

fp = (data.get("tool_input") or {}).get("file_path", "") or ""
# Files where a "finding" (new/changed interpretation, scale, field map, control frame) lands.
triggers = ("calictl/semantics.py", "calictl/control.py", "calictl/overrides.py",
            "protocol/dictionary.yaml")
if not any(fp.endswith(t) for t in triggers):
    sys.exit(0)

msg = (
    "You edited %s. A protocol/semantics finding is usually not done until it's mirrored — "
    "check each and update the ones this change touches:\n"
    "  • GUI: calictl/webui/app.js (LIGHT_LAMPS / labels / any hardcoded map) — served client-side, "
    "not generated;\n"
    "  • RE docs: docs/business-logic/ (the relevant note + evidence-ledger.md tier + a dated "
    "DECISIONS.md entry) — supersede any now-wrong 'inferred/UNVERIFIED' claim;\n"
    "  • ui/screens/*.yaml if the app-UI semantics changed;\n"
    "  • protocol sequence diagrams (docs/protocol-sequences.rst) only if a FRAME/sequence changed "
    "(the lamp/area map does not);\n"
    "  • Grafana dashboard (see the dashboard-sync reminder) if a SURFACED signal changed;\n"
    "  • tests + `python3 -m tools.audit_signals --report`, then deploy to buspi to live-verify."
    % os.path.basename(fp)
)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                          "additionalContext": msg}}))
