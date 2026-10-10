---
name: capture-and-diff
description: Use when running tools/capture_diff.py — diffing a captured or recorded app frame against calictl's control.build, adding a tools/scenarios/<fn>/<case>.yaml, or when a set/actuate frame ACKs but does not apply and you suspect the app sends a field calictl does not.
---

# capture_diff (the tool) — the workflow lives elsewhere

This skill is only the tool reference. Getting a capture: `phone-app-lab` (real app on the real
unit, HCI snoop from buspi) or `app-lab` (real app on the fake unit). Turning a capture into a fact
(tier, claim wording, docs, mock): **`real-unit-evidence`**. The old iOS PacketLogger path on the
bar Mac is retired.

```sh
python3 -m tools.capture_diff <pcap|btsnoop> <fn>/<case>          # vs tools/scenarios/<fn>/<case>.yaml
python3 -m tools.capture_diff frames.txt <fn>/<case> --frames      # one "<uuid|handle>: <hex>" per line
python3 -m tools.capture_diff tests/vectors/app/<x>.jsonl --recording   # replay an app-lab recording
```

- Validate on a known-good scenario first (`cooler/power-on` → identical, rc 0).
- `<-- LEAD` = a field the app sets that calictl leaves at 0/sentinel (rc 1): a finding, then
  live-verify on buspi (`buspi-deploy`) before calling it fixed.
- Scenario yaml: `{function, what, value, control_char, handle?, state?}` — `state` = the decoded
  state at capture time (full-packet carry-forward). The capture only validates that state.
- Handles: only `0x0022` = cooler `1101` is hardcoded; pin `handle:` or use `gatt.txt` (`phone-app-lab`).
- Never commit captures (`*.pcap*`, `*.pklg`, `*.btsnoop`, `*.cvr` are gitignored).
