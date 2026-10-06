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

App recordings (``tests/vectors/app/*.jsonl``, written by ``tools/applab/walk.py``) are a third
input: ``--frames`` reads their ``write`` events, and :func:`check_recording` replays a whole
recording — every non-neutral write attributed to the scenario step that caused it and diffed on
the whole frame (roof: the fields the app targets). ``--recording FILE`` prints that replay.

.. req:: Hold calictl to the real app's recorded frames
   :id: R_APP_FIDELITY
   :status: implemented
   :tags: control, evidence, applab

   Every control write the real app made in a committed recording (``tests/vectors/app/*.jsonl``)
   shall equal ``control.build`` for the action its scenario step names byte for byte (the roof,
   whose SafetyCounter the app generates: on the fields the app targets); a write calictl cannot
   attribute or build shall fail the replay; a recording shall start with its header and carry no
   VIN, VIN hash, passkey or MAC other than the fake unit's test identity.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from calictl import control, overrides, protocol, semantics, trace

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
    control_char: str  # short UUID, e.g. "1501"
    capture_label: str = ""
    handle: int | None = None  # ATT handle of that char, if known
    state: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, d: dict) -> Scenario:
        return cls(
            name=name,
            function=d["function"],
            what=d["what"],
            value=d["value"],
            control_char=str(d["control_char"]),
            capture_label=d.get("capture_label", ""),
            handle=d.get("handle"),
            state=d.get("state") or {},
        )


def load_scenario(name: str, root: str | Path | None = None) -> Scenario:
    """Load `tools/scenarios/<name>.yaml` (needs PyYAML — this is tooling, deps allowed)."""
    import yaml  # lazy: keep the module importable in the bleak/yaml-less test env

    base = Path(root) if root else Path(__file__).resolve().parent / "scenarios"
    path = base / (name + ".yaml")
    return Scenario.from_dict(name, yaml.safe_load(path.read_text()))


# --- capture frontends ------------------------------------------------------


def parse_frames_file(path: str | Path) -> list[tuple[str, bytes]]:
    """Normalized fallback input: lines `<uuid-or-handle>: <hex>`. Keys are a short UUID
    (`1501`), a full UUID, or a handle (`0x0022`). `#` comments and blanks ignored.
    An app recording (JSONL, first character ``{``) yields its ``write`` events instead,
    the ``1003`` heartbeat and ``f000`` skipped."""
    text = Path(path).read_text()
    if text.lstrip().startswith("{"):
        out_jl: list[tuple[str, bytes]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("ev") == "write" and ev.get("char") not in SKIP_CHARS:
                out_jl.append((ev["char"], bytes.fromhex(ev["hex"])))
        return out_jl
    out: list[tuple[str, bytes]] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, hexstr = line.partition(":")
        out.append((key.strip(), bytes.fromhex(hexstr.strip().replace(" ", ""))))
    return out


def extract_att_writes(path: str | Path, tshark: str = "tshark") -> list[tuple[int, bytes]]:
    """Extract (handle, value) for every ATT Write via tshark (correct reassembly)."""
    cmd = [
        tshark,
        "-r",
        str(path),
        "-Y",
        "btatt.opcode.method==0x12 || btatt.opcode.method==0x52",
        "-T",
        "fields",
        "-e",
        "btatt.handle",
        "-e",
        "btatt.value",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise SystemExit(
            "tshark not found — install Wireshark CLI, or use --frames. "
            "See .claude/skills/capture-and-diff/SKILL.md"
        ) from None
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
                str(key).lower().startswith("0x") and scenario.handle == int(str(key), 16)
            )
        else:
            matches = scenario.handle is not None and key == scenario.handle
            if not matches and key in HANDLE_UUID:
                matches = HANDLE_UUID[key] == target_short
        if matches:
            chosen = value
    if chosen is None:
        raise SystemExit(
            "no app write to control char %s (%s) found in the capture"
            % (scenario.control_char, "handle %s" % scenario.handle if scenario.handle else "unknown handle")
        )
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
    calictl_frame = control.build(
        funcs, scenario.function, scenario.what, scenario.value, dict(scenario.state)
    )
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


def format_report(
    scenario: Scenario, rows: list[DiffRow], leads: list[str], app_frame: bytes, calictl_frame: bytes
) -> str:
    lines = [
        "scenario: %s   (%s/%s = %s)" % (scenario.name, scenario.function, scenario.what, scenario.value),
        "app frame:     %s" % app_frame.hex(),
        "calictl frame: %s" % calictl_frame.hex(),
        "",
        "  %-22s %-8s %-8s %s" % ("field", "app", "calictl", ""),
        "  " + "-" * 46,
    ]
    for r in rows:
        flag = "" if r.match else ("  <-- LEAD" if r.name in leads else "  <-- differs")
        lines.append("  %-22s %-8s %-8s%s" % (r.name, r.app, r.calictl, flag))
    lines.append("")
    if not any(not r.match for r in rows):
        lines.append("RESULT: identical — calictl reproduces the app's frame for this action.")
    elif leads:
        lines.append(
            "RESULT: LEADS — the app sets %s that calictl leaves at 0/sentinel. "
            "This is the missing ingredient." % ", ".join(leads)
        )
    else:
        lines.append("RESULT: differs (no zero-vs-value leads; review the mismatches above).")
    return "\n".join(lines)


# --- app recordings (tests/vectors/app/*.jsonl, tools/applab/walk.py) --------------------------

HEADER_KEYS = frozenset({"recorded_by", "date", "avd", "scenario"})
RECORDING_EVENTS = frozenset(
    {
        "connect",
        "disconnect",
        "read",
        "write",
        "notify",
        "mtu",
        "subscribe",
        "pair",
        "step",
        "app_screen",
        "esp_state",
        "note",
    }
)
SKIP_CHARS = frozenset({"1003", "f000"})  # liveness heartbeat + the unmodelled generic write
# The app's neutral frame per function — every field at its leave-unchanged default. The app writes
# it 500 ms after every action (protocol-crosscheck-applab.md, "Heartbeat / arming"). A write's
# TARGETED fields are the ones that differ from it; a write with none is that flush.
APP_NEUTRAL_HEX = {
    "cooler": "ff771e3e1f1f",
    "airheater": "3f7b007f1f3f",
    "campingmode": "ff",
    "energy": "30",
    "lighting": "0e00000000000000eeeeeeeeeeeeeeee",
}
ROOF_NEUTRAL = {"Up": 0, "Down": 0}  # page-open / STOP frame; the SafetyCounter always moves
# Frames the app writes that are not an action calictl models — each with its evidence.
APP_ONLY_HEX = {
    "0d0c000000000000eeeeeeeeeeeeeeee": "lighting REQUEST_CONFIG screen-open pull; retired in calictl "
    "(photon-verified not an actuation gate, see the comment above control.decode_control)",
}
# Actions the app performs that calictl cannot build (or builds differently) yet, with the reason.
# A recorded write for one is reported as a gap; once calictl matches it the replay FAILS until the
# entry is removed, so this list cannot rot. Empty since the wake-up builder landed (A2).
GAPS: dict[tuple[str, str], str] = {}
# Functions whose recorded action is compared on its TARGETED fields only; every other function must
# match the app's frame BYTE FOR BYTE (untargeted fields at the app's leave-unchanged values, ruling
# R1 "calictl follows the app"). The roof is exempt: its SafetyCounter is app-generated (seed +
# elapsed/500 ms), never reproducible from the recording.
TARGETED_ONLY = frozenset({"roof"})
TEST_IDENTITY = "C0:FF:EE:CA:11:F0"  # the fake unit's identity — the only MAC a recording may hold
_MAC_RE = re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b")
_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_PAIR_KEYS = frozenset({"t", "t_ms", "conn", "ev", "state", "reason"})


class RecordingError(ValueError):
    """A recording that is not in the format: the message names the file and line."""


def load_recording(path: str | Path) -> tuple[dict, list[tuple[int, dict]]]:
    """Read an app recording.

    :param path: a ``tests/vectors/app/<scenario>.jsonl`` file.
    :returns: ``(header, [(line_no, event), ...])``.
    :raises RecordingError: no header on line 1, a line that is not JSON, or an unknown ``ev``.
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    try:
        header = json.loads(lines[0]) if lines else None
    except ValueError:
        header = None
    if not isinstance(header, dict) or set(header) != HEADER_KEYS:
        raise RecordingError("%s:1: first line must be the header %s" % (path, sorted(HEADER_KEYS)))
    events: list[tuple[int, dict]] = []
    for n, line in enumerate(lines[1:], 2):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError as e:
            raise RecordingError("%s:%d: not JSON (%s)" % (path, n, e)) from None
        if ev.get("ev") not in RECORDING_EVENTS:
            raise RecordingError("%s:%d: unknown ev %r" % (path, n, ev.get("ev")))
        events.append((n, ev))
    return header, events


def recording_hygiene(path: str | Path) -> list[str]:
    """The PII guard for a committed recording: ``avd`` is ``lab34``; every ``1002`` payload is
    ``<vin-hash>``; no VIN-shaped token; no MAC but :data:`TEST_IDENTITY`; ``pair`` events carry
    no extra key (a passkey).

    :returns: one message per violation, ``file:line: what`` (empty = clean).
    """
    header, events = load_recording(path)
    problems = []
    if header["avd"] != "lab34":
        problems.append("%s:1: avd %r (recordings are made on lab34)" % (path, header["avd"]))
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if _VIN_RE.search(line):
            problems.append("%s:%d: VIN-shaped token" % (path, n))
        for mac in _MAC_RE.findall(line):
            if mac.upper() != TEST_IDENTITY:
                problems.append("%s:%d: MAC %s (only %s may appear)" % (path, n, mac, TEST_IDENTITY))
    for n, ev in events:
        if ev.get("char") == "1002" and "hex" in ev and ev["hex"] != trace.REDACTED_VIN_HASH:
            problems.append("%s:%d: 1002 payload not redacted" % (path, n))
        if ev["ev"] == "pair" and set(ev) - _PAIR_KEYS:
            problems.append("%s:%d: pair event carries %s" % (path, n, sorted(set(ev) - _PAIR_KEYS)))
    return problems


def neutral_fields(funcs: dict, fn: str) -> dict | None:
    """The app's neutral control values for ``fn`` (:data:`APP_NEUTRAL_HEX` decoded; roof: Up/Down 0),
    or ``None`` when none is known (then every field of a write counts as targeted)."""
    if fn == "roof":
        return dict(ROOF_NEUTRAL)
    hx = APP_NEUTRAL_HEX.get(fn)
    return None if hx is None else control.decode_control(funcs[fn], bytes.fromhex(hx))


@dataclass
class WriteCheck:
    """One recorded control write and what the replay made of it.

    ``kind``: ``action`` (matched calictl), ``flush`` (the neutral frame), ``app-only``
    (:data:`APP_ONLY_HEX`), ``gap`` (:data:`GAPS`), ``error`` (see ``problem``)."""

    line: int
    fn: str | None
    hex: str
    kind: str
    step: int | None = None
    problem: str | None = None


def check_recording(
    path: str | Path, *, funcs: dict | None = None, gaps: dict | None = None
) -> list[WriteCheck]:
    """Replay a recording's control writes against calictl (:need:`R_APP_FIDELITY`).

    State for ``control.build``'s full-packet carry is the last ``read``/``notify`` of each function's
    state char. A write to ``1003``/``f000`` is skipped; a write for a function without a calictl
    builder fails; the app's neutral frame is a ``flush``; any other write must sit under a ``step``
    whose ``expect`` names its function, and it must equal calictl's frame byte for byte (a
    :data:`TARGETED_ONLY` function: on its targeted fields). Every step with an ``expect`` must
    have produced such a write.

    :param path: the recording.
    :param funcs: the loaded + overridden function table (loaded if omitted).
    :param gaps: known gaps, ``{(function, what): reason}``; default :data:`GAPS`.
    :returns: one :class:`WriteCheck` per checked write, plus one ``error`` per expectation no write met.
    """
    if funcs is None:
        funcs = protocol.load()
        overrides.apply(funcs)
    gaps = GAPS if gaps is None else gaps
    _, events = load_recording(path)
    state: dict[str, dict] = {}
    step: dict | None = None
    expected: dict[int, tuple[int, dict]] = {}
    hit: set[int] = set()
    out: list[WriteCheck] = []
    for line, ev in events:
        kind = ev["ev"]
        if kind == "step":
            step = ev
            if ev.get("expect"):
                expected[ev["n"]] = (line, ev)
            continue
        fn, hx = ev.get("fn"), ev.get("hex")
        if kind in ("read", "notify"):
            if fn in funcs and hx and hx != trace.REDACTED_VIN_HASH:
                decoded = protocol.decode(funcs[fn], bytes.fromhex(hx))
                if fn == "lighting":  # carry the config latch across later frames, exactly like serve
                    decoded = {**decoded, **semantics.lighting_config(state.get(fn), decoded)}
                state[fn] = decoded
            continue
        if kind != "write" or ev.get("char") in SKIP_CHARS:
            continue
        out.append(_check_write(funcs, gaps, state, step, line, ev, hit))
    for sn, (line, sev) in sorted(expected.items()):
        fn, what, value = sev["expect"]
        if sn not in hit and (fn, what) not in gaps:
            out.append(
                WriteCheck(
                    line,
                    fn,
                    "",
                    "error",
                    sn,
                    "step %d (%s %s) expected %s/%s=%r but the app wrote no such frame"
                    % (sn, sev["verb"], sev["arg"], fn, what, value),
                )
            )
    return out


def _check_write(funcs, gaps, state, step, line, ev, hit) -> WriteCheck:
    fn, hx, char = ev.get("fn"), ev["hex"], ev.get("char")
    if fn not in control.BUILDERS:
        return WriteCheck(
            line,
            fn,
            hx,
            "error",
            problem="write to char %s (fn=%s): calictl has no control builder for it" % (char, fn),
        )
    if hx in APP_ONLY_HEX:
        return WriteCheck(line, fn, hx, "app-only")
    app = control.decode_control(funcs[fn], bytes.fromhex(hx))
    neutral = neutral_fields(funcs, fn)
    targeted = list(app) if neutral is None else [k for k, v in neutral.items() if app.get(k) != v]
    if not targeted:
        return WriteCheck(line, fn, hx, "flush")
    sn = step["n"] if step else None
    exp = step.get("expect") if step else None
    if not exp or exp[0] != fn:
        return WriteCheck(
            line,
            fn,
            hx,
            "error",
            sn,
            "unattributed %s write %s (targets %s): step %s expects %s" % (fn, hx, targeted, sn, exp),
        )
    _, what, value = exp
    hit.add(sn)
    st = dict(state.get(fn, {}))
    # Pin the wake-up builder's clock to the recorded write: the phone's local time, read as UTC
    # (the lab AVD runs UTC; Task 7 of the A2 plan sets it).
    saved_now = control.local_now
    if ev.get("t") is not None:
        rec_now = datetime.datetime.fromtimestamp(ev["t"], datetime.UTC).replace(tzinfo=None)
        control.local_now = lambda: rec_now
    bad: list[DiffRow] = []
    leads: list[str] = []
    try:  # one pin spans the builder AND diff()'s own control.build
        try:
            ours = control.build(funcs, fn, what, value, st)
        except ValueError as e:
            return WriteCheck(
                line, fn, hx, "error", sn, "calictl refuses %s/%s=%r: %s" % (fn, what, value, e)
            )
        if ours is not None:
            scen = Scenario(
                name="step %s" % sn, function=fn, what=what, value=value, control_char=str(char), state=st
            )
            rows, leads, _ = diff(funcs, scen, bytes.fromhex(hx))
            whole = fn not in TARGETED_ONLY
            bad = [r for r in rows if (whole or r.name in targeted) and not r.match]
            if whole and not bad and ours.hex() != hx:  # bits outside every placed field
                bad = [DiffRow("frame", hx, ours.hex(), False)]
    finally:
        control.local_now = saved_now
    if (fn, what) in gaps:
        if ours is not None and not bad:
            return WriteCheck(
                line,
                fn,
                hx,
                "error",
                sn,
                "gap %s/%s is closed (calictl now matches %s): remove it from GAPS" % (fn, what, hx),
            )
        return WriteCheck(line, fn, hx, "gap", sn)
    if ours is None:
        return WriteCheck(
            line,
            fn,
            hx,
            "error",
            sn,
            "calictl has no builder for %s/%s=%r (add one, or list (%r, %r) in GAPS with a reason)"
            % (fn, what, value, fn, what),
        )
    if bad:
        detail = ", ".join(
            "%s app=%s calictl=%s%s" % (r.name, r.app, r.calictl, " LEAD" if r.name in leads else "")
            for r in bad
        )
        return WriteCheck(
            line,
            fn,
            hx,
            "error",
            sn,
            "step %s %s/%s=%r: app %s vs calictl %s: %s" % (sn, fn, what, value, hx, ours.hex(), detail),
        )
    return WriteCheck(line, fn, hx, "action", sn)


def run_recording(path: str) -> int:
    """Print the replay of one recording; exit status 1 if any write failed."""
    checks = check_recording(path)
    for c in checks:
        print(
            "%5d %-8s %-11s %-34s step=%-4s %s"
            % (c.line, c.kind, c.fn or "-", c.hex, c.step, c.problem or "")
        )
    return 1 if any(c.problem for c in checks) else 0


def run(capture: str, scenario_name: str, *, frames: bool = False) -> int:
    funcs = protocol.load()
    overrides.apply(funcs)
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
    p = argparse.ArgumentParser(
        prog="capture_diff", description="Diff the real app's BLE control writes vs calictl's"
    )
    p.add_argument(
        "capture", help="HCI capture (pcap/pcapng via tshark), a --frames file, or an app recording"
    )
    p.add_argument(
        "scenario", nargs="?", help="scenario name under tools/scenarios/, e.g. lighting/kitchen-50"
    )
    p.add_argument(
        "--frames",
        action="store_true",
        help="treat `capture` as a normalized `<uuid|handle>: <hex>` list or an app recording (JSONL)",
    )
    p.add_argument(
        "--recording",
        action="store_true",
        help="replay every write of an app recording (tests/vectors/app/*.jsonl)",
    )
    args = p.parse_args(argv)
    if args.recording:
        return run_recording(args.capture)
    if not args.scenario:
        p.error("a scenario is required unless --recording")
    return run(args.capture, args.scenario, frames=args.frames)


if __name__ == "__main__":
    raise SystemExit(main())
