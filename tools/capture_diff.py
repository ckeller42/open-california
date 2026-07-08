"""Differential oracle: diff the REAL app's control writes against calictl's (Phase 2, B1).

The mock (`tools/mock_unit.py`) can only encode what we already decoded, so it cannot
reveal new protocol truth. This tool can: it takes a PASSIVE HCI capture of the real
CaliforniaOnTour app driving the real van (the proven B1 path that found the 1003
heartbeat), decodes the app's ATT writes with our OWN `control.decode_control`, and diffs
them field-by-field against what `control.build` emits for the same action.

The prize is `lighting`: capture the app doing a real brightness change and this surfaces
the exact field the app carries that calictl leaves at 0 (leading suspect `LightValue`, a
per-zone enable mask, or a SET_PROFILE preamble) — the concrete crack for the #1 open gap
(`docs/business-logic/re-gap-inventory.md` §A1). Validate the pipeline on the known-good
`cooler/power-on` scenario (must diff to zero) BEFORE trusting a lighting diff.

Capture is the one manual seam (van + phone + a human tapping); everything here is
automated. See `.claude/skills/capture-and-diff/SKILL.md` for the capture SOP.

Frontend: BLE reassembly is delegated to `tshark` (handles HCI/L2CAP fragmentation
correctly) — we do NOT re-implement it. Alternatively pass `--frames FILE`, a normalized
list a human extracts from any capture tool (`<uuid-or-handle>: <hex>` per line).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from calictl import protocol, control, overrides

# ATT write opcodes (method bits): 0x12 Write Request, 0x52 Write Command.
_ATT_WRITE_OPCODES = {0x12, 0x52}

# Known ATT handle -> short char UUID (extend as captures reveal more). The cooler
# control char anchor was confirmed live (issue #2). Handles are stable per bonded
# session but a capture's own GATT discovery is authoritative if it differs.
HANDLE_UUID = {0x0022: "1101"}

SENTINEL_2BIT = 3


@dataclass
class Scenario:
    """One (feature, action) capture target: the calictl args to reproduce it, plus
    which control char the app writes and the decoded state at capture time (for the
    full-packet carry-forward `control.build` needs)."""
    name: str
    function: str
    what: str
    value: object
    control_char: str                 # short UUID, e.g. "1501"
    capture_label: str = ""
    handle: int | None = None         # ATT handle of that char, if known
    state: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "Scenario":
        return cls(name=name, function=d["function"], what=d["what"], value=d["value"],
                   control_char=str(d["control_char"]), capture_label=d.get("capture_label", ""),
                   handle=d.get("handle"), state=d.get("state") or {})


def load_scenario(name: str, root: str | Path | None = None) -> Scenario:
    """Load `tools/scenarios/<name>.yaml` (needs PyYAML — this is tooling, deps allowed)."""
    import yaml   # lazy: keep the module importable in the bleak/yaml-less test env
    base = Path(root) if root else Path(__file__).resolve().parent / "scenarios"
    path = base / (name + ".yaml")
    return Scenario.from_dict(name, yaml.safe_load(path.read_text()))


# --- capture frontends ------------------------------------------------------

def parse_frames_file(path: str | Path) -> list[tuple[str, bytes]]:
    """Normalized fallback input: lines `<uuid-or-handle>: <hex>`. Keys are a short UUID
    (`1501`), a full UUID, or a handle (`0x0022`). `#` comments and blanks ignored."""
    out: list[tuple[str, bytes]] = []
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, hexstr = line.partition(":")
        out.append((key.strip(), bytes.fromhex(hexstr.strip().replace(" ", ""))))
    return out


def extract_att_writes(path: str | Path, tshark: str = "tshark") -> list[tuple[int, bytes]]:
    """Extract (handle, value) for every ATT Write via tshark (correct reassembly)."""
    cmd = [tshark, "-r", str(path),
           "-Y", "btatt.opcode.method==0x12 || btatt.opcode.method==0x52",
           "-T", "fields", "-e", "btatt.handle", "-e", "btatt.value"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise SystemExit("tshark not found — install Wireshark CLI, or use --frames. "
                         "See .claude/skills/capture-and-diff/SKILL.md")
    if res.returncode != 0:
        raise SystemExit("tshark failed: %s" % res.stderr.strip())
    return _parse_tshark_fields(res.stdout)


def _parse_tshark_fields(text: str) -> list[tuple[int, bytes]]:
    out: list[tuple[int, bytes]] = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        handle = int(parts[0], 16) if parts[0].lower().startswith("0x") else int(parts[0], 16)
        out.append((handle, bytes.fromhex(parts[1].replace(":", ""))))
    return out


def _short(uuid: str) -> str:
    """Short 4-hex form of a full/short char UUID."""
    u = uuid.lower()
    return u[4:8] if u.startswith("0000") and len(u) >= 8 else u


def select_app_frame(scenario: Scenario, writes: list[tuple], *, from_frames: bool) -> bytes:
    """Pick the app's committed frame for the scenario's control char: the LAST write to
    it. `writes` are (handle, bytes) from tshark or (key, bytes) from a frames file."""
    target_short = _short(scenario.control_char)
    chosen = None
    for key, value in writes:
        if from_frames:
            matches = _short(str(key)) == target_short or (
                str(key).lower().startswith("0x") and scenario.handle == int(str(key), 16))
        else:
            matches = scenario.handle is not None and key == scenario.handle
            if not matches and key in HANDLE_UUID:
                matches = HANDLE_UUID[key] == target_short
        if matches:
            chosen = value
    if chosen is None:
        raise SystemExit("no app write to control char %s (%s) found in the capture"
                         % (scenario.control_char, "handle %s" % scenario.handle
                            if scenario.handle else "unknown handle"))
    return chosen


# --- the diff engine (the tested core) --------------------------------------

@dataclass
class DiffRow:
    name: str
    app: object
    calictl: object
    match: bool


def diff(funcs: dict, scenario: Scenario, app_frame: bytes) -> tuple[list[DiffRow], list[str], bytes]:
    """Decode the app frame and calictl's frame for the same action; compare per field.

    Returns (rows, leads, calictl_frame). `leads` are the RE signal: fields where the app
    carries a meaningful value that calictl leaves at 0 or the leave-unchanged sentinel —
    i.e. exactly the missing ingredient to reproduce the app's effect."""
    func = funcs[scenario.function]
    calictl_frame = control.build(funcs, scenario.function, scenario.what,
                                  scenario.value, dict(scenario.state))
    if calictl_frame is None:
        raise SystemExit("calictl has no builder for %s/%s" % (scenario.function, scenario.what))
    ours = control.decode_control(func, calictl_frame)
    theirs = control.decode_control(func, app_frame)
    rows, leads = [], []
    for cf in func.control_fields:
        if not cf.placed:
            continue
        a, o = theirs.get(cf.name), ours.get(cf.name)
        match = a == o
        rows.append(DiffRow(cf.name, a, o, match))
        if not match and o in (0, SENTINEL_2BIT) and a not in (None, 0, SENTINEL_2BIT):
            leads.append(cf.name)
    return rows, leads, calictl_frame


def format_report(scenario: Scenario, rows: list[DiffRow], leads: list[str],
                  app_frame: bytes, calictl_frame: bytes) -> str:
    lines = ["scenario: %s   (%s/%s = %s)"
             % (scenario.name, scenario.function, scenario.what, scenario.value),
             "app frame:     %s" % app_frame.hex(),
             "calictl frame: %s" % calictl_frame.hex(),
             "",
             "  %-22s %-8s %-8s %s" % ("field", "app", "calictl", ""),
             "  " + "-" * 46]
    for r in rows:
        flag = "" if r.match else ("  <-- LEAD" if r.name in leads else "  <-- differs")
        lines.append("  %-22s %-8s %-8s%s" % (r.name, r.app, r.calictl, flag))
    lines.append("")
    if not any(not r.match for r in rows):
        lines.append("RESULT: identical — calictl reproduces the app's frame for this action.")
    elif leads:
        lines.append("RESULT: LEADS — the app sets %s that calictl leaves at 0/sentinel. "
                     "This is the missing ingredient." % ", ".join(leads))
    else:
        lines.append("RESULT: differs (no zero-vs-value leads; review the mismatches above).")
    return "\n".join(lines)


def run(capture: str, scenario_name: str, *, frames: bool = False) -> int:
    funcs = protocol.load(); overrides.apply(funcs)
    scenario = load_scenario(scenario_name)
    if frames:
        writes = parse_frames_file(capture)
    else:
        writes = extract_att_writes(capture)
    app_frame = select_app_frame(scenario, writes, from_frames=frames)
    rows, leads, calictl_frame = diff(funcs, scenario, app_frame)
    print(format_report(scenario, rows, leads, app_frame, calictl_frame))
    return 0 if not any(not r.match for r in rows) else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="capture_diff",
                                description="Diff the real app's BLE control writes vs calictl's")
    p.add_argument("capture", help="HCI capture (pcap/pcapng via tshark) or a --frames file")
    p.add_argument("scenario", help="scenario name under tools/scenarios/, e.g. lighting/kitchen-50")
    p.add_argument("--frames", action="store_true",
                   help="treat `capture` as a normalized `<uuid|handle>: <hex>` list")
    args = p.parse_args(argv)
    return run(args.capture, args.scenario, frames=args.frames)


if __name__ == "__main__":
    raise SystemExit(main())
