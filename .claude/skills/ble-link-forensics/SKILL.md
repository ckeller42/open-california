---
name: ble-link-forensics
description: Use when a BLE link to the camper unit drops, reconnects in a loop, or a central (ESP32, buspi, phone app) is "kicked" while another holds on; when a log shows HCI 0x13 / 0x16 / 0x08 / 0x3e or NimBLE 531; or before blaming the drop on "unit policy", sleep or heartbeat pace.
---

# BLE link forensics

## Overview

A link drop has a cause on the wire and a time signature. **Measure both sides with timestamps, then
change one variable at a time.** Theories without a timing measurement cost days; the ESP parked "kick"
(#264/#279) fell in about an hour once the drop times were measured and buspi's held link was captured
with btmon. Facts: `docs/firmware.md` "The unit's own ATT requests (GATT server on, #264)" and the
2026-10-09/10 rows in `docs/business-logic/evidence-ledger.md`.

## Method

1. **Timestamp every side.** Never reason from a log that has no own clock.
   - ESP console (no timestamps of its own): `python3 tools/esplab/esp_tail.py /dev/ttyACM0 120` on
     the host the board is plugged into (no reset; `-hupcl`). Commands: `tools/esplab/esp_cmd.py`.
   - buspi: `CALICTL_BLE_TRACE=~/ble.jsonl` (connect/read/notify/write/disconnect, JSONL) +
     `journalctl -u calictl -o short-precise`.
   - Phone app: Android HCI snoop (REQUIRED SUB-SKILL: phone-app-lab), then
     `python3 -m tools.applab.phone.snoop_links btsnoop_hci.log` (LE connects/disconnects + reason).
2. **Compute the signature.** Drop time minus connect time, over several cycles. A fixed ~30 s
   (here 30.0 s / 31.3 s after `connect_bonded`, ~23 s after read-all) = a **30 s protocol
   transaction timeout** (ATT or SMP request left unanswered), not a policy, not sleep.
3. **Compare holders: who is NOT dropped?** App vs buspi vs ESP on the same van, same state. Change
   **one** variable per run, on the van:
   - buspi heartbeat pace / off via a RUNTIME drop-in (gone on reboot):

     ```sh
     D=/run/systemd/system/calictl.service.d; sudo mkdir -p $D
     printf '[Service]\nEnvironment=CALICTL_HEARTBEAT_PERIOD_S=0.5\n' | sudo tee $D/exp.conf
     sudo systemctl daemon-reload && sudo systemctl restart calictl
     # revert: sudo rm $D/exp.conf && sudo systemctl daemon-reload && sudo systemctl restart calictl
     ```

     `=600` = heartbeat effectively off. Measured: link NOT dropped in >2 min — the heartbeat arms
     writes, it does not keep links.
   - also: counter start value, reads on/off, other centrals' connects (correlate timestamps).
   - Hold buspi's persistent session: `POST /api/session {"action":"connect"}` on `:8088`, then poll
     `/api/state` every 2 s (a viewer keeps it alive).
4. **See what the PEER sends you.** `sudo btmon -w /tmp/held.btsnoop` on buspi during a held session,
   then `btmon -r /tmp/held.btsnoop`: look at `> ACL` packets carrying ATT **requests** or SMP from
   the unit. Found: the unit sends **ATT Exchange MTU Request (Client RX MTU 247)** on every connect.
   Then check each central answers it (BlueZ/Android yes; esp-nimble without a GATT server drops it
   silently in `ble_att.c` `ble_att_rx_handle_unknown_request`).
5. **Read the stack source at the version CI builds**, not docs or memory: `gh api` the IDF tag and
   its esp-nimble submodule sha, read Kconfig + C there (IDF v6.1: `BT_NIMBLE_GATT_SERVER depends on
   BT_NIMBLE_ROLE_PERIPHERAL`). Diff the **device** sdkconfig against the **host-test** syscfg (host had
   `BLE_ROLE_PERIPHERAL 1` — why host tests never showed the kick).
6. **Reproduce in simulation, guard the fix.** Fake unit `mtu_request` knob in
   `tools/fake_unit_peripheral.py` (sends the request, hangs up unanswered after 30 s); config guard
   `tests/firmware/test_sdkconfig_gatt_server.py`. Then verify on the van and add a ledger row.

## Quick reference: HCI disconnect reasons

| Code | Meaning | Seen here as |
|---|---|---|
| `0x13` | remote user terminated | the peer's HOST decided: unit after its ATT timeout; phone when the app goes background |
| `0x16` | local host terminated | our own side hung up |
| `0x08` | supervision timeout | radio: range, interference, peer gone silent |
| `0x3e` | failed to establish | busy radio / another scanner during connect or pairing |
| NimBLE `531` | `0x200 + 0x13` | NimBLE host-encodes HCI reasons: 531 = `0x213` = `0x13` |

## Safety

- Van experiments: read-only or harmless actions only — no actuation unless the owner asks.
- Revert every runtime drop-in; confirm `systemctl show calictl -p Environment` afterwards.
- serve stays the single BLE owner on buspi; btmon is passive, a second connection is not.
- thinky: never start NetworkManager. Never `pkill -f PATTERN` with that pattern in the same ssh line
  (it kills its own shell).

## Common mistakes

| Mistake | Fix |
|---|---|
| "Unit policy" / "state-dependent tolerance" theory with no timing measurement | Measure drop-minus-connect over 3+ cycles first |
| Trusting a doc claim never measured ("dropped ~15 s without heartbeat" — false) | Re-measure; heartbeat off held >2 min |
| Assuming host-test build = device build | Diff device sdkconfig vs host syscfg for every flag on the path |
| Reading the ESP log without timestamps | `esp_tail.py`; correlate with `ble.jsonl` and journal |
| Changing two variables at once | One knob per run; revert before the next |
| Looking only at what you send | btmon the peer's `> ACL` requests; unanswered request = 30 s timeout |
| Guessing Kconfig semantics | Read source at the exact tag/submodule sha CI builds |
