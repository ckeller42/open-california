# App lab — the real CaliforniaOnTour app against a fake camper unit

Run the vendor's **Android app** in an emulator and let it talk to a **fake camper unit**
that serves calictl's own protocol model over BLE. No van, no phone, no radio: the emulator's
virtual Bluetooth controller (netsim) is bridged to a Python peripheral built on Google's
[Bumble](https://google.github.io/bumble/) stack, and that peripheral wraps
`tools/mock_unit.py` — the same state model the e2e suite uses.

Why it exists:

- **See every app screen in any state.** Push a register value into the fake unit and the app
  renders it — roof alert dialogs, heater status lines, cooler gates — without waiting for the
  van to be awake or reproducing a fault on the road.
- **Diff the app against calictl.** Every frame the app writes lands in the mock's audit trail,
  so `control.build()` can be compared with the real thing, field by field. First session's
  yield: the app fills untargeted fields with each field's leave-unchanged default (heater
  `HeatingLevel 11 / RunningTime 127 / TimerHour 31 / TimerMin 63`), it streams the roof
  `SafetyCounter` while its roof page is open, and it expects `SafetyCounterValid` back — three
  things the mock did not model until this lab exposed them (`tests/test_mock_fidelity.py`).
- **Ground truth for vocabulary and gating** (the unit's screen is the other ground truth):
  status texts, dialog wording, which switches exist, and what a code means.

Everything below is local tooling. **Nothing from the APK or the emulator is committed** — the
APK, the decompile, screenshots and the SDK all live outside the repo (see the hard rules in
`AGENTS.md`), and so does the VIN you type into the app (the pre-commit hook refuses any
17-character VIN in a tracked file). Own-account, own-vehicle interoperability research only.

## One-time setup (macOS, Apple Silicon)

Put the big pieces on an external disk — the SDK + one system image is ~5 GB:

```sh
LAB=/Volumes/External/android-lab            # or wherever; export ANDROID_* accordingly
mkdir -p $LAB/{sdk,avd,apks}
curl -sSL -o /tmp/ct.zip https://dl.google.com/android/repository/commandlinetools-mac_arm64-<build>_latest.zip
unzip -q /tmp/ct.zip -d /tmp/ct && mkdir -p $LAB/sdk/cmdline-tools && mv /tmp/ct/cmdline-tools $LAB/sdk/cmdline-tools/latest
cat > $LAB/env.sh <<EOF
export ANDROID_SDK_ROOT=$LAB/sdk ANDROID_HOME=$LAB/sdk
export ANDROID_USER_HOME=$LAB/.android ANDROID_AVD_HOME=$LAB/avd ANDROID_EMULATOR_HOME=$LAB/.android
export PATH=\$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:\$ANDROID_SDK_ROOT/platform-tools:\$ANDROID_SDK_ROOT/emulator:\$PATH
EOF
source $LAB/env.sh
yes | sdkmanager --licenses >/dev/null
sdkmanager "platform-tools" "emulator" "build-tools;35.0.0" "platforms;android-34" "system-images;android-34;google_apis;arm64-v8a"
echo no | avdmanager create avd -n cali34 -k "system-images;android-34;google_apis;arm64-v8a" -d pixel_6
apkeep -a de.volkswagen.CaliforniaOnTour -d apk-pure $LAB/apks     # same source as the private decompile pipeline (docs/protocol.md)
python3 -m venv $LAB/venv-bumble && $LAB/venv-bumble/bin/pip install 'bumble[android]'
```

`google_apis` (not Play) images are rootable and carry the netsim Bluetooth controller;
emulator ≥ 33.1.4 is required (Bumble's note). macOS may revoke the terminal's access to
removable volumes mid-session (`Operation not permitted` on every read while executables still
run) — grant *Files and Folders → Removable Volumes* to the terminal app, or keep the Bumble venv
on the internal disk.

## One-time setup (Linux x86_64 with KVM — thinky)

The APK ships x86_64 native libraries, so on an x86_64 Linux host the emulator runs the
`google_apis;x86_64` image natively under KVM, headless. `tools/applab/setup_linux.sh` installs
everything idempotently under `~/android-lab` (SDK + emulator + image, AVD `lab34` on a `pixel_6`
profile, `grpcio`/`protobuf` into the Bumble venv, the APK by `scp` from `pi@buspi:~/apks/`). It
stops with the exact command when a prerequisite is missing: group `kvm`, Java 17, `unzip`, and
`loginctl enable-linger` (netsim's `netsim.ini` lives in `$XDG_RUNTIME_DIR`, which systemd removes
when the last ssh session ends).

```sh
tools/applab/setup_linux.sh
export LAB_DIR=~/android-lab AVD=lab34 BUMBLE_PY=~/esp-venv/bin/python
. $LAB_DIR/env.sh
FAKE_UNIT_VIN=$(cat $LAB_DIR/vin) tools/applab/labctl.sh up
adb install -r "$(ls $LAB_DIR/apks/*.apk | head -1)"
```

The emulator flag that bridges netsim Bluetooth is `-packet-streamer-endpoint default`
(`labctl.sh up` passes it); boot it by hand with
`emulator -avd lab34 -no-window -no-audio -no-snapshot -gpu swiftshader_indirect -packet-streamer-endpoint default`.

`$LAB_DIR/vin` (mode 600) holds the test VIN the app was set up with — never print it, never
commit it. **If buspi is offline when you run setup**, the APK copy is skipped with a note; copy it
by hand once buspi is back (`scp pi@buspi:~/apks/*.apk ~/android-lab/apks/`), then `adb install -r`.

**Radio separation.** On thinky a second fake unit serves the ESP satellite over the UB500 radio
(find it by USB id `2357:0604` → `hci-socket:<N>`, the index moves across reboots); it runs the same
script, so `labctl.sh` and `walk.py` only ever signal the pid in `$TMPDIR/applab/fake_unit.pid` —
the app's fake is on `android-netsim`, the ESP's on the UB500. Never point both fakes at one
transport, never start NetworkManager, never touch the `wlx*`/`enp1s0` interfaces.

## Each session

```sh
source $LAB/env.sh
emulator -avd cali34 -no-window -no-audio -no-boot-anim -no-snapshot -gpu swiftshader_indirect \
         -packet-streamer-endpoint default &        # <- the flag that enables netsim Bluetooth
adb wait-for-device && adb install -r $LAB/apks/de.volkswagen.CaliforniaOnTour.apk
adb shell am start -n de.volkswagen.CaliforniaOnTour/de.volkswagen.caliontour.development.CaliforniaOnTourMainActivity

export FAKE_UNIT_VIN=<the 17-char VIN you will type into the app — any syntactically valid one>
mkdir -p "${TMPDIR:-/tmp}/applab"
$LAB/venv-bumble/bin/python tools/applab/fake_unit_ble.py > "${TMPDIR:-/tmp}/applab/fake_unit.log" 2>&1 &
```

(`tools/applab/labctl.sh up` does the emulator + fake + app launch in one go — see
[Surviving a restart](#surviving-a-restart-labctl). It writes the log to the same place.)

The fake makes its own **scenario console**: a named FIFO at `$FAKE_UNIT_FIFO`, default
`${TMPDIR:-/tmp}/applab/fake_unit.in`, read in a background thread (not stdin — asyncio's stdin
reader fails on macOS when stdin is a FIFO or redirected under `nohup`). `$TMPDIR` on macOS is a
per-user `/var/folders/…` path, not `/tmp`, so use the variable rather than a literal path.

The peripheral advertises as `VWCAMPER` from a rotating resolvable private address over the fixed
identity `FAKE_UNIT_ADDR` (rotation period `FAKE_UNIT_RPA_S`, default 600 s — the real unit rotated
every ~10 min), serves the 0xNN00 services from `protocol/dictionary.yaml`, seeds state from
`tests/scenarios/firmware/baseline-0410.json`, and answers the app's VIN check for
`FAKE_UNIT_VIN`. It displays a **fresh passkey per pairing attempt**, printed to the log as
`### PASSKEY nnnnnn — the unit shows this; type it on the central ###`; set `FAKE_UNIT_PASSKEY` to
pin a fixed code instead (e.g. for a scripted pairing wizard). Drive it while it runs:

```sh
FIFO="${FAKE_UNIT_FIFO:-${TMPDIR:-/tmp}/applab/fake_unit.in}"
echo "set roof InfoPopUp=5"          > "$FIFO"    # any dictionary field, notifies subscribers
echo "set vehicle TerminalOneFive=1" > "$FIFO"    # ignition on (roof page needs it)
echo "raw airheater 1050003c0c0000"  > "$FIFO"    # replace a whole state frame
echo "show airheater"                > "$FIFO"    # decoded state -> log
echo "pair off"                      > "$FIFO"    # close the "Gerät verbinden" screen (refuses new bonds)
echo "pair on"                       > "$FIFO"    # reopen it
echo "rotate"                        > "$FIFO"    # advertise from a fresh private address now
echo "forget"                        > "$FIFO"    # drop every stored bond, like "Bluetooth zurücksetzen"
echo "q"                             > "$FIFO"    # exit immediately (no clean radio power-off — prefer labctl.sh down)
```

`fake_unit.log` (`${TMPDIR:-/tmp}/applab/fake_unit.log`) carries `READ <fn>` / `WRITE <fn> <hex> -> state` lines — the WRITE lines are
the app's real frames. `BUMBLE_LOGLEVEL=DEBUG` adds the ATT/SMP trace (large).

### Pairing the app (once per emulator image)

The app's pairing flow and error UX were cross-checked against this fake on 2026-09-27 (passkey
association model, reconnect over a rotated address, re-pair after `forget`, refusal while
`pair off`): see the "Pairing" section of `docs/business-logic/protocol-crosscheck-applab.md`.
calictl's own wizard is checked against the same fake in CI (`tests/test_pairing_link.py`, and
real BlueZ in the `pairing-real-stack` VM job — `docs/simulation-and-testing.md`).

In the app: onboarding → Vehicle tab → *Add vehicle* → enter `FAKE_UNIT_VIN` (online validation
fails → pick model *California* + equipment *Ocean* manually) → *Set up remote control* → grant
the nearby-devices permission → *Connect now*. The app reads `1002`, compares it with
`sha256(VIN)[-16:]` and would otherwise stop with *"Wrong vehicle found"*. It then reads the
auth-gated `1004`, Android starts LE passkey pairing and posts a **notification** ("Pairing
request") — you have ~30 s: expand the shade, tap it, read the code from `### PASSKEY nnnnnn`
in `fake_unit.log` (or the fixed one from `FAKE_UNIT_PASSKEY`, if set), type it, tap OK:

```sh
adb shell cmd statusbar expand-notifications; python3 tools/applab/adbui.py tap "Pairing request"
adb shell input tap 536 988; adb shell input text <passkey from fake_unit.log>; python3 tools/applab/adbui.py tap "^OK$"
```

Or let `tools/applab/pair_wizard.py` walk the wizard sheets and type the code. It types
`FAKE_UNIT_PASSKEY` when that is set, otherwise the last `### PASSKEY nnnnnn` line in the fake's log
(`FAKE_UNIT_LOG`, default `${TMPDIR:-/tmp}/applab/fake_unit.log`). If you pin a code, export the
**same** `FAKE_UNIT_PASSKEY` for both processes — the fake (before `labctl.sh up`/`fake`, since it is
read at start) and `pair_wizard.py` — otherwise the fake shows a fresh random code the wizard can't
know. If you run the fake with a log elsewhere, point `FAKE_UNIT_LOG` at it.

The bond persists on both sides (Android's bond store + the fake's `JsonKeyStore` at
`tools/applab/.fake_unit_keys.json`, gitignored), so later sessions reconnect without the dance.
The peripheral drops a link that carries no `1003` heartbeat for 15 s once the link has carried a
beat (`FAKE_UNIT_HEARTBEAT_TIMEOUT_S`) — like the real unit, and because a connected peripheral
cannot advertise (a stale link makes the app report *"No vehicle found"*). Before the first beat
it allows `FAKE_UNIT_PAIRING_GRACE_S` (90 s) so a slow passkey entry doesn't get the link dropped
mid-pairing.

## Surviving a restart (labctl)

The lab drifts apart across a restart, so use the one-command wrapper instead of relaunching parts
by hand — **run it yourself**, from a terminal with Removable-Volumes access (an assistant sandbox
often can't read the external volume):

```sh
FAKE_UNIT_VIN=<the VIN you paired> tools/applab/labctl.sh up      # emulator + fake + app, idempotent
tools/applab/labctl.sh status                                     # what's running
tools/applab/labctl.sh down                                       # stop the fake CLEANLY, then the emulator
```

| `labctl.sh` subcommand | What it does |
|---|---|
| `up` | start the emulator if it is down (headless, netsim Bluetooth), start the fake if it is down, launch the app. Idempotent. |
| `fake` | (re)start only the fake unit: `SIGTERM` the one in the pid file, then start it again (needs `FAKE_UNIT_VIN`) |
| `status` (default) | emulator / fake / `netsimd` up or down, plus the scenario-console command line |
| `down` | `SIGTERM` the fake (so netsim drops its radio), kill the emulator, then stop `netsimd` |

State lives in `${TMPDIR:-/tmp}/applab/`: `fake_unit.log`, `fake_unit.pid`, `emulator.log`, and the
`fake_unit.in` FIFO. Under `labctl.sh` the FIFO path is always that one (it passes
`FAKE_UNIT_FIFO` to the fake itself, so an exported `FAKE_UNIT_FIFO` is ignored there).

| Variable | Read by | Default | Meaning |
|---|---|---|---|
| `LAB_DIR` | `labctl.sh` | `/Volumes/External/android-lab` | lab install root; `$LAB_DIR/env.sh` is sourced for `adb`/`emulator` on `PATH` |
| `BUMBLE_PY` | `labctl.sh` | `$LAB_DIR/venv-bumble/bin/python` | a Python with `bumble[android]` installed |
| `AVD` | `labctl.sh` | `cali34` | emulator AVD name |
| `APP_ID` / `APP_ACTIVITY` | `labctl.sh` | `de.volkswagen.CaliforniaOnTour` / `de.volkswagen.caliontour.development.CaliforniaOnTourMainActivity` | app package / launch activity |
| `TMPDIR` | `labctl.sh`, fake, `pair_wizard.py` | `/tmp` | parent of the `applab/` state dir |
| `FAKE_UNIT_VIN` | fake (required by `labctl.sh up`/`fake`) | empty | the VIN you type into the app; `1002` serves `SHA-256(VIN)[-16:]`. Never committed. |
| `FAKE_UNIT_PASSKEY` | fake, `pair_wizard.py` | unset: fresh random code per attempt (fake); last `### PASSKEY` in the log (wizard) | pin a fixed 6-digit code — export it for both |
| `FAKE_UNIT_ADDR` | fake | `C0:FF:EE:CA:11:F0` | stable identity address (keep it: changing it forces a re-pair) |
| `FAKE_UNIT_KEYSTORE` | fake | `tools/applab/.fake_unit_keys.json` (gitignored) | Bumble `JsonKeyStore` for bonds |
| `FAKE_UNIT_FIFO` | fake | `${TMPDIR:-/tmp}/applab/fake_unit.in` | scenario console FIFO (created if missing) |
| `FAKE_UNIT_LOG` | `pair_wizard.py` | `${TMPDIR:-/tmp}/applab/fake_unit.log` | the fake's log, tailed for the passkey and the result |
| `FAKE_UNIT_RPA_S` | fake | `600` | resolvable-private-address rotation period, seconds |
| `FAKE_UNIT_HEARTBEAT_TIMEOUT_S` | fake | `15` | drop a link with no `1003` beat for this long (after the first beat) |
| `FAKE_UNIT_PAIRING_GRACE_S` | fake | `90` | the same, before the first beat (passkey entry) |
| `FAKE_UNIT_RECORD` | fake | unset (off) | append every GATT/link event to this JSONL file (`walk.py` sets it; `1002` written as `<vin-hash>`, passkeys never) |
| `BUMBLE_LOGLEVEL` | fake | `INFO` | `DEBUG` adds the ATT/SMP trace |
| `OC_REPO` | fake | the checkout containing the script | repo root put on `sys.path` |
| `ADB` / `ANDROID_SDK_ROOT` | `adbui.py` | `$ANDROID_SDK_ROOT/platform-tools/adb`, else `adb` | which `adb` to run |
| `APPLAB_SHOTS` | `adbui.py` | `./applab-shots` | screenshot directory (keep it out of the repo) |

What makes a restart survivable (`fake_unit_ble.py` + the shared `tools/fake_unit_peripheral.py`):

- **Stable address** (`FAKE_UNIT_ADDR`, default `C0:FF:EE:CA:11:F0`) + **persistent keystore**
  (`FAKE_UNIT_KEYSTORE`) → the phone's bond still matches after the fake restarts, so the app shows
  **Connect** (a reconnect), not *Set up remote control* (a fresh pair), and no passkey dialog.
  Verified: a fresh pair writes `.fake_unit_keys.json` and the app then treats the vehicle as bonded
  across a fake restart.
- **Clean shutdown**: the fake powers its radio off on `SIGTERM`/`SIGINT`. A `kill -9` skips this and
  guarantees a **dead twin** registered at the same address. Always stop it with `labctl.sh down`
  (or `pkill -TERM`), never `-9`.
- **Known limitation** — reconnecting to a *restarted* fake is not fully reliable: netsimd can keep
  the old radio registered at the same address even after a clean `power_off()`, so the app reports
  *"Connection not possible"* against the new fake and the new process logs **nothing at all**
  (that silence is the giveaway — the packets never reach it). `netsimd` is a **separate process
  that outlives the emulator**, so killing the emulator alone does not clear it; `labctl.sh down`
  stops netsimd too, and `labctl.sh status` shows it (netsimd up while the fake is down is the
  ghost smell). Then `up` again — the bond still holds, so it's a reconnect, not a re-pair.
  Do **not** work around it by changing `FAKE_UNIT_ADDR`: that escapes the ghost but silently
  forces a full re-pair.
- If a re-pair *is* needed (address changed, keystore wiped, or a stuck twin): in the app
  **Account → Vehicle → Bluetooth Reset**, forget `VWCAMPER` in Android's Bluetooth settings (on a
  rootable emulator a stubborn bond clears with BT off + `rm /data/misc/blue*/bt_config.*` + BT on),
  then rerun `tools/applab/pair_wizard.py` (scripts the wizard sheets + passkey).

> Fragility that remains, by design: macOS may revoke the terminal's access to the external volume
> (re-grant *Files and Folders → Removable Volumes*), and netsim occasionally needs the emulator
> fully restarted (above). Budget ~5 min of setup for an occasional deep-dive; the lab isn't meant
> for routine unattended use.

## Driving the UI

`adbui.py` is a tiny uiautomator wrapper: `dump` (visible texts), `tap "<regex>"` (by text or
content-description), `tree` (nodes with bounds/clickable/checked), `shot <name>` (PNG into
`$APPLAB_SHOTS`, default `./applab-shots` — keep it out of the repo). The app is Compose, so
switches show up as clickable `View`s with `checked`; sliders and the roof press-and-hold switch
are plain images — drive them by coordinates.

## Recording a scenario (`walk.py`)

`tools/applab/walk.py <scenario>…` records the app doing one scripted thing
(`tools/applab/scenarios.py`) into `tests/vectors/app/<scenario>.jsonl`, which CI replays against
`control.build` (`tests/test_app_recordings.py`). Per scenario it SIGTERMs the lab's fake, starts a
fresh one with `FAKE_UNIT_RECORD`, runs the steps with a screenshot after each
(`$TMPDIR/applab/shots/`), SIGTERMs the fake and writes the header + the merged events. A failed
step stops the run, leaves the fake running and prints the last screenshot — fix the selector or
the XY point and run it again; nothing retries.

```sh
. $LAB_DIR/env.sh
~/esp-venv/bin/python tools/applab/walk.py cooler airheater
python3 -m tools.capture_diff tests/vectors/app/cooler.jsonl --recording    # the replay, write by write
python3 -m tools.app_parity tests/vectors/app/session.jsonl                  # app / calictl / ESP lifecycle
```

`--live --esp-fifo <the ESP fake's FIFO>` also records, after every step, the app's visible texts
and the ESP satellite's `/api/state` + page texts; `set`/`raw` console lines then go to both fakes
(link commands such as `forget` only to the app's). Recordings are made by hand once per APK
version; commit them with the evidence-ledger rows they flip.

### Measuring XY points

Sliders, the roof hold and the time wheels have no text to tap, so `scenarios.XY` holds named
screen points, valid only for the screen in `scenarios.XY_SCREEN` (`walk.py` refuses any other).
To measure one: open the screen, run `python3 tools/applab/adbui.py tree`, take the control's
bounds `[x1, y1, x2, y2]` and use the centre for a tap; for a slider use
`x = x1 + (x2 - x1) * fraction` at the centre `y` (level 8 of 1–10 → fraction 7/9); a long press
appends the hold in ms; a wheel row is `(x, y, x, y - 145)`. Check the point with
`adb shell input tap x y` and a screenshot before committing it.

## What the app itself told us (2026-09-16)

- `1002` = last 16 bytes of SHA-256 of the VIN string (`ny/c.java` case 8) — the "wrong
  vehicle" check.
- Immediate heating ON → `3d7b007f1f3f`, then a neutral `3f7b007f1f3f` 500 ms later; no
  confirmation dialog. Continuous heating OFF (only after the "Turn off continuous heating?"
  dialog) → `0f7b007f1f3f` + neutral. Untargeted fields ride at their leave-unchanged defaults.
- Roof page open → `Up=0 Down=0 SafetyCounter=n` every ~500 ms; needs `SafetyCounterValid` back;
  refuses to open its controls without terminal 15 ("Switch on the ignition").
- Roof `InfoPopUp` → tile/dialog: 1 moved too often, 2/3/12 "Function currently in use",
  4 unknown error/workshop, 5 roof open while driving (Warning), 6 jammed or locked,
  7 secure manually, 9 "Only possible when stationary", 10 currently unavailable,
  11 battery low/run engine; 8, 13, 14 nothing.
- Heater page: temperature slider 1–9 + **HI**, run time slider **10–120**, *Immediate heating*
  switch, *Timer* expander, *Permanent Heating* switch always present (greyed "can be activated
  only in the vehicle" when off). Status: "Inactive", "Active • N min remaining",
  "Active • Continuous heating".
