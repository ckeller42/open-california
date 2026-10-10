---
name: phone-app-lab
description: Use when the REAL CaliforniaOnTour app on the owner's phone must drive the REAL camper unit — to capture the exact BLE frames, link drops or timing the app produces at the van, to check a calictl frame or a mock assumption against the real unit, or when wireless adb to the phone, the Bluetooth HCI snoop log, or a bugreport pull is not working.
---

# Phone app lab (owner's phone + real unit, driven over wireless adb from buspi)

The real app (`de.volkswagen.CaliforniaOnTour`) on the owner's **Fairphone 6** (stock Android 16)
talks to the **real unit**; Android's HCI snoop log records every frame. You drive the app with
`adb` from buspi and pull the log with a bugreport. A matched frame is tier **CAPTURE** in
`docs/business-logic/evidence-ledger.md`. Emulator + fake unit sibling (any unit state, no van):
the `app-lab` skill. This lab is for what only the real unit can show.

## Rules (privacy + the owner's van)

- **Never commit** a bugreport, `btsnoop_hci.log`, screenshot, VIN, unit MAC (write `20:81:9A…`),
  passkey or pairing code. A bugreport holds the phone's pairing keys and identity. Everything stays
  under buspi `~/applog/`.
- **Screenshots** (real phone or emulator) go ONLY to the private repo `ckeller42/californiaontour-re`
  (`screens/`). open-california gets text and links only: no image or video files.
- **Never open the VIN screens:** Account → Vehicle, and (app 5.4.0.3036) Vehicle tab → Help →
  "Vehicle Settings" — the same page moved. Use the bottom tab **Vehicle** quick actions.
- **Actuation needs the owner's explicit request in the conversation**, per actuation session. A tap
  that writes to the unit without it is refused by the auto-mode permission classifier ("Unrequested
  Commit in a Connected App"). Navigation and screenshots need no request.
- Locked phone → **ask the owner**. Never try to unlock it.
- **Heater:** do not touch it (diesel; short cycles harm it). **"Save light setting", double-tap a
  favourite, factory reset:** do not touch (overwrites owner presets). Each needs explicit owner OK.
- **Roof:** ignition ON, owner present and watching, explicit go for each press.
- Restore every setting you change. Report the end state.
- **Stamp the app version on every capture:** `adb shell dumpsys package de.volkswagen.CaliforniaOnTour
  | grep versionName`. The phone and the emulator lab run 5.4.0.3036 (lab since 2026-10-10); the 5.0.8.3028
  decompile and the committed `tests/vectors/app/` recordings are older.

## One-time setup (owner, on the phone)

Developer options: **Wireless debugging** ON; **Enable Bluetooth HCI snoop log = Enabled** ("Filtered"
blanks the payloads); **Stay awake** ON and the phone on a charger (else adb sees the lock screen).
`setprop persist.bluetooth.btsnooplogmode full` is refused for the shell user on a user build, so
the owner sets the snoop mode. USB to buspi does not work ("usb 1-1-port2: Cannot enable. Maybe the
USB cable is bad?", undervoltage): use wireless adb. `sudo apt-get install -y adb` on buspi, once.

## Quick reference (all on buspi: `ssh buspi`, then …)

| Task | Command |
|---|---|
| pair (once; code expires ~1 min) | owner opens "Pair device with pairing code" → `adb pair <ip>:<pairport> <6-digit code>` |
| connect | `adb connect <ip>:<port>` — the port on the main Wireless debugging screen (≠ pair port; changes when wireless debugging restarts). `adb mdns services` finds nothing on the van LAN: ask the owner |
| snoop log active? | `adb shell cmd bluetooth_manager disable; sleep 3; adb shell cmd bluetooth_manager enable` after the owner set it, then `adb shell dumpsys bluetooth_manager \| grep -i snoop` → `sSnoopLogSettingAtEnable = FULL` |
| launch app | `adb shell monkey -p de.volkswagen.CaliforniaOnTour -c android.intent.category.LAUNCHER 1` |
| screen map | `adb shell uiautomator dump /sdcard/ui.xml; adb exec-out cat /sdcard/ui.xml` → nodes: `text`, `content-desc`, `bounds`, `clickable`, `checked` |
| tap / slider | `adb shell input tap X Y` / `adb shell input swipe X1 Y X2 Y 450` (sliders need a swipe 400–500 ms, a tap does nothing) |
| screenshot | `adb exec-out screencap -p > ~/applog/shot-N.png` (VW app footage: private RE repo `screens/` only) |
| app version | `adb shell dumpsys package de.volkswagen.CaliforniaOnTour \| grep versionName` — record it with the capture |
| mark an action | `echo "T_LABEL $(adb shell date +%H:%M:%S.%N)" >> ~/applog/marks.txt` right BEFORE the tap |
| pull the capture | `cd ~/applog; adb bugreport br-N.zip` (~1–2 min, ~8 MB), then `unzip -o -q br-N.zip FS/data/misc/bluetooth/logs/btsnoop_hci.log -d brN` |
| GATT handle map | `sudo -n sh -c "grep -h 2803 /var/lib/bluetooth/*/cache/<UNIT MAC>" > ~/applog/gatt.txt` |
| decode ATT | `cd ~/open-california; python3 -m tools.applab.phone.snoop_att ~/applog/brN/FS/data/misc/bluetooth/logs/btsnoop_hci.log ~/applog/gatt.txt` |
| link events | `python3 -m tools.applab.phone.snoop_links <btsnoop>` — `0x13` remote user terminated, `0x16` local host terminated, `0x3e` failed to establish |
| buspi's own side | `~/ble.jsonl` (CALICTL_BLE_TRACE) and `journalctl -u calictl` |

## Driving the app

- Bottom tab **Vehicle**: quick-action icons toggle on a tap — Refrigerator box, Lighting, Auxiliary
  air heater (do not), Camping mode — plus Pop-up roof, Level Indicator, Self-check.
- First visits show intro sheets (Skip / Close / "Got it") and a "background connection /
  notifications" sheet → **Cancel**.
- Cooler level slider is greyed while the cooler is off; the cooler timer needs the cooler off.
- **Roof (press-and-hold):** the first open press gets unit InfoPopUp 2 → the app's pre-open safety
  checklist (space above / access board / window or door): OK, then a fresh press within a few
  seconds. **Remote holds do not work:** `input motionevent DOWN…UP`, DOWN+MOVE loops and a 6 s
  `input swipe` each sent only 1–3 move frames, then STOP. The owner holds the button with a finger;
  you record.

## Decoding and comparing

The app uses its cached GATT DB, so the capture has no discovery: `snoop_att` needs `gatt.txt` from
buspi's BlueZ cache (value handle h → uuid, CCCD = h+1). Without it you get `h0011`-style handles.
Times are the raw snoop clock: **check the offset against a mark in `marks.txt`** (one run was 2 h
off) before you match frames to actions.

Compare a captured write with calictl's builder (on buspi, in `~/open-california`):

```bash
python3 -c "
import json, os
from calictl import control, overrides, protocol
funcs = protocol.load(); overrides.apply(funcs)        # without overrides.apply the cooler frame is wrong (fd7700000000)
last = json.load(open(os.path.expanduser('~/.cache/calictl/last_state.json')))['last']
print(control.build(funcs, 'cooler', 'mode', 'quiet', last).hex())   # COOLER_MODES: normal / quiet / timer_quiet
"
```

Byte-identical → CAPTURE row in `evidence-ledger.md`; a difference is a finding. Every protocol fact
lands in the protocol docs in the same PR (see the `app-lab` skill's "Recording" table).

## Common mistakes

| Symptom | Fix |
|---|---|
| decoded writes have empty payloads | snoop mode is "Filtered" → owner sets "Enabled", toggle BT, check `FULL` |
| `adb devices` shows the phone but every dump is the lock screen | "Stay awake" off or phone not charging → ask the owner; do not unlock |
| `adb connect` refused after it worked | wireless debugging restarted → new port from the owner |
| zsh on the Mac: `adb $args` passes one argument | zsh does not word-split `$var` → `${=var}`, or run the script on buspi |
| slider does not move | `input swipe` 400–500 ms, not `tap` |
| `snoop_att` prints only `hXXXX` | pass `gatt.txt` (cached GATT DB, no discovery in the capture) |
| roof moved a little, then STOP | remote hold — the owner must hold it |
| Help → "Vehicle Settings" opened | it shows the VIN (5.4.0.3036): back out at once, never screenshot it |
| tap on a control refused: "Unrequested Commit in a Connected App" | ask the owner to request that actuation; do not work around the classifier |
| a screenshot staged in open-california | unstage it; screenshots go to `californiaontour-re` `screens/` |
| a 5.4 capture differs from the 5.0.8 decompile | version drift is possible: note both versions before calling it a calictl bug |
| a frame "matches" the wrong action | the time offset — re-align on a mark |
