---
name: esp-satellite-ops
description: Use when operating or checking the ESP32 satellite on the van after it is built and shipped — the live check after an OTA, power-cycling or a battery test from buspi, its USB console, setting up or checking its BLE sensors (Victron, Govee), reading the 72 h soak report before promoting esp-stable, or getting it back onto WiFi when it is stuck on its setup hotspot. Release mechanics (PR, merge, OTA install, channels) live in ship-a-change.
---

# Operate and verify the ESP satellite

`ship-a-change` gets a build onto the ESP. This skill is what comes next: prove the feature works
on the real satellite and the real camper unit, and keep the satellite healthy. Everything runs
from buspi, so nobody has to be at the van.

| Fact | Value |
|---|---|
| ESP on the van LAN | `http://192.168.8.183` (`calictl-esp.local`); over the tailnet `https://busnet.tail0e7f29.ts.net:8443/` |
| Its USB | buspi, `/dev/ttyACM0` (the only USB device on buspi) |
| Health JSON | `GET /api/state` → `device.fw`, `ota`, `link`, `wifi`, `battery`, `res`, `time`, `sd`; `ext` while sensors are on |

## 1. Live check after every OTA (owner rule: verified vs owed)

1. `device.fw` is the merge sha, `ota.state` `idle`, `link.up` true, `wifi.mode` `station`, uptime past 60 s (the build marked itself valid).
2. Exercise the changed feature on the **real** satellite and unit, not the stub: open the screen at 390 px (Playwright from a Mac on the van LAN or via busnet), tap only what the change touched, read the result back from `/api/state` or the unit.
3. Screenshots: the scratchpad or `.playwright-mcp/` (gitignored). A Device-status shot shows the unit's MAC: never post or commit it.
4. Report two lists to the owner: **verified live** and **still owed** (needs ignition, a sunny day, the owner's phone, …). Never call untested behaviour done.

## 2. Bench control from buspi

```sh
sudo uhubctl -l 1-1 -a off     # cut the ESP's USB power: it runs on its 500 mAh cell
sudo uhubctl -l 1-1 -a on      # restore power (charging)
python3 ~/open-california/tools/esplab/esp_cmd.py /dev/ttyACM0 2 "wifi status"   # one console command
python3 ~/open-california/tools/esplab/esp_cmd.py /dev/ttyACM0 --tail 120        # timestamped log, no reset
```

- `esp_cmd.py` opens the port without DTR/RTS. `cat`, `screen` or a default pyserial open **reset the chip**.
- With USB power off the console disappears. Watch the ESP over WiFi instead.
- Battery test: power off, log `device.battery` every minute over WiFi, power on. The fuel-gauge slope resets on every reboot, so measure on one boot. Baseline 2026-10-13: ~100 mA, ~5 h from full.
- Owner decisions: WiFi stays on when on battery; no light sleep; CPU frequency scaling stays off (no gain, #355). Do not propose these again.

## 3. External BLE sensors

- Setup: `tools/esplab/sensors_setup.sh [http://<esp>]` on buspi. It turns the experimental flag on, adds the Victrons with their keys from `/etc/vedirect-influx/*_key.txt` (POST body only, never argv or output) and the strongest Govee.
- Names: the alias set on the ESP is the only name. The sensors broadcast none. A default alias equals the type id and shows no prefix in the UI.
- Check: `ext` in `/api/state` (each entry has `type` and `age_s`); compare with buspi's own readers in Influx. Readings taken a moment apart differ slightly under load.

## 4. The soak gate before promoting esp-stable

- buspi runs `soak_report.py` daily at 08:00 (`calictl-esp-soak.timer`) → `~/esp-soak/<date>.txt`, last line `VERDICT PASS|FAIL`.
- A FAIL on `fw` alone after a day of OTAs is expected: the window needs 72 h on one build. Read the other criteria.
- Ad hoc: `soak_report.py --url http://192.168.8.183 --hours 8 --no-influx-write` (needs the env files: run it through `sudo systemd-run --pipe --wait -p EnvironmentFile=/etc/buspi/secrets.env -p EnvironmentFile=/etc/buspi/calictl.env --uid=pi …`).
- Promote only on a green 72 h report: `tools/esplab/promote_stable.sh <sha>` (see `ship-a-change`).

## 5. The ESP is stuck on its setup hotspot

Since #394 a failed network change rolls back by itself in about 10 s. If the ESP still ends up on
`calictl-esp-setup` (old build, a wrong password at first setup), restore buspi's own network over
USB: `ssh buspi 'sudo python3 ~/open-california/tools/esplab/esp_wifi_restore.py [PROFILE]'`. It
reads the NetworkManager profile as root and writes the password only to the serial port.
Fallback without buspi: a phone joins the hotspot and opens `http://192.168.4.1/device`.
