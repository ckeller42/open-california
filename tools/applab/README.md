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
`CLAUDE.md`), and so does the VIN you type into the app (the pre-commit hook refuses any
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
apkeep -a de.volkswagen.CaliforniaOnTour -d apk-pure $LAB/apks     # same source the decompile skill uses
python3 -m venv $LAB/venv-bumble && $LAB/venv-bumble/bin/pip install 'bumble[android]'
```

`google_apis` (not Play) images are rootable and carry the netsim Bluetooth controller;
emulator ≥ 33.1.4 is required (Bumble's note). macOS may revoke the terminal's access to
removable volumes mid-session (`Operation not permitted` on every read while executables still
run) — grant *Files and Folders → Removable Volumes* to the terminal app, or keep the Bumble venv
on the internal disk.

## Each session

```sh
source $LAB/env.sh
emulator -avd cali34 -no-window -no-audio -no-boot-anim -no-snapshot -gpu swiftshader_indirect \
         -packet-streamer-endpoint default &        # <- the flag that enables netsim Bluetooth
adb wait-for-device && adb install -r $LAB/apks/de.volkswagen.CaliforniaOnTour.apk
adb shell am start -n de.volkswagen.CaliforniaOnTour/de.volkswagen.caliontour.development.CaliforniaOnTourMainActivity

export FAKE_UNIT_VIN=<the 17-char VIN you will type into the app — any syntactically valid one>
mkfifo /tmp/fake_unit.in                            # scenario console (stdin of the peripheral)
tail -f /tmp/fake_unit.in | $LAB/venv-bumble/bin/python tools/applab/fake_unit_ble.py > /tmp/fake_unit.log 2>&1 &
```

The peripheral advertises as `VWCAMPER`, serves the 0xNN00 services from
`protocol/dictionary.yaml`, seeds state from `tests/scenarios/firmware/baseline-0410.json`,
displays passkey **123456** (`FAKE_UNIT_PASSKEY`), and answers the app's VIN check for
`FAKE_UNIT_VIN`. Drive it while it runs:

```
echo "set roof InfoPopUp=5"          > /tmp/fake_unit.in    # any dictionary field, notifies subscribers
echo "set vehicle TerminalOneFive=1" > /tmp/fake_unit.in    # ignition on (roof page needs it)
echo "raw airheater 1050003c0c0000"  > /tmp/fake_unit.in    # replace a whole state frame
echo "show airheater"                > /tmp/fake_unit.in    # decoded state -> log
```

`fake_unit.log` carries `READ <fn>` / `WRITE <fn> <hex> -> state` lines — the WRITE lines are
the app's real frames. `BUMBLE_LOGLEVEL=DEBUG` adds the ATT/SMP trace (large).

### Pairing the app (once per emulator image)

In the app: onboarding → Vehicle tab → *Add vehicle* → enter `FAKE_UNIT_VIN` (online validation
fails → pick model *California* + equipment *Ocean* manually) → *Set up remote control* → grant
the nearby-devices permission → *Connect now*. The app reads `1002`, compares it with
`sha256(VIN)[-16:]` and would otherwise stop with *"Wrong vehicle found"*. It then reads the
auth-gated `1004`, Android starts LE passkey pairing and posts a **notification** ("Pairing
request") — you have ~30 s: expand the shade, tap it, type `123456`, tap OK:

```sh
adb shell cmd statusbar expand-notifications; python3 tools/applab/adbui.py tap "Pairing request"
adb shell input tap 536 988; adb shell input text 123456; python3 tools/applab/adbui.py tap "^OK$"
```

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

What makes a restart survivable (all in `fake_unit_ble.py`):

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
  *"Connection not possible"* against the new fake. When that happens, restart the emulator too
  (`labctl.sh down` then `up`) — a fresh netsim clears the twin, and the bond still holds so it's a
  reconnect, not a re-pair.
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
