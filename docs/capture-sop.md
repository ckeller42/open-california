# Capture SOP (manual — cannot be automated)

## One-time Mac/iPhone setup
1. Install "Additional Tools for Xcode" → PacketLogger.
2. Install Apple's **Bluetooth for iOS** logging profile on the iPhone
   (Settings ▸ General ▸ VPN & Device Management after installing).
3. Install **nRF Connect** on the iPhone.

## Confirm transport (once)
- In nRF Connect, put the Camper Unit in pairing mode and scan.
- If it appears with GATT services → BLE. If not → likely classic BT.

## Per-action capture loop
For each action, capture **at least 2 repeats** (needed by `california check`):
1. Mac: PacketLogger ▸ File ▸ New iOS Trace ▸ Record.
2. iPhone: in the California app perform exactly ONE action (e.g. light on).
3. Stop recording; export as **BTSnoop** (or keep `.pklg`).
4. Save as `YYYY-MM-DD_<action>_NNN.pklg`, e.g. `2026-07-05_light-on_001.pklg`.

Minimum set: `light-on`, `light-off` (x2 each), `fridge-on`, `fridge-off`,
`temp-4c`, `temp-6c`.

## Then (automated)
```
california classify 2026-07-05_light-on_001.pklg
california check "2026-07-05_light-*.pklg"      # crypto/nonce guard
california diff  "2026-07-05_*.pklg" --out protocol.yaml
# review protocol.yaml, set confirmed: true on verified rows
california codegen --map protocol.yaml --address <camper-unit-mac> --out replay.py
```
