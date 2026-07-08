# open-california: VW California T7 Camper Unit BLE Analysis & Replay Toolkit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python toolkit that ingests iPhone/Mac Bluetooth HCI captures of the VW California app talking to the T7 Camper Unit, isolates which GATT write controls each function (lights, fridge, setpoints), and generates a `bleak` replay script for a Raspberry Pi.

**Architecture:** A Mac-side analysis pipeline plus documented manual capture SOPs. The human captures traces with PacketLogger (physical, cannot be automated); the toolkit does everything after: `.pklg`/`.btsnoop` → normalized ATT event list (via `tshark`) → differential diff across labeled captures → living protocol map (YAML) → generated `bleak` replay script. The parser is split from the `tshark` invocation so the core logic is fully unit-testable with synthetic capture fixtures — **no vehicle required to develop or test the toolkit.**

**Tech Stack:** Python 3.11+, `bleak` (BLE replay, BlueZ backend on Pi), `tshark`/Wireshark (capture decoding), `pytest`, `PyYAML`. PacketLogger + "Additional Tools for Xcode" + Bluetooth iOS logging profile on the Mac/iPhone side (manual).

## Context

The user owns a VW California T7 Ocean and wants to control its lights and fridge from a Raspberry Pi. VW documents only the official California app + Camper Unit Bluetooth pairing — no open API. Controlling these functions from a Pi therefore requires observing the app↔Camper-Unit BLE traffic on the user's own vehicle (interoperability/personal use) and reproducing the non-safety commands.

**Why iPhone + Mac works even with encryption:** PacketLogger records at the **HCI layer**, above BLE link-layer encryption. The iPhone is a party to the connection, so its host stack sees ATT writes **decrypted** — an external sniffer (e.g. Nordic dongle) would only see ciphertext on a bonded link. This is the key enabler.

**Two make-or-break risks this plan surfaces early, not late:**
1. **App-layer crypto / nonces.** If commands carry counters, tokens, or challenge-response *above* GATT, raw-byte replay fails. Milestone: capture the *same* action twice — if payloads differ, an app-layer crypto layer exists and byte replay is out. This is checked in Task 6 before any codegen.
2. **Transport unknown until measured.** The Camper Unit may use BLE/GATT or classic Bluetooth (RFCOMM/SPP). Task 3's classifier reports which; the whole GATT approach is conditional on BLE.

**Scope guardrail (enforced in code, Task 8):** only `light_*`, `fridge_*`, and setpoint actions are eligible for replay codegen. Safety-relevant functions (heater, pop-top roof) are analyzed/documented but **refused** by the codegen step. Purpose is interoperability of the owner's own vehicle with Linux/open platforms; note the warranty implication in the README and DISCLAIMER.md.

## Global Constraints

- Python ≥ 3.11 (uses `tomllib`, `X | None` types, `dataclasses`).
- `bleak` ≥ 0.21 (stable BlueZ + CoreBluetooth backends).
- All capture-decoding logic MUST accept pre-decoded `tshark` JSON as input, kept separate from the `subprocess` call to `tshark`, so unit tests run without Wireshark installed and without a vehicle.
- No network calls in the toolkit.
- Capture filename convention (the label carrier): `<YYYY-MM-DD>_<action>_<NNN>.pklg`, e.g. `2026-07-05_light-on_001.pklg`. `action` is a slug matching `^[a-z0-9-]+$`.
- Codegen MUST refuse any action slug not in the allowlist `{light-*, fridge-*, temp-*, setpoint-*}` and MUST emit a top-of-file safety banner.

---

## File Structure

```
open-california/
  pyproject.toml                     # package metadata, deps, console entry points
  README.md                          # overview, legal/warranty note, quickstart
  docs/
    capture-sop.md                   # manual PacketLogger capture procedure (human runbook)
    protocol.md                      # human-readable rendering of the protocol map
  src/california/
    __init__.py
    model.py                         # AttEvent, Capture dataclasses + filename label parsing
    parse.py                         # tshark-JSON (dict) -> list[AttEvent]   (pure, testable)
    ingest.py                        # run tshark on a .pklg/.btsnoop -> list[AttEvent]  (subprocess)
    classify.py                      # transport classifier: BLE/GATT vs classic
    diff.py                          # differential engine: labeled captures -> action->write candidates
    protocol.py                      # load/save/merge protocol map (YAML)
    codegen.py                       # confirmed map -> bleak replay script (+ allowlist guard)
    cli.py                           # argparse entry points wiring the above
  tests/
    fixtures/
      tshark_light_on.json           # synthetic tshark -T json output, ATT write present
      tshark_light_off.json          # baseline, no write
      tshark_classic_rfcomm.json     # synthetic classic-BT capture (no btatt)
    test_model.py
    test_parse.py
    test_classify.py
    test_diff.py
    test_protocol.py
    test_codegen.py
    test_cli.py
```

Each `src/california/*.py` module has one responsibility and is small enough to hold in context. `parse.py` (pure) is deliberately split from `ingest.py` (I/O) — the split is what makes the pipeline testable without a vehicle.

---

## Task 1: Project scaffold + package metadata

**Files:**
- Create: `pyproject.toml`
- Create: `src/california/__init__.py`
- Create: `README.md`
- Test: `tests/test_cli.py` (import smoke test only, this task)

**Interfaces:**
- Produces: installable package `california`, version `0.1.0`; console script `california` → `california.cli:main`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
def test_package_imports():
    import california
    assert california.__version__ == "0.1.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py::test_package_imports -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'california'`

- [ ] **Step 3: Write minimal implementation**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "open-california"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["bleak>=0.21", "PyYAML>=6"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
california = "california.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
```

```python
# src/california/__init__.py
__version__ = "0.1.0"
```

```markdown
<!-- README.md -->
# open-california

Analyze VW California T7 Camper Unit BLE traffic (own vehicle, interoperability use)
and generate a Raspberry Pi replay script for lights and fridge.

> **Purpose / risk:** For use on your own vehicle only, to enable Linux/open-platform
> interoperability that the vendor app does not provide. Use at your own risk; actuation
> may affect your warranty or safety. Safety functions (heater, roof) are documented but
> excluded from replay codegen. (Not a lawyer; no legal claims — see DISCLAIMER.md.)

## Quickstart
See `docs/capture-sop.md` for the manual capture procedure, then:
`california ingest`, `california diff`, `california codegen`.
```

Also create `src/california/cli.py` with a stub so the entry point resolves:

```python
# src/california/cli.py
def main() -> int:
    print("california: see --help")
    return 0
```

- [ ] **Step 4: Install editable + run test**

Run: `python -m pip install -e ".[dev]" && python -m pytest tests/test_cli.py::test_package_imports -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git init && git add -A && git commit -m "feat: scaffold open-california package"
```

---

## Task 2: Data model + capture-filename label parsing

**Files:**
- Create: `src/california/model.py`
- Test: `tests/test_model.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) AttEvent(frame: int, t: float, op: str, handle: int, uuid: str | None, value: bytes, direction: str)` where `op ∈ {"write-req","write-cmd","read-resp","notify","other"}`, `direction ∈ {"sent","rcvd"}`.
  - `@dataclass(frozen=True) Capture(action: str, index: int, date: str, events: list[AttEvent])`.
  - `parse_label(filename: str) -> tuple[str, str, int]` returning `(date, action, index)`; raises `ValueError` on malformed names.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_model.py
import pytest
from california.model import AttEvent, parse_label

def test_parse_label_ok():
    assert parse_label("2026-07-05_light-on_001.pklg") == ("2026-07-05", "light-on", 1)

def test_parse_label_rejects_bad_action():
    with pytest.raises(ValueError):
        parse_label("2026-07-05_Light_On_001.pklg")  # uppercase/underscore in action

def test_att_event_value_is_bytes():
    e = AttEvent(frame=1, t=0.0, op="write-cmd", handle=0x2a, uuid="fff1", value=b"\x01", direction="sent")
    assert e.value == b"\x01" and e.handle == 42
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'california.model'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/california/model.py
from __future__ import annotations
import re
from dataclasses import dataclass

_LABEL = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<action>[a-z0-9-]+)_(?P<index>\d{3})\.(pklg|btsnoop|pcap|pcapng)$")

@dataclass(frozen=True)
class AttEvent:
    frame: int
    t: float
    op: str          # write-req | write-cmd | read-resp | notify | other
    handle: int
    uuid: str | None
    value: bytes
    direction: str   # sent | rcvd

@dataclass(frozen=True)
class Capture:
    action: str
    index: int
    date: str
    events: list[AttEvent]

def parse_label(filename: str) -> tuple[str, str, int]:
    m = _LABEL.match(filename)
    if not m:
        raise ValueError(f"bad capture filename: {filename!r}; expect YYYY-MM-DD_<action>_NNN.<ext>")
    return m["date"], m["action"], int(m["index"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_model.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: AttEvent/Capture model + filename label parsing"
```

---

## Task 3: ATT parser from tshark JSON (pure) + transport classifier

**Files:**
- Create: `src/california/parse.py`
- Create: `src/california/classify.py`
- Create: `tests/fixtures/tshark_light_on.json`
- Create: `tests/fixtures/tshark_classic_rfcomm.json`
- Test: `tests/test_parse.py`, `tests/test_classify.py`

**Interfaces:**
- Consumes: `AttEvent` from Task 2.
- Produces:
  - `parse_tshark(packets: list[dict]) -> list[AttEvent]` — maps `tshark -T json` packet dicts to `AttEvent`s, keeping only `btatt`-bearing frames.
  - `classify_transport(packets: list[dict]) -> str` returning `"ble-gatt"`, `"classic"`, or `"unknown"`.

**Note on fixtures:** `tshark -T json` emits a list of `{"_source": {"layers": {...}}}`. We model the minimal shape our parser reads. A real capture is produced later by Task 4; these synthetic fixtures let the pure parser be tested with no Wireshark and no vehicle.

- [ ] **Step 1: Write the fixtures**

```json
// tests/fixtures/tshark_light_on.json
[
  {"_source": {"layers": {
    "frame": {"frame.number": "12", "frame.time_relative": "1.500"},
    "btatt": {
      "btatt.opcode": "0x52",
      "btatt.handle": "0x002a",
      "btatt.uuid128": "0000fff1-0000-1000-8000-00805f9b34fb",
      "btatt.value": "01:ff"
    }
  }}}
]
```

```json
// tests/fixtures/tshark_classic_rfcomm.json
[
  {"_source": {"layers": {
    "frame": {"frame.number": "5", "frame.time_relative": "0.200"},
    "btrfcomm": {"btrfcomm.channel": "3"}
  }}}
]
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_parse.py
import json, pathlib
from california.parse import parse_tshark

FX = pathlib.Path(__file__).parent / "fixtures"

def test_parse_extracts_write_cmd():
    packets = json.loads((FX / "tshark_light_on.json").read_text())
    events = parse_tshark(packets)
    assert len(events) == 1
    e = events[0]
    assert e.op == "write-cmd"          # 0x52 = Write Command
    assert e.handle == 0x2a
    assert e.uuid == "0000fff1-0000-1000-8000-00805f9b34fb"
    assert e.value == bytes.fromhex("01ff")
    assert e.frame == 12
```

```python
# tests/test_classify.py
import json, pathlib
from california.classify import classify_transport

FX = pathlib.Path(__file__).parent / "fixtures"

def test_classify_ble():
    packets = json.loads((FX / "tshark_light_on.json").read_text())
    assert classify_transport(packets) == "ble-gatt"

def test_classify_classic():
    packets = json.loads((FX / "tshark_classic_rfcomm.json").read_text())
    assert classify_transport(packets) == "classic"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_parse.py tests/test_classify.py -v`
Expected: FAIL with `ModuleNotFoundError` for `california.parse` / `california.classify`

- [ ] **Step 4: Write minimal implementation**

```python
# src/california/parse.py
from __future__ import annotations
from california.model import AttEvent

# ATT opcode -> (name, direction). Only the ones we act on; else "other".
_OPCODES = {
    0x12: ("write-req", "sent"),
    0x52: ("write-cmd", "sent"),
    0x0b: ("read-resp", "rcvd"),
    0x1b: ("notify", "rcvd"),
    0x1d: ("notify", "rcvd"),  # handle value indication
}

def _hexcolon_to_bytes(s: str) -> bytes:
    if not s:
        return b""
    return bytes.fromhex(s.replace(":", ""))

def parse_tshark(packets: list[dict]) -> list[AttEvent]:
    out: list[AttEvent] = []
    for pkt in packets:
        layers = pkt.get("_source", {}).get("layers", {})
        att = layers.get("btatt")
        if not att:
            continue
        frame = layers.get("frame", {})
        opcode = int(att.get("btatt.opcode", "0x0"), 16)
        name, direction = _OPCODES.get(opcode, ("other", "sent"))
        uuid = att.get("btatt.uuid128") or att.get("btatt.uuid16")
        out.append(AttEvent(
            frame=int(frame.get("frame.number", "0")),
            t=float(frame.get("frame.time_relative", "0") or 0.0),
            op=name,
            handle=int(att.get("btatt.handle", "0x0"), 16),
            uuid=uuid,
            value=_hexcolon_to_bytes(att.get("btatt.value", "")),
            direction=direction,
        ))
    return out
```

```python
# src/california/classify.py
from __future__ import annotations

def classify_transport(packets: list[dict]) -> str:
    saw_att = saw_classic = False
    for pkt in packets:
        layers = pkt.get("_source", {}).get("layers", {})
        if "btatt" in layers or "btgatt" in layers:
            saw_att = True
        if "btrfcomm" in layers or "btspp" in layers or "btl2cap" in layers and "btatt" not in layers:
            saw_classic = True
    if saw_att:
        return "ble-gatt"
    if saw_classic:
        return "classic"
    return "unknown"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_parse.py tests/test_classify.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: tshark-JSON ATT parser + transport classifier"
```

---

## Task 4: tshark ingest wrapper (subprocess I/O)

**Files:**
- Create: `src/california/ingest.py`
- Test: `tests/test_ingest.py` (mocks subprocess; no real tshark needed in CI)

**Interfaces:**
- Consumes: `parse_tshark` (Task 3), `Capture`/`parse_label` (Task 2).
- Produces:
  - `run_tshark(path: str, tshark_bin: str = "tshark") -> list[dict]` — runs `tshark -r <path> -T json -Y "btatt || btrfcomm || btspp"`, returns parsed JSON list. Raises `FileNotFoundError` if `tshark` missing, `RuntimeError` on non-zero exit.
  - `load_capture(path: str) -> Capture` — labels via filename, decodes via `run_tshark`+`parse_tshark`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ingest.py
import json
from unittest import mock
from california.ingest import load_capture

def test_load_capture_uses_filename_label(tmp_path):
    f = tmp_path / "2026-07-05_light-on_001.pklg"
    f.write_bytes(b"\x00")  # content irrelevant; tshark is mocked
    fake_json = json.dumps([{"_source": {"layers": {
        "frame": {"frame.number": "1", "frame.time_relative": "0.1"},
        "btatt": {"btatt.opcode": "0x52", "btatt.handle": "0x002a",
                  "btatt.uuid16": "fff1", "btatt.value": "01"}}}}])
    completed = mock.Mock(returncode=0, stdout=fake_json, stderr="")
    with mock.patch("california.ingest.subprocess.run", return_value=completed):
        cap = load_capture(str(f))
    assert cap.action == "light-on"
    assert cap.index == 1
    assert len(cap.events) == 1
    assert cap.events[0].op == "write-cmd"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'california.ingest'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/california/ingest.py
from __future__ import annotations
import json, os, shutil, subprocess
from california.model import Capture, parse_label
from california.parse import parse_tshark

_DISPLAY_FILTER = "btatt || btrfcomm || btspp"

def run_tshark(path: str, tshark_bin: str = "tshark") -> list[dict]:
    if shutil.which(tshark_bin) is None:
        raise FileNotFoundError(f"{tshark_bin} not found; install Wireshark CLI")
    proc = subprocess.run(
        [tshark_bin, "-r", path, "-T", "json", "-Y", _DISPLAY_FILTER],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"tshark failed ({proc.returncode}): {proc.stderr.strip()}")
    return json.loads(proc.stdout or "[]")

def load_capture(path: str) -> Capture:
    date, action, index = parse_label(os.path.basename(path))
    packets = run_tshark(path)
    return Capture(action=action, index=index, date=date, events=parse_tshark(packets))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ingest.py -v`
Expected: PASS (`shutil.which` is not hit because `subprocess.run` is mocked — but note the mock patches `subprocess.run` only; `run_tshark`'s `shutil.which` guard would run. Adjust: the test must also mock `shutil.which`.)

> Correction for Step 1 — the test must also patch `which`. Use this test body instead:

```python
    with mock.patch("california.ingest.shutil.which", return_value="/usr/bin/tshark"), \
         mock.patch("california.ingest.subprocess.run", return_value=completed):
        cap = load_capture(str(f))
```

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: tshark ingest wrapper (load_capture)"
```

---

## Task 5: Differential diff engine

**Files:**
- Create: `src/california/diff.py`
- Test: `tests/test_diff.py`

**Interfaces:**
- Consumes: `Capture`, `AttEvent` (Task 2).
- Produces:
  - `@dataclass(frozen=True) WriteCandidate(handle: int, uuid: str | None, value: bytes, confidence: float)`.
  - `writes_of(cap: Capture) -> set[tuple[int, bytes]]` — the `(handle, value)` write set of a capture (write-req + write-cmd only).
  - `diff_pair(action_cap: Capture, baseline_cap: Capture) -> list[WriteCandidate]` — writes present in `action_cap` but not `baseline_cap`.
  - `isolate(captures: list[Capture]) -> dict[str, list[WriteCandidate]]` — for each action, diff every `<action>` capture against the paired baseline. Pairing rule: an action `X-on` is baselined against `X-off` if present, else against the union of all *other* actions' writes. Confidence = fraction of that action's capture-repeats in which the candidate write appears.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_diff.py
from california.model import Capture, AttEvent
from california.diff import writes_of, diff_pair, isolate

def _cap(action, idx, writes):
    events = [AttEvent(frame=i, t=float(i), op="write-cmd", handle=h,
                       uuid=None, value=v, direction="sent")
              for i, (h, v) in enumerate(writes)]
    return Capture(action=action, index=idx, date="2026-07-05", events=events)

def test_writes_of_filters_writes():
    cap = _cap("light-on", 1, [(0x2a, b"\x01")])
    assert writes_of(cap) == {(0x2a, b"\x01")}

def test_diff_pair_isolates_unique_write():
    on = _cap("light-on", 1, [(0x2a, b"\x01"), (0x10, b"\x00")])   # 0x10 is noise/keepalive
    off = _cap("light-off", 1, [(0x2a, b"\x00"), (0x10, b"\x00")]) # shared noise cancels
    cands = diff_pair(on, off)
    assert [(c.handle, c.value) for c in cands] == [(0x2a, b"\x01")]

def test_isolate_confidence_from_repeats():
    caps = [
        _cap("light-on", 1, [(0x2a, b"\x01")]),
        _cap("light-on", 2, [(0x2a, b"\x01")]),
        _cap("light-off", 1, [(0x2a, b"\x00")]),
    ]
    result = isolate(caps)
    on = result["light-on"]
    assert on[0].handle == 0x2a and on[0].value == b"\x01"
    assert on[0].confidence == 1.0   # appears in both light-on repeats
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_diff.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'california.diff'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/california/diff.py
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from california.model import Capture

_WRITE_OPS = {"write-req", "write-cmd"}

@dataclass(frozen=True)
class WriteCandidate:
    handle: int
    uuid: str | None
    value: bytes
    confidence: float

def writes_of(cap: Capture) -> set[tuple[int, bytes]]:
    return {(e.handle, e.value) for e in cap.events if e.op in _WRITE_OPS}

def _uuid_for(cap: Capture, handle: int) -> str | None:
    for e in cap.events:
        if e.handle == handle and e.uuid:
            return e.uuid
    return None

def diff_pair(action_cap: Capture, baseline_cap: Capture) -> list[WriteCandidate]:
    unique = writes_of(action_cap) - writes_of(baseline_cap)
    return [WriteCandidate(h, _uuid_for(action_cap, h), v, 1.0)
            for (h, v) in sorted(unique)]

def isolate(captures: list[Capture]) -> dict[str, list[WriteCandidate]]:
    by_action: dict[str, list[Capture]] = defaultdict(list)
    for cap in captures:
        by_action[cap.action].append(cap)

    def baseline_for(action: str) -> set[tuple[int, bytes]]:
        # prefer the paired inverse (on<->off); else union of all other actions
        inverse = action.replace("-on", "-off") if action.endswith("-on") else action.replace("-off", "-on")
        if inverse in by_action and inverse != action:
            base: set[tuple[int, bytes]] = set()
            for c in by_action[inverse]:
                base |= writes_of(c)
            return base
        base = set()
        for other, caps in by_action.items():
            if other == action:
                continue
            for c in caps:
                base |= writes_of(c)
        return base

    result: dict[str, list[WriteCandidate]] = {}
    for action, caps in by_action.items():
        base = baseline_for(action)
        # count in how many repeats each unique write appears
        counts: dict[tuple[int, bytes], int] = defaultdict(int)
        for c in caps:
            for w in writes_of(c) - base:
                counts[w] += 1
        n = len(caps)
        cands = [WriteCandidate(h, _uuid_for(caps[0], h), v, cnt / n)
                 for (h, v), cnt in counts.items()]
        cands.sort(key=lambda c: (-c.confidence, c.handle))
        result[action] = cands
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_diff.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: differential diff engine to isolate per-action writes"
```

---

## Task 6: Protocol map (YAML) + replay-safety check (nonce/crypto detector)

**Files:**
- Create: `src/california/protocol.py`
- Test: `tests/test_protocol.py`

**Interfaces:**
- Consumes: `WriteCandidate` (Task 5), `Capture` (Task 2).
- Produces:
  - `load_map(path: str) -> dict` and `save_map(path: str, data: dict) -> None` (YAML round-trip; schema: `{"actions": {action: {"handle": int, "uuid": str|None, "value": hexstr, "confidence": float, "confirmed": bool}}}`).
  - `merge_candidates(existing: dict, isolated: dict[str, list[WriteCandidate]]) -> dict` — folds new diff results into the map, keeping `confirmed: true` entries untouched.
  - `replay_safe(repeats: list[Capture]) -> bool` — **the crypto/nonce guard.** Given ≥2 captures of the *same* action, returns `False` if the isolated write payloads differ across repeats (⇒ counter/nonce/challenge-response ⇒ raw replay won't work). Returns `True` only if the same `(handle, value)` write appears in every repeat.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_protocol.py
from california.model import Capture, AttEvent
from california.protocol import save_map, load_map, replay_safe

def _cap(action, writes):
    ev = [AttEvent(frame=i, t=0.0, op="write-cmd", handle=h, uuid=None, value=v, direction="sent")
          for i, (h, v) in enumerate(writes)]
    return Capture(action=action, index=0, date="2026-07-05", events=ev)

def test_map_roundtrip(tmp_path):
    p = tmp_path / "protocol.yaml"
    data = {"actions": {"light-on": {"handle": 42, "uuid": "fff1",
            "value": "01ff", "confidence": 1.0, "confirmed": True}}}
    save_map(str(p), data)
    assert load_map(str(p)) == data

def test_replay_safe_true_when_stable():
    reps = [_cap("light-on", [(0x2a, b"\x01")]), _cap("light-on", [(0x2a, b"\x01")])]
    assert replay_safe(reps) is True

def test_replay_safe_false_when_payload_varies():
    # same action, differing payloads => nonce/counter/crypto present
    reps = [_cap("light-on", [(0x2a, b"\x01\xAA")]), _cap("light-on", [(0x2a, b"\x01\xBB")])]
    assert replay_safe(reps) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_protocol.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'california.protocol'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/california/protocol.py
from __future__ import annotations
import yaml
from california.model import Capture
from california.diff import writes_of, WriteCandidate

def load_map(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {"actions": {}}

def save_map(path: str, data: dict) -> None:
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=True)

def merge_candidates(existing: dict, isolated: dict[str, list[WriteCandidate]]) -> dict:
    out = {"actions": dict(existing.get("actions", {}))}
    for action, cands in isolated.items():
        if out["actions"].get(action, {}).get("confirmed"):
            continue  # never overwrite a human-confirmed mapping
        if not cands:
            continue
        top = cands[0]
        out["actions"][action] = {
            "handle": top.handle,
            "uuid": top.uuid,
            "value": top.value.hex(),
            "confidence": top.confidence,
            "confirmed": False,
        }
    return out

def replay_safe(repeats: list[Capture]) -> bool:
    if len(repeats) < 2:
        return False  # cannot prove stability from a single capture
    common = writes_of(repeats[0])
    for cap in repeats[1:]:
        common &= writes_of(cap)
    return len(common) > 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_protocol.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: protocol map YAML + nonce/crypto replay-safety guard"
```

---

## Task 7: bleak replay codegen + safety allowlist

**Files:**
- Create: `src/california/codegen.py`
- Test: `tests/test_codegen.py`

**Interfaces:**
- Consumes: protocol map dict (Task 6).
- Produces:
  - `ALLOWED_PREFIXES = ("light-", "fridge-", "temp-", "setpoint-")`.
  - `generate(mapping: dict, address: str) -> str` — returns a runnable `bleak` Python script. Excludes any action whose slug does not start with an allowed prefix (safety functions). Raises `ValueError` if no allowed, confirmed action remains.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_codegen.py
import pytest
from california.codegen import generate

MAP = {"actions": {
    "light-on":  {"handle": 42, "uuid": "fff1", "value": "01", "confidence": 1.0, "confirmed": True},
    "roof-open": {"handle": 99, "uuid": "aaaa", "value": "ff", "confidence": 1.0, "confirmed": True},
}}

def test_generate_includes_allowed_action():
    src = generate(MAP, "AA:BB:CC:DD:EE:FF")
    assert "light-on" in src
    assert 'bytes.fromhex("01")' in src
    assert "AA:BB:CC:DD:EE:FF" in src

def test_generate_excludes_safety_action():
    src = generate(MAP, "AA:BB:CC:DD:EE:FF")
    assert "roof-open" not in src   # safety function must be filtered out

def test_generate_raises_when_nothing_allowed():
    with pytest.raises(ValueError):
        generate({"actions": {"roof-open": MAP["actions"]["roof-open"]}}, "AA:BB:CC:DD:EE:FF")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_codegen.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'california.codegen'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/california/codegen.py
from __future__ import annotations

ALLOWED_PREFIXES = ("light-", "fridge-", "temp-", "setpoint-")

_HEADER = '''"""AUTO-GENERATED by open-california. Own-vehicle interoperability use.
Safety functions (heater, roof) are intentionally excluded.
Run on a Raspberry Pi with BlueZ: `python replay.py light-on`."""
import asyncio, sys
from bleak import BleakClient

ADDRESS = "{address}"
COMMANDS = {{
{commands}
}}

async def main(action: str) -> None:
    char, payload = COMMANDS[action]
    async with BleakClient(ADDRESS) as client:
        await client.write_gatt_char(char, payload, response=False)
        print(f"sent {{action}} -> {{payload.hex()}}")

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        raise SystemExit(f"usage: python replay.py <{{'|'.join(COMMANDS)}}>")
    asyncio.run(main(sys.argv[1]))
'''

def generate(mapping: dict, address: str) -> str:
    lines = []
    for action, spec in sorted(mapping.get("actions", {}).items()):
        if not action.startswith(ALLOWED_PREFIXES):
            continue
        if not spec.get("confirmed"):
            continue
        uuid = spec.get("uuid")
        char = f'"{uuid}"' if uuid else spec["handle"]
        lines.append(f'    "{action}": ({char}, bytes.fromhex("{spec["value"]}")),')
    if not lines:
        raise ValueError("no confirmed, allowed actions to generate")
    return _HEADER.format(address=address, commands="\n".join(lines))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_codegen.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: bleak replay codegen with safety-function allowlist"
```

---

## Task 8: CLI wiring + capture SOP doc

**Files:**
- Modify: `src/california/cli.py`
- Create: `docs/capture-sop.md`
- Test: `tests/test_cli.py` (extend)

**Interfaces:**
- Consumes: everything above.
- Produces console subcommands:
  - `california classify <capture>` → prints transport.
  - `california diff <glob> [--out protocol.yaml]` → loads captures, isolates, merges into map, saves.
  - `california check <glob>` → runs `replay_safe` per action, prints PASS/FAIL (crypto guard).
  - `california codegen --map protocol.yaml --address <mac> --out replay.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py  (append)
from california.cli import build_parser

def test_parser_has_subcommands():
    p = build_parser()
    args = p.parse_args(["codegen", "--map", "m.yaml", "--address", "AA:BB", "--out", "r.py"])
    assert args.cmd == "codegen"
    assert args.address == "AA:BB"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py::test_parser_has_subcommands -v`
Expected: FAIL with `ImportError: cannot import name 'build_parser'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/california/cli.py
from __future__ import annotations
import argparse, glob as _glob
from california.ingest import load_capture
from california.classify import classify_transport
from california.ingest import run_tshark
from california.diff import isolate
from california.protocol import load_map, save_map, merge_candidates, replay_safe
from california.codegen import generate

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="california")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("classify"); c.add_argument("capture")
    d = sub.add_parser("diff"); d.add_argument("glob"); d.add_argument("--out", default="protocol.yaml")
    k = sub.add_parser("check"); k.add_argument("glob")
    g = sub.add_parser("codegen")
    g.add_argument("--map", required=True); g.add_argument("--address", required=True)
    g.add_argument("--out", default="replay.py")
    return p

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "classify":
        print(classify_transport(run_tshark(args.capture)))
    elif args.cmd == "diff":
        caps = [load_capture(f) for f in sorted(_glob.glob(args.glob))]
        existing = load_map(args.out) if _glob.glob(args.out) else {"actions": {}}
        merged = merge_candidates(existing, isolate(caps))
        save_map(args.out, merged)
        print(f"wrote {args.out} ({len(merged['actions'])} actions)")
    elif args.cmd == "check":
        caps = [load_capture(f) for f in sorted(_glob.glob(args.glob))]
        by_action: dict[str, list] = {}
        for c in caps:
            by_action.setdefault(c.action, []).append(c)
        for action, reps in by_action.items():
            print(f"{action}: {'PASS' if replay_safe(reps) else 'FAIL (nonce/crypto?)'}")
    elif args.cmd == "codegen":
        src = generate(load_map(args.map), args.address)
        with open(args.out, "w") as f:
            f.write(src)
        print(f"wrote {args.out}")
    return 0
```

Also write the manual runbook:

```markdown
<!-- docs/capture-sop.md -->
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -v`
Expected: PASS (all tests green)

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: CLI subcommands + manual capture SOP"
```

---

## Verification (end-to-end, no vehicle)

1. **Full test suite:** `python -m pytest -v` → all tasks' tests pass.
2. **Synthetic pipeline dry-run:** create two labeled fixture captures on disk (copy the `tshark_light_on.json` shape into `.pklg` via a tiny helper, or point `diff` at pre-made pcaps), run:
   - `california diff "tests/fixtures/*.pklg" --out /tmp/protocol.yaml` → produces a map with a `light-on` candidate.
   - hand-edit `confirmed: true`, then `california codegen --map /tmp/protocol.yaml --address AA:BB:CC:DD:EE:FF --out /tmp/replay.py` → produces a `bleak` script.
   - `python -c "import ast; ast.parse(open('/tmp/replay.py').read())"` → generated script is valid Python.
3. **Real vehicle (user, manual):** follow `docs/capture-sop.md`; first run `california classify` (settles BLE-vs-classic), then `california check` (settles the nonce/crypto risk) **before** trusting any codegen output.

## Self-Review notes

- **Spec coverage:** transport-unknown → Task 3 `classify` + Task 8 `classify` cmd; encryption-vs-HCI → addressed in Context (HCI is above link crypto); app-layer nonce → Task 6 `replay_safe` + Task 8 `check`; safety-scope → Task 7 allowlist; Pi replay → Task 7 codegen (bleak/BlueZ); "Claude can't capture physically" → `docs/capture-sop.md` manual runbook.
- **Known follow-ups (not blocking v0.1):** handle→UUID resolution across a capture is best-effort (`_uuid_for` takes the first match); GATT service discovery table export could be added later; classic-BT (RFCOMM) path is only classified, not decoded/replayed — out of scope until Task 3 reports `classic`.
