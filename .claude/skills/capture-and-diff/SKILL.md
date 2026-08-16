---
name: capture-and-diff
description: Capture the real CaliforniaOnTour app's BLE control writes and diff them against calictl's, to crack unsolved actuation gates (lighting SET first). Use when a set/actuate frame ACKs but doesn't apply and you need to see what field the app sends that calictl doesn't.
---

# Capture the app, diff against calictl (the Phase-2 oracle)

The mock (`tools/mock_unit.py`) only encodes what we already decoded, so it can't reveal
new protocol truth. This does: passively capture the **real app driving the real van** (B1
— the proven path that found the 1003 heartbeat), then `tools/capture_diff.py` decodes the
app's control writes with calictl's own `control.decode_control` and diffs them field-by-field
against `control.build`. A field the app carries that calictl leaves at 0/sentinel is the
missing ingredient (flagged `<-- LEAD`).

**Capture is the one manual seam** — it needs the van + the phone + a human tapping. Everything
after the pcap is automated.

## The loop

1. **Sniff the app↔van BLE session.** Two options:
   - **iOS** (used for the 1003 discovery), on the bar Mac (Tailscale hostname `bar`, NOT
     `laptop-bar`; see [[laptop-bar-tailscale]]). All tools are **MacPorts** at
     `/opt/local/bin` (NOT Homebrew, and not on the default ssh PATH — use
     `export PATH=/opt/local/bin:$PATH` or absolute paths): `idevicebtlogger`, `tshark`
     (wireshark4 +no_gui), `idevice_id`. GUI PacketLogger also exists at
     `/Applications/Utilities/PacketLogger.app` (Xcode installed).
     Plug the phone in via USB, tap Trust, confirm `idevice_id -l` lists it, then
     `idevicebtlogger -f /tmp/cali.pklg`; open the app and perform
     the ONE labelled action (e.g. set the Kitchen zone to 50%), then stop. Convert to pcap if
     needed: `tshark -r /tmp/cali.pklg -w /tmp/cali.pcapng`.
   - **Android/Linux**: enable Bluetooth HCI snoop log (Developer Options) or `btmon -w
     /tmp/cali.btsnoop` on a rooted/adb host.
   Keep the capture SHORT and do exactly one action so the target write is easy to pick out.

2. **Run the diff** (needs `tshark` for pcap/pcapng/btsnoop; `pip install pyyaml` for scenarios):
   ```sh
   python3 -m tools.capture_diff /tmp/cali.pcapng lighting/kitchen-50
   ```
   Validate the pipeline on the **known-good** scenario first — it must diff to zero:
   ```sh
   python3 -m tools.capture_diff /tmp/cooler.pcapng cooler/power-on   # expect: identical
   ```

3. **If tshark can't parse the format**, extract ATT writes by hand into a normalized list and
   use `--frames` (one `<uuid|handle>: <hex>` per line):
   ```sh
   printf '1501: 00040000...\n' > /tmp/frames.txt
   python3 -m tools.capture_diff /tmp/frames.txt lighting/kitchen-50 --frames
   ```

## Scenarios

`tools/scenarios/<feature>/<action>.yaml` — `{function, what, value, control_char, handle?,
state?}`. `state` is the decoded state at capture time (the full-packet carry-forward
`control.build` needs). Add a scenario per action you capture. First targets:
`cooler/power-on` (validation), `lighting/kitchen-50` (the prize).

## Reading the result

- **identical** → calictl already reproduces the app's frame (rc 0).
- **LEADS** → the app sets fields calictl leaves at 0/sentinel (rc 1). For lighting the suspects
  are `LightValue` (a per-zone enable mask) and/or a `SET_PROFILE` (Mode=16) preamble frame — a
  capture will show which. Feed the finding into `control._lighting` + `re-gap-inventory.md §A1`,
  then **live-verify on buspi** ([[buspi-deploy]]) before declaring it fixed (the ProfileNumber
  theory looked airtight and still failed on-device — see the lighting note in that memory).

## Notes
- Never commit captures — `.gitignore` covers `*.pcap`/`*.pcapng`/`*.pklg`/`*.btsnoop`/`*.cvr`.
  They contain the van's BLE identity + payloads. Citations only.
- Handle↔UUID: only `0x0022`=cooler `1101` is hardcoded (`capture_diff.HANDLE_UUID`); a scenario
  can pin `handle:` directly, or the capture's own GATT discovery is authoritative.
