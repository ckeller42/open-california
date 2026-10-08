---
name: thinky-bench
description: Use when working on thinky (the Linux bench on the tailnet) — running the Android app lab, flashing or talking to the ESP32 CoreS3, running a fake unit on a USB Bluetooth dongle, joining the ESP over WiFi, or when thinky is unreachable or the lab is down after a reboot.
---

# thinky — the Linux bench

`ssh thinky` (Pop!_OS, x86_64 + KVM, on the tailnet). It hosts the **Android app lab** (emulator +
real app vs the fake unit) and the **ESP32 CoreS3 bench** (board + second radio + fake unit). The
owner uses the same machine for their own work, so the rules below are not optional.

## Never (owner's machine)

- **Never start or stop NetworkManager**; never touch `enp1s0` (uplink), tailscale, the owner's
  WiFi sticks `wlx801f02a6a65e` / `wlx54ef33f5119a`, or the owner's xrdp sessions.
- **Never reboot thinky.** If it is offline (`tailscale status` → "offline, last seen"), wait or ask.
- **Never `pkill -f <pattern>` in the same ssh command line as that pattern** — it kills the shell
  running it. `pgrep -f …` first, then `kill <pid>` in a separate command.
- Never print or commit a password/PSK (`~/.calictl-esplab/*.psk`, mode 600; pass by file/stdin),
  the VIN (`~/android-lab/vin`, mode 600) or its hash, a passkey, or a real MAC (only the fake
  identity `C0:FF:EE:CA:11:F0` may appear). Never screenshot the app's Account → Vehicle screen.
- Use `ssh -n` when the remote command backgrounds something, or the ssh hangs.

## Devices: identify by USB id, never by number

`hciN` and `wlx…` numbering changes across reboots. Check before use:

```bash
for h in /sys/class/bluetooth/hci*; do echo $h $(cat $h/device/../idVendor):$(cat $h/device/../idProduct); done
```

| USB id | What | Used for |
|---|---|---|
| `2357:0604` | TP-Link UB500 (RTL8761B) Bluetooth | the ESP's fake unit (`hci-socket:N`, interface down first, root) |
| `0e8d:7961` | MT7921AU combo stick: WiFi `wlx00c0cab9bea9` + its own BT | **our** WiFi to reach the ESP — the only WiFi interface we may touch |
| `303a:1001` | CoreS3 USB-Serial/JTAG, `/dev/ttyACM0` | flash + console |

The app lab's fake unit uses **netsim** (`android-netsim`), not a dongle — never point two fakes at one radio.

## WiFi to the ESP (NetworkManager is off — use our own supplicant)

The ESP is 2.4 GHz-only; it joins `Insel` (`calictl-esp.local`). Reach it through the MT7921 stick with
**our own** `wpa_supplicant` (control dir `/run/esp-wpa`, config `~/.calictl-esplab/esp-insel-wpa.conf`),
then `dhcpcd -G` (no default route) and a manual host route. Never `nmcli` while NM is stopped.

## ESP flash + console

```bash
gh run download <run-id> -n firmware-esp32s3 -D ~/calictl-esp     # the CI image + flasher_args.json
tools/esplab/flash.sh /dev/ttyACM0                                 # offsets from flasher_args.json, never hand-typed
tools/esplab/flash_ci.sh <branch|run-id>                           # both steps in one (same on buspi)
tools/esplab/esp_cmd.py /dev/ttyACM0 2 status "wifi status"        # console WITHOUT resetting the chip
```

A plain serial open (screen, pyserial, `cat`) **resets** the chip; `esp_cmd.py` opens it with
`stty -hupcl` + a raw non-blocking open. Console protocol: `docs/firmware.md`. Use `~/esp-venv/bin/python`
(esptool, Bumble) on thinky.

## App lab after a reboot

Runbook: `tools/applab/README.md` ("One-time setup (Linux)", GPU notes, ghost radios, DHKEY flake) and
the `app-lab` skill. thinky-specific:

- GPU: `swiftshader_indirect` **crashes the app**, and so does `-gpu guest` (it silently falls back
  to host lavapipe: qemu died on ~23 of 25 cold starts, 2026-10-06). Stable: a private `Xvfb :120` +
  **`-gpu swangle_indirect`**. `-gpu host` on the NVIDIA xrdp display `:10` works only while no other
  GPU session is busy. Stop your Xvfb afterwards.
- Run the emulator in **UTC** (the recording replay assumes it).
- Fake unit env: `FAKE_UNIT_HEARTBEAT_TIMEOUT_S=600 FAKE_UNIT_PAIRING_GRACE_S=600 FAKE_UNIT_RPA_S=99999`
  + a pinned passkey.
- Pairing fails ~50 % with a DHKEY mismatch → **retry without rebooting** anything. A *second* pair
  inside one `walk.py` run failed 5/5 — re-pair in a manual loop instead.
- App connects to nothing after a fake restart → ghost radio in netsimd (it outlives the emulator):
  stop the fake with SIGTERM, kill qemu, wait, kill netsimd, start again.
- Run the fake from the **branch under test** (sync the checkout first) or the recording tests the wrong mock.
- Give up after ~3 attempts per failure mode and report what failed — don't thrash.

## Common mistakes

| Symptom | Fix |
|---|---|
| `ssh thinky` times out | it is offline/asleep — check `tailscale status`; never wake it by rebooting anything |
| fake unit "advertising" but the ESP never connects | wrong `hciN` after a reboot — re-check the USB ids |
| ESP unreachable at `calictl-esp.local` | our supplicant on the MT7921 is not running (NM won't start it) |
| console shows a boot log on every command | you opened the port the resetting way — use `esp_cmd.py` |
