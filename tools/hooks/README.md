# Claude Code hooks

## block-vendor-material.py
A **PreToolUse** hook (matcher `Edit|Write`): the *local* enforcement of CLAUDE.md's "never commit
the APK, decompiled sources (`decompile/`), VW manuals (`manuals/`), or secrets (`*.env`)" rule. It
mirrors the `no-vendor-material` CI job's patterns but blocks the **write** before the file exists,
instead of failing CI after it's committed and pushed. Refuses any Edit/Write whose path is a
vendor/binary/secret path (`.apk/.dex/.cvr/.pcap…`, `decompile/`, `manuals/`, `ui/assets/svg/`,
`*.env`) or whose content carries the real vehicle MAC (OUI `20:81:9A`) or a VIN.

Reads the PreToolUse event JSON on stdin; **exit 2 + reason on stderr blocks the call**, silent
(exit 0) otherwise. Fails open on a malformed event (never breaks the tool).

### Register it (per-user; `.claude/settings.json` is gitignored)
Add to `.claude/settings.local.json`:
```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Edit|Write",
        "hooks": [ { "type": "command",
          "command": "python3 \"$CLAUDE_PROJECT_DIR/tools/hooks/block-vendor-material.py\"" } ] }
    ]
  }
}
```
Test: `echo '{"tool_input":{"file_path":"decompile/x.smali"}}' | python3 tools/hooks/block-vendor-material.py; echo $?` (want `2`)

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

## enigma-update-reminder.py (PostToolUse: Bash)

Fires when a Bash command reads/greps/traces the decompiled app sources (or runs jadx/baksmali/
vineflower) — injects a reminder to record any newly-understood class/method into the private
`mapping.enigma` (the standing "mapping grows monotonically" rule). Stays silent while you're
editing the mapping itself or on unrelated commands. Wire it as a PostToolUse `Bash` matcher.
