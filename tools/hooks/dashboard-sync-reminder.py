#!/usr/bin/env python3
"""PostToolUse hook — Grafana dashboards do NOT auto-update when BLE signals change.

If a signal-defining file was just edited, inject a reminder to update + push the
dashboards. Reads the PostToolUse event JSON on stdin; prints additionalContext.
"""
import json
import os
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

fp = (data.get("tool_input") or {}).get("file_path", "") or ""
targets = ("calictl/semantics.py", "protocol/signals.yaml", "calictl/mqtt.py", "calictl/influx.py")
if any(fp.endswith(t) for t in targets):
    msg = ("You edited %s (a signal-defining file). Grafana dashboards do NOT auto-update: "
           "if you added or changed a SURFACED signal, update calictl/deploy/camper-dashboard.json "
           "and push it to BOTH the Pi (:3000) and Grafana Cloud via "
           "calictl/deploy/push_dashboard.py, then re-run 'python3 -m tools.audit_signals --report'."
           % os.path.basename(fp))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                             "additionalContext": msg}}))
