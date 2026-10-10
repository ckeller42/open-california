---
name: app-lab
description: Use when you need the real CaliforniaOnTour app running against the fake camper unit — to capture a screen or dialog in a given unit state, to record the BLE frame the app writes for an action, to cross-check calictl or the decompile specs against the app, or when the lab is "up" but the fake unit, adb, or the app cannot be reached from this session.
---

# The California app lab (emulator + netsim + Bumble fake unit)

The real app (5.0.8) runs in an Android emulator whose virtual radio (netsim) is bridged to
`tools/applab/fake_unit_ble.py`, a Bumble peripheral serving `tools/mock_unit.py` over the unit's real
GATT. Every app screen, dialog and write frame is protocol evidence (tier **APP-OBSERVED**). Generic
recipe and pitfalls: **REQUIRED BACKGROUND:** `android-ble-app-lab`. This skill is the runbook for
THIS repo and THIS machine.
Real phone + real unit instead (frames, link drops and timing only the van shows): the
`phone-app-lab` skill.

## Who launches what (the disk-permission split)

This session usually runs under agent-deck's **tmux**, which macOS never grants *Removable Volumes*
access, so `/Volumes/External/android-lab` (SDK, AVD, APK, the lab's venv) is **unreadable here**
(`Operation not permitted`) — but binaries on it still **execute**, and `adb`, the fake unit and the
app need nothing readable from it. So:

| Step | Who | How |
|---|---|---|
| Emulator | **owner**, from a plain iTerm2 tab (not inside tmux) | `cd ~/src/open-california && tools/applab/labctl.sh up` — it boots the emulator, then complains `FAKE_UNIT_VIN: set FAKE_UNIT_VIN` (ignore) |
| Fake unit, adb, app | **you**, from this session | the resume recipe below |

Permanent fix (owner, kills every agent-deck session): Full Disk Access for `/opt/local/bin/tmux`, then
restart the tmux server.

## Resume recipe (idempotent; ~30 s)

```bash
cd ~/src/open-california
export ADB=/Volumes/External/android-lab/sdk/platform-tools/adb PATH=/Volumes/External/android-lab/sdk/platform-tools:$PATH
S=$TMPDIR/applab; mkdir -p "$S"; [ -p "$S/fake_unit.in" ] || mkfifo "$S/fake_unit.in"
$ADB root >/dev/null; sleep 2                          # google_apis image: rootable
# the VIN the app was set up with lives in the app's own data — never print it, never ask the owner
VIN=$($ADB shell "grep -rhoaE 'WV2[A-HJ-NPR-Z0-9]{14}' /data/data/de.volkswagen.CaliforniaOnTour/ 2>/dev/null | head -1" | tr -d '\r')
if ! pgrep -f applab/fake_unit_ble.py >/dev/null; then
  n0=$(grep -c . "$S/fake_unit.log" 2>/dev/null || echo 0)              # only accept a NEW readiness line
  FAKE_UNIT_PASSKEY=123456 FAKE_UNIT_VIN="$VIN" FAKE_UNIT_FIFO="$S/fake_unit.in" \
    nohup /opt/local/bin/python3.13 tools/applab/fake_unit_ble.py >>"$S/fake_unit.log" 2>&1 &
  for i in $(seq 1 30); do
    tail -n +$((n0+1)) "$S/fake_unit.log" | grep -q "advertising from" && break
    pgrep -f applab/fake_unit_ble.py >/dev/null || { echo "fake unit died:"; tail -5 "$S/fake_unit.log"; break; }
    sleep 1
  done
fi; unset VIN
$ADB shell am start -n de.volkswagen.CaliforniaOnTour/de.volkswagen.caliontour.development.CaliforniaOnTourMainActivity
ui(){ /opt/local/bin/python3.13 tools/applab/adbui.py "$@"; }          # dump | tap "<regex>" | tree | shot <name>
$ADB shell input tap 745 2290; sleep 3; ui tap '^Connect$'             # Vehicle tab, reconnect on the stored bond
```

Facts the recipe relies on: only `/opt/local/bin/python3.13` has Bumble (+ `grpcio` for the netsim
transport; `pip install --user grpcio protobuf` if missing); `$TMPDIR/netsim.ini` is shared with the
owner's shell (same user); the fake's keystore is `tools/applab/.fake_unit_keys.json` (internal disk,
gitignored) so the app's bond survives restarts; `BUMBLE_LOGLEVEL=DEBUG` on the fake prints every
ATT/SMP PDU (needed for pairing questions, noisy otherwise); macOS has no `timeout` command.

## Controlling the unit state (the fake's FIFO console)

`echo '<cmd>' > $TMPDIR/applab/fake_unit.in`; replies land in `fake_unit.log`.

| Command | Effect |
|---|---|
| `set <function> <Field>=<value>` | change a decoded field (names: `protocol/dictionary.yaml`); the app sees the notification at once |
| `show <function>` / `raw <function> <hex>` | print / replace a state frame |
| `pair on\|off` | model "Gerät verbinden" (off = new pairings refused with PAIRING_NOT_SUPPORTED) |
| `rotate` / `forget` | rotate the advertising RPA / drop the unit-side bonds (the app then re-pairs on the next Connect) |

State injection that is known to render: `set airheater ErrorCode=2` (Low-fuel toast), `set cooler
Error=1` (workshop dialog), `set roof Position=…`/`InfoPopUp=9`, `set campingmode …`, `set general
<version fields>` (→ the app refuses with "App version outdated", ex080 — restore the originals from
`show general` afterwards). ECO appears with `set energy PvInstalled=1` but only on the SECOND visit to the Energy Mode picker (stale first read, 2026-10-06).

## Driving the app

- `ui dump` lists visible texts; `ui tree` gives bounds + `click`/`chk=`; `ui tap '^Connect$'`.
- **Time wheels** (heater "Start heating at"): `adb shell input draganddrop X Y1 X Y2 1500` one row
  (~145 px) per step — `input swipe` does NOT turn them. Confirm with the wheel's OK; the write lands
  in the fake log as `WRITE <char> <hex>` (09:31 → `3f7b007f091f`).
- Custom toggles are `checkable="true"` Views, not `Switch` widgets. "More info on this model" opens
  Chrome — press back until the app is in front. Never log in, never accept the Maps consent.
- **Pairing prompt** (after `forget` or a fresh keystore): Android shows a notification, ~30 s window:
  `FAKE_UNIT_PASSKEY=123456 FAKE_UNIT_LOG=$TMPDIR/applab/fake_unit.log /opt/local/bin/python3.13 tools/applab/pair_wizard.py`
  (needs `adb` on PATH). Trigger the request first (tap Connect), then run it.
- Screenshots: `ui shot <name>` → `./applab-shots/` (gitignored) or `APPLAB_SHOTS=<scratch dir>`. Read
  the PNG to review it. Never commit screenshots, the APK, or a VIN.

## Capturing a frame the app writes

1. Start from the app state you need; `grep -c WRITE $TMPDIR/applab/fake_unit.log` to mark the offset.
2. Perform the action; `grep WRITE $TMPDIR/applab/fake_unit.log | tail -3`.
3. Decode it and compare with calictl's own builder — byte-identical, or the difference is a finding:

   ```bash
   /opt/local/bin/python3.13 -c "
   from calictl import control, overrides, protocol
   funcs = protocol.load(); overrides.apply(funcs)
   frame = bytes.fromhex('3f7b007f091f')
   print(control.decode_control(funcs['airheater'], frame))           # TimerHour=9 TimerMin=31, rest sentinel
   print(control.build(funcs, 'airheater', 'timer', '09:31', {}).hex())   # what calictl would send
   "
   ```

   `what` names: see the `:param what:` list in each `calictl/control.py` builder (`_airheater`: power,
   level, runtime, timer, timer_start, …).

## Recording a scenario (walk.py)

Scripted recording beats grepping the log: add a scenario in `tools/applab/scenarios.py`, then on
thinky (Linux lab: `tools/applab/README.md`, "One-time setup") run:

```bash
~/esp-venv/bin/python tools/applab/walk.py <scenario>      # -> tests/vectors/app/<scenario>.jsonl
python3 -m tools.capture_diff tests/vectors/app/<scenario>.jsonl --recording
python3 -m pytest tests/test_app_recordings.py -k <scenario>
```

A failed step names its screenshot; fix the selector or XY point and rerun. Recordings are made by
hand once per APK version. Never `pkill -f` the fake: use `labctl.sh` and the pid file.

## Recording (the owner's rule: every protocol fact lands in the docs in the same PR)

| Fact | Where |
|---|---|
| a claim checked against the app | row in `docs/business-logic/protocol-crosscheck-applab.md` (OBSERVED / CONSISTENT / CONTRADICTED / NOT TESTABLE, screen cited) |
| a frame, gate or dialog text | `control-and-actuation.md` recipe/capture table, `alert-states.md`, the function's doc; tier line in `evidence-ledger.md` ("APP-OBSERVED (date)") |
| what a screen shows | `ui/screens/` — `home`, `vehicle-info`, `energy`, `coolbox`, `air-heater`, `camping-mode`, `lighting`, `roof`, `water`, `notifications`, `self-check`, `settings`, `account`, `connectivity`, `onboarding` (+ `stairs`, `roof-air-condition`, `satellite-antenna` not fitted) `.yaml` — `label_text` + `evidence:`; dead keys marked |
| what a class/method does | `~/src/californiaontour-decompile/mapping.enigma` on buspi (private repo; grows monotonically; commit + push) |
| a new byte-identical frame | a `tools/scenarios/<function>/<name>.yaml` capture-diff scenario |

## Common mistakes

| Symptom | Fix |
|---|---|
| `labctl.sh up` → `env.sh: Operation not permitted` | you are under tmux: owner starts the emulator from plain iTerm2; you do the rest (recipe above) |
| fake prints `ModuleNotFoundError: grpc` | `/opt/local/bin/python3.13 -m pip install --user grpcio protobuf` |
| app shows "Wrong vehicle found" / disconnects 20 ms after MTU | VIN mismatch — the recipe's `grep` failed; check `adb root` succeeded |
| app "No vehicle found" although the fake advertises | a ghost radio in netsim from a hard-killed fake — SIGTERM only; see `android-ble-app-lab` |
| wheel does not turn | `draganddrop`, not `swipe` |
| pairing notification ignored → link dropped after ~30 s | run `pair_wizard.py` within the window; the app silently goes back to "Connect" — tap it again |
| emulator process gone (`adb devices` empty) | it died; only the owner can relaunch it (permission); the fake and netsimd survive |
