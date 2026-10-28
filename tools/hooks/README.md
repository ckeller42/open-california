# Claude Code hooks

## block-vendor-material.py

A **PreToolUse** hook (matcher `Edit|Write`): the *local* enforcement of AGENTS.md's "never commit
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

## finding-artifact-sync.py

A **PostToolUse** hook (matcher `Edit|Write`): a protocol/semantics *finding* usually has to be
mirrored into several artifacts that do NOT update themselves. When an interpretation/mapping file
is edited (`calictl/semantics.py`, `calictl/control.py`, `calictl/overrides.py`,
`protocol/dictionary.yaml`, `protocol/signals.yaml`) it injects a checklist: the web GUI
(`calictl/webui/app.js`), the RE docs (`docs/business-logic/` note + evidence-ledger tier + a dated
DECISIONS entry), `ui/screens/*.yaml`, the protocol sequence diagrams, the Grafana dashboard, and
tests + the auditor. For `dictionary.yaml`/`overrides.py` edits it also flags the REQUIRED codec
regen (`tools.gen_codec_vectors` + `tools.gen_c_dict`) that the codec-parity CI enforces.

`calictl/pairing.py` (the pairing SM) and `calictl/device.py` (heartbeat/aux-char constants) get a
narrower, codec-only reminder instead of the full checklist above (no GUI/docs/dashboard finding
applies to them): REQUIRED regen `tools.gen_c_dict`, stage `csrc/codec_chars.h` +
`csrc/pairing_consts.h` — these feed the ESP32 firmware's generated C headers (#154).

Reads the PostToolUse event JSON on stdin; prints `additionalContext` when relevant,
otherwise silent.

### Register it (per-user; `.claude/settings.json` is gitignored)

Add to `.claude/settings.local.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      { "matcher": "Edit|Write",
        "hooks": [ { "type": "command",
          "command": "python3 \"$CLAUDE_PROJECT_DIR/tools/hooks/finding-artifact-sync.py\"" } ] }
    ]
  }
}
```

Test: `echo '{"tool_input":{"file_path":"protocol/dictionary.yaml"}}' | python3 tools/hooks/finding-artifact-sync.py`

## enigma-update-reminder.py (PostToolUse: Bash)

Fires when a Bash command reads/greps/traces the decompiled app sources (or runs jadx/baksmali/
vineflower) — injects a reminder to record any newly-understood class/method into the private
`mapping.enigma` (the standing "mapping grows monotonically" rule). Stays silent while you're
editing the mapping itself or on unrelated commands. Wire it as a PostToolUse `Bash` matcher.

## docs-sync-reminder.py

A **PostToolUse** hook (matcher `Edit|Write`): when runtime code changes (`firmware/components/`,
`calictl/webui/`, `csrc/`, the rest of `calictl/`; tests excluded), it names the docs page that
describes it — `docs/firmware.md` + an evidence-ledger row for the firmware, the UI page +
`strings.de.js` for the web UI, and so on. The enforcing half is the CI check `docs-touched`
(`tools/docs_touched.py`, `R_DOCS_TOUCHED`): a PR that changes runtime code without a change under
`docs/`, a Markdown file or `protocol/signals.yaml` fails unless its description has a line
`Docs: n/a — <reason>`.

Register it like the others (`.claude/settings.local.json`, `PostToolUse`, matcher `Edit|Write`,
command `python3 "$CLAUDE_PROJECT_DIR/tools/hooks/docs-sync-reminder.py"`).

Test: `echo '{"tool_input":{"file_path":"x/firmware/components/a.c"}}' | python3 tools/hooks/docs-sync-reminder.py`

## block-no-verify.py (PreToolUse: Bash)

Refuses a git command that skips the git hooks: `--no-verify` on `commit`/`push`/`merge`/
`cherry-pick`/`rebase`/`am`, and `git commit -n` (alone or in a cluster like `-qn`; `push -n` is a
dry run and passes). The pre-commit hooks are the local copy of CI's lint/format/type gates; a
bypassed commit is how ruff-format, mypy and markdownlint failures still reached CI. Each command
of a `&&`/`;`/`|` chain is checked on its own; quoted text (`echo '… --no-verify'`) is not a command.
Exit 2 + reason on stderr blocks the call; fails open on a malformed event.

```json
{ "hooks": { "PreToolUse": [
  { "matcher": "Bash",
    "hooks": [ { "type": "command",
      "command": "python3 \"$CLAUDE_PROJECT_DIR/tools/hooks/block-no-verify.py\"" } ] } ] } }
```

Test: `echo '{"tool_input":{"command":"git commit -n -m x"}}' | python3 tools/hooks/block-no-verify.py; echo $?` (want `2`)

## ruff-on-edit.py (PostToolUse: Edit|Write)

Runs `ruff check --fix` and `ruff format` on the edited `.py` file with the repo's config, so a
format or autofixable lint finding is fixed at the edit instead of at the commit hook (or in CI).
What ruff cannot fix comes back as `additionalContext`. Silent for other files or without ruff
(`python3 -m ruff`, from `requirements-dev.txt`).

```json
{ "hooks": { "PostToolUse": [
  { "matcher": "Edit|Write",
    "hooks": [ { "type": "command",
      "command": "python3 \"$CLAUDE_PROJECT_DIR/tools/hooks/ruff-on-edit.py\"" } ] } ] } }
```

Both are tested in `tests/test_agent_hooks.py`.
