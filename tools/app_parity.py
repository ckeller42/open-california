#!/usr/bin/env python3
"""The app's connection lifecycle next to calictl's and the ESP satellite's — a report, not a test.

    python3 -m tools.app_parity tests/vectors/app/session.jsonl
    python3 -m tools.app_parity --live tests/vectors/app/cooler.jsonl

The **app** column comes from the recording, per connection: MTU, reads before the first
subscribe, pairing, subscribe order, where the 1003 heartbeat starts and its period, the warm-up
from the last subscribe to the first read, the read order, ``1002``, the disconnect reason and the
reconnect gap. **calictl**'s column comes from code: ``calictl.device`` (``PersistentSession.start``
reads ``VERSION_CHAR`` + ``AUTH_CHAR``, subscribes everything, then starts the heartbeat; the
documented defaults of ``CALICTL_HEARTBEAT_PERIOD_S``/``_WARMUP_S`` via ``tools.gen_c_dict._env_default``;
reads in dictionary order) and ``SessionSupervisor.SESSION_BACKOFF``. The **ESP**'s column comes
from the C headers the firmware compiles: ``csrc/codec_chars.h`` (``CODEC_CHARS`` order and the
heartbeat timing; generated and ``--check``-ed in CI) and ``cali_session.h``
(``CALI_SESSION_BACKOFF_MIN/MAX_MS``, doubling per ``schedule_reconnect``). Its ordering —
heartbeat at ``link_up`` before discovery, subscribe then read in ``CODEC_CHARS`` order, no
``1002`` — is ``session.c``'s ``link_up``/``on_discovered``/``start_reads``.

``--live`` adds, per step, the app's screen texts next to the satellite page's texts and
``semantics.interpret`` of the ESP's raw ``fn``; numbers with units on one side only are flagged.
Timing rows are "same" within 20 %. Nothing here asserts: sub-project 2 turns rows into changes or
documented differences.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path

from calictl import device, overrides, protocol, semantics
from calictl.session import SessionSupervisor
from calictl.trace import char_short
from tools.capture_diff import load_recording
from tools.gen_c_dict import _env_default

REPO = Path(__file__).resolve().parents[1]
CODEC_CHARS_H = REPO / "csrc" / "codec_chars.h"
CALI_SESSION_H = REPO / "firmware" / "components" / "cali_core" / "include" / "cali_session.h"
TOLERANCE = 0.2
# German unit labels (Std = Stunden/h, Min = Minuten) appear in the app and the ESP page; re.I so
# "Min"/"Std" and the upper-case SI letters all match.
_UNIT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s?(%|°C|°|Std|Min|min|h|V|A|L|l)(?![A-Za-z])", re.I)


def _c_defines(path: Path) -> dict[str, int]:
    """``#define NAME <int>`` lines of a C header (hex or decimal, optional ``u``)."""
    text = path.read_text(encoding="utf-8")
    return {n: int(v, 0) for n, v in re.findall(r"^#define (\w+) (0x[0-9A-Fa-f]+|\d+)u?\s*$", text, re.M)}


def calictl_side() -> dict:
    """calictl's connect sequence, from code constants."""
    funcs = protocol.load()
    overrides.apply(funcs)
    return {
        "pre_reads": [char_short(device.VERSION_CHAR), char_short(device.AUTH_CHAR)],
        "beat_phase": "after subscribes",
        "beat_ms": round(_env_default("CALICTL_HEARTBEAT_PERIOD_S") * 1000),
        "warmup_ms": round(_env_default("CALICTL_HEARTBEAT_WARMUP_S") * 1000),
        "reads": [char_short(f.state_char) for f in funcs.values() if f.state_char],
        "subscribes": None,  # BlueZ discovery (handle) order — not a code constant
        "backoff_s": list(SessionSupervisor.SESSION_BACKOFF),
    }


def esp_side() -> dict:
    """The ESP satellite's connect sequence, from the headers its firmware compiles."""
    chars = [
        c for _, c in re.findall(r'\{"(\w+)", 0x([0-9a-f]{4})\}', CODEC_CHARS_H.read_text(encoding="utf-8"))
    ]
    d = _c_defines(CODEC_CHARS_H) | _c_defines(CALI_SESSION_H)
    lo, hi = d["CALI_SESSION_BACKOFF_MIN_MS"], d["CALI_SESSION_BACKOFF_MAX_MS"]
    backoff, b = [], lo
    while b < hi:
        backoff.append(b / 1000)
        b *= 2
    backoff.append(hi / 1000)
    return {
        "pre_reads": [],
        "beat_phase": "before subscribes",
        "beat_ms": d["CODEC_HEARTBEAT_PERIOD_MS"],
        "warmup_ms": d["CODEC_HEARTBEAT_WARMUP_MS"],
        "reads": chars,
        "subscribes": chars,
        "backoff_s": backoff,
    }


def connections(events: list[tuple[int, dict]]) -> list[dict]:
    """The app's column: the fake's events split by ``conn`` and summarised per connection."""
    by: dict[int, list[dict]] = {}
    for _, ev in events:
        if ev.get("conn"):
            by.setdefault(ev["conn"], []).append(ev)
    out = []
    for n in sorted(by):
        evs = by[n]
        subs = [i for i, e in enumerate(evs) if e["ev"] == "subscribe"]
        first_sub, last_sub = (subs[0], subs[-1]) if subs else (None, None)
        reads = [(i, e) for i, e in enumerate(evs) if e["ev"] == "read"]
        beats = [(i, e) for i, e in enumerate(evs) if e["ev"] == "write" and e.get("char") == "1003"]
        post: list[str] = []
        for i, e in reads:
            if last_sub is not None and i > last_sub and e["char"] not in post:
                post.append(e["char"])
        phase = None
        if beats and first_sub is not None:
            bi = beats[0][0]
            phase = (
                "before subscribes"
                if bi < first_sub
                else "after subscribes"
                if bi > last_sub
                else "during subscribes"
            )
        gaps = [b["t_ms"] - a["t_ms"] for (_, a), (_, b) in zip(beats, beats[1:])]
        first_post = next((e for i, e in reads if last_sub is not None and i > last_sub), None)
        disc = next((e for e in evs if e["ev"] == "disconnect"), None)
        out.append(
            {
                "conn": n,
                "t_connect": evs[0]["t"],
                "t_disconnect": disc["t"] if disc else None,
                "mtu": next((e["mtu"] for e in evs if e["ev"] == "mtu"), None),
                "pre_reads": [e["char"] for i, e in reads if first_sub is None or i < first_sub],
                "pair": [e["state"] for e in evs if e["ev"] == "pair"],
                "subscribes": [evs[i]["char"] for i in subs],
                "beat_phase": phase,
                "beat_ms": round(statistics.median(gaps)) if gaps else None,
                "warmup_ms": first_post["t_ms"] - evs[last_sub]["t_ms"]
                if first_post and last_sub is not None
                else None,
                "reads": post,
                "reason": disc.get("reason") if disc else None,
            }
        )
    return out


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, list):
        return ",".join(_fmt(x) for x in v) if v else "(none)"
    if isinstance(v, float):
        return "%g" % v
    return str(v)


def verdict(app, *others, timing: bool = False) -> str:
    """``same`` when every known runtime value equals the app's (timing: within 20 %),
    ``differs`` when one does not, ``n/a`` when the app's or every runtime's value is unknown."""
    if app is None or all(o is None for o in others):
        return "n/a"
    for o in others:
        if o is None:
            continue
        if timing:
            if abs(app - o) > TOLERANCE * o:
                return "differs"
        elif o != app:
            return "differs"
    return "same"


def lifecycle_rows(c: dict, cal: dict, esp: dict) -> list[tuple[str, str, str, str, str]]:
    """One connection's rows: (step, app, calictl, ESP, verdict)."""
    rows: list[tuple[str, str, str, str, str]] = []

    def add(step, a, ca, e, timing=False):
        rows.append((step, _fmt(a), _fmt(ca), _fmt(e), verdict(a, ca, e, timing=timing)))

    def has_1002(side):
        return "yes" if "1002" in side["pre_reads"] + side["reads"] else "no"

    add("MTU", c["mtu"], None, None)
    add("reads before subscribe", c["pre_reads"], cal["pre_reads"], esp["pre_reads"])
    add("reads 1002", has_1002(c), has_1002(cal), has_1002(esp))
    add("pairing", c["pair"] or None, None, None)
    add("subscribe order", c["subscribes"], cal["subscribes"], esp["subscribes"])
    add("heartbeat starts", c["beat_phase"], cal["beat_phase"], esp["beat_phase"])
    add("heartbeat period ms", c["beat_ms"], cal["beat_ms"], esp["beat_ms"], timing=True)
    add("warm-up before reads ms", c["warmup_ms"], cal["warmup_ms"], esp["warmup_ms"], timing=True)
    add("read order", c["reads"], cal["reads"], esp["reads"])
    add("disconnect reason", c["reason"], None, None)
    return rows


def render(path) -> str:
    """The lifecycle report of one recording (markdown)."""
    header, events = load_recording(path)
    cal, esp = calictl_side(), esp_side()
    lines = [
        "# %s — %s, %s, %s" % (header["scenario"], header["recorded_by"], header["date"], header["avd"]),
        "",
    ]
    conns = connections(events)
    for i, c in enumerate(conns):
        rows = lifecycle_rows(c, cal, esp)
        if i + 1 < len(conns) and c["t_disconnect"] is not None:
            gap = round(conns[i + 1]["t_connect"] - c["t_disconnect"], 1)
            c0, e0 = cal["backoff_s"][0], esp["backoff_s"][0]
            rows.append(("reconnect gap s", _fmt(gap), _fmt(c0), _fmt(e0), verdict(gap, c0, e0, timing=True)))
        rows.append(("backoff schedule s", "-", _fmt(cal["backoff_s"]), _fmt(esp["backoff_s"]), "n/a"))
        lines += [
            "## connection %d" % c["conn"],
            "",
            "| step | app | calictl | ESP | verdict |",
            "|---|---|---|---|---|",
        ]
        lines += ["| %s |" % " | ".join(r) for r in rows] + [""]
    if not conns:
        lines.append("(no connection in this recording)")
    return "\n".join(lines)


def units(texts: list[str]) -> set[str]:
    """Numbers with a unit in screen texts, normalised (``60 min`` -> ``60min``, ``12,5 V`` -> ``12.5V``)."""
    return {"%s%s" % (m.group(1).replace(",", "."), m.group(2)) for t in texts for m in _UNIT_RE.finditer(t)}


def live_rows(events: list[tuple[int, dict]]) -> list[dict]:
    """Per step with both screens: app texts, satellite texts, interpreted ESP values, one-sided units."""
    steps = {e["n"]: e for _, e in events if e["ev"] == "step"}
    app = {e["step"]: e for _, e in events if e["ev"] == "app_screen"}
    esp = {e["step"]: e for _, e in events if e["ev"] == "esp_state"}
    out = []
    for n in sorted(app.keys() & esp.keys()):
        a, s = app[n]["texts"], esp[n]["texts"]
        screen = esp[n].get("screen")
        fields = (esp[n].get("fn") or {}).get(screen)
        interp = semantics.interpret(screen, fields) if isinstance(fields, dict) else {}
        st = steps.get(n, {})
        ua, us = units(a), units(s)
        out.append(
            {
                "step": n,
                "action": "%s %s" % (st.get("verb", "?"), " ".join(str(x) for x in st.get("arg", []))),
                "app": a,
                "satellite": s,
                "interpreted": {k: v for k, v in interp.items() if not isinstance(v, dict | list)},
                "only_app": sorted(ua - us),
                "only_satellite": sorted(us - ua),
                "error": esp[n].get("error"),
            }
        )
    return out


def render_live(path) -> str:
    """The ``--live`` table: | step | action | app | satellite | interpreted | flags |."""
    _, events = load_recording(path)
    lines = ["| step | action | app | satellite | interpreted | flags |", "|---|---|---|---|---|---|"]
    for r in live_rows(events):
        flags = ["app only: " + ", ".join(r["only_app"])] if r["only_app"] else []
        flags += ["satellite only: " + ", ".join(r["only_satellite"])] if r["only_satellite"] else []
        flags += ["ESP error: " + r["error"]] if r["error"] else []
        interp = "; ".join("%s=%s" % kv for kv in sorted(r["interpreted"].items()))
        lines.append(
            "| %d | %s | %s | %s | %s | %s |"
            % (
                r["step"],
                r["action"],
                " · ".join(r["app"]),
                " · ".join(r["satellite"]),
                interp,
                "; ".join(flags),
            )
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="app_parity", description="app / calictl / ESP lifecycle report")
    ap.add_argument("recording")
    ap.add_argument("--live", action="store_true", help="also the per-step app-vs-satellite screen table")
    a = ap.parse_args(argv)
    print(render(a.recording))
    if a.live:
        print()
        print(render_live(a.recording))
    return 0


if __name__ == "__main__":
    sys.exit(main())
