# Claude Code hooks

## dashboard-sync-reminder.py
A **PostToolUse** hook: Grafana dashboards do NOT auto-update when BLE signals
change. When a signal-defining file (`calictl/semantics.py`, `protocol/signals.yaml`,
`calictl/mqtt.py`, `calictl/influx.py`) is edited, it injects a reminder to update
`calictl/deploy/camper-dashboard.json` and push it to the Pi + Grafana Cloud via
`calictl/deploy/push_dashboard.py`, then re-run `python3 -m tools.audit_signals --report`.

Reads the PostToolUse event JSON on stdin; prints `additionalContext` when relevant,
otherwise silent.

### Register it (per-user; `.claude/settings.json` is gitignored)
Add to `.claude/settings.local.json` (or `.claude/settings.json`):
```json
{
  "hooks": {
    "PostToolUse": [
      { "matcher": "Edit|Write",
        "hooks": [ { "type": "command",
          "command": "python3 \"$CLAUDE_PROJECT_DIR/tools/hooks/dashboard-sync-reminder.py\"" } ] }
    ]
  }
}
```
Test: `echo '{"tool_input":{"file_path":"calictl/semantics.py"}}' | python3 tools/hooks/dashboard-sync-reminder.py`
