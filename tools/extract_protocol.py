#!/usr/bin/env python3
"""Extract the VW California Camper Unit control dictionary from the decompiled
CaliforniaOnTour app.

Pure static extraction. Harvests every function's control + state field names
from the app's own debug-log methods ("--> Sending Data:" for control models,
"<-- Incoming Data for <Fn>:" for state), plus per-field bit width/default from
the control model's `new sg.a(default, typecode)` declarations.

Merged control models (one class serving two services via two constructors, e.g.
fridge 1101 + heater 1701) are NOT force-attributed — that mis-attribution is
exactly the bug this avoids. A control model is joined to a function only when its
service set is unambiguous; otherwise it is listed under `control_models` with the
services it touches, for a human/live pass to resolve.

Usage:  python3 extract_protocol.py <decompile/.../sources> [out.yaml]
Stdlib only. Output contains only protocol facts (field names, bits) — no app source.
"""
from __future__ import annotations
import os
import re
import sys

UUID_RE = re.compile(r'0000([0-9a-fA-F]{4})-6[cC]77-4[bB]7[dD]-[bB][bB][fF]6-[aA]5[eE]587701[fF]3[dD]')
INCOMING_RE = re.compile(r'"<-- Incoming Data for ([A-Za-z0-9]+)')
SENDING_RE = re.compile(r'"--> Sending Data')
QUOTED_RE = re.compile(r'"([^"]*)"')
LABEL_RE = re.compile(r'^([A-Za-z][A-Za-z0-9]*):?$')
SGA_RE = re.compile(r'new sg\.a\((\d+),\s*(\d+)\)')
TYPECODE_WIDTH = {0: 1, 2: 8, 3: 16, 4: 2, 5: 32, 6: 4, 7: 8}
LOG_END = re.compile(r'\.toString\(\)|xm\.a\.b\(')


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def java_files(root: str):
    for dp, _, files in os.walk(root):
        for fn in files:
            if fn.endswith(".java"):
                yield os.path.join(dp, fn)


def log_labels(text: str, start: int) -> list[str]:
    """Ordered field labels between a log marker and the log-emit call."""
    end_m = LOG_END.search(text, start)
    window = text[start: end_m.start() if end_m else start + 4000]
    names: list[str] = []
    for q in QUOTED_RE.finditer(window):
        s = q.group(1)
        s = re.sub(r'^<-- Incoming Data for [A-Za-z0-9]+:', '', s)
        s = re.sub(r'^--> Sending Data:?', '', s)
        s = s.strip()
        m = LABEL_RE.match(s)
        if m and m.group(1) not in names:
            names.append(m.group(1))
    return names


def uuids(text: str) -> list[str]:
    return sorted({m.group(1).lower() for m in UUID_RE.finditer(text)})


def _constructors(text: str) -> list[str]:
    """Each constructor body (a class may have several — one per shared service)."""
    cm = re.search(r'class\s+(\w+)', text)
    if not cm:
        return []
    ctor_re = re.compile(r'\n    public ' + re.escape(cm.group(1)) + r'\(')
    method_re = re.compile(r'\n    (?:public|private|protected|final|static|void|Object) ')
    blocks = []
    for m in ctor_re.finditer(text):
        s = m.start()
        nxt = method_re.search(text, s + 5)
        blocks.append(text[s: nxt.start() if nxt else len(text)])
    return blocks


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root, out = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "protocol/dictionary.yaml")

    functions: dict[str, dict] = {}   # by state-log function name
    control_ctors: list[dict] = []    # per-constructor: {file, fields, ctrl_uuid, sga}

    for p in java_files(root):
        t = read(p)
        rel = os.path.relpath(p, root)
        im = INCOMING_RE.search(t)
        if im:
            fn = im.group(1)
            functions[fn] = {
                "state_fields": log_labels(t, im.start()),
                "state_uuids": uuids(t),
                "control_fields": [], "control_sga": [], "control_uuid": None,
                "control_source": None,
            }
        if SENDING_RE.search(t):
            # a control-model file may hold several "Sending Data" logs + constructors
            # (merged classes serving multiple services, each with its own name-set + defaults)
            blogs = [log_labels(t, m.start()) for m in SENDING_RE.finditer(t)]
            for ctor in _constructors(t):
                cu = uuids(ctor)
                sga = [(int(a), TYPECODE_WIDTH.get(int(b), int(b))) for a, b in SGA_RE.findall(ctor)]
                if cu and sga:
                    control_ctors.append({"file": rel, "blogs": blogs,
                                          "ctrl_uuid": cu[0], "sga": sga})

    # attach each constructor to the function whose service prefix matches its control char,
    # then pick the field-name log that best overlaps THAT function's state fields.
    fn_by_prefix: dict[str, str] = {}
    for fn, e in functions.items():
        for u in e["state_uuids"]:
            fn_by_prefix.setdefault(u[:2], fn)
    unresolved: list[dict] = []
    for cc in control_ctors:
        fn = fn_by_prefix.get(cc["ctrl_uuid"][:2])
        if not fn:
            unresolved.append(cc)
            continue
        state = set(functions[fn]["state_fields"])
        blogs = [b for b in cc["blogs"] if b]
        best = max(blogs, key=lambda b: len(set(b) & state), default=[])
        functions[fn]["control_fields"] = best
        functions[fn]["control_sga"] = cc["sga"]
        functions[fn]["control_uuid"] = cc["ctrl_uuid"]
        functions[fn]["control_source"] = cc["file"]

    _emit(functions, unresolved, out)
    _report(functions, unresolved)
    return 0


def _emit(functions, unresolved, out):
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    L = ["# VW California Camper Unit control dictionary",
         "# Auto-extracted (static) from the decompiled app by tools/extract_protocol.py.",
         "# Field names are the app's own debug-log labels. Widths from sg.a typecodes.",
         "# All value_semantics UNVERIFIED until a live pass; bit offsets need f()/e() (todo).",
         "functions:"]
    for fn in sorted(functions):
        e = functions[fn]
        L.append("  %s:" % fn.lower())
        L.append("    state_services: [%s]" % ", ".join(e["state_uuids"]))
        if e["control_source"]:
            L.append("    control_source: %s   # control char %s" % (e["control_source"], e["control_uuid"]))
        L.append("    control_fields:")
        cf, sga = e["control_fields"], e["control_sga"]
        if not cf:
            L.append("      []   # no unambiguous control model (see control_models_unresolved)")
        for i, name in enumerate(cf):
            if i < len(sga):
                d, w = sga[i]
                L.append("      - {name: %s, width: %d, default: %d, value_semantics: UNVERIFIED}" % (name, w, d))
            else:
                L.append("      - {name: %s, value_semantics: UNVERIFIED}" % name)
        L.append("    state_fields:")
        for name in e["state_fields"]:
            L.append("      - {name: %s}" % name)
    L.append("")
    L.append("# Control models that couldn't be uniquely attributed (merged classes serving")
    L.append("# multiple services). Resolve which function each belongs to via a live pass.")
    L.append("control_models_unresolved:")
    for cm in unresolved:
        L.append("  - file: %s   # control char %s (no matching state function)" % (cm["file"], cm["ctrl_uuid"]))
        L.append("    candidate_field_sets: %s" % (cm["blogs"] or "[]"))
    with open(out, "w") as f:
        f.write("\n".join(L) + "\n")
    print("wrote %s (%d functions, %d unresolved control models)" % (out, len(functions), len(unresolved)))


def _report(functions, unresolved):
    print("\n=== coverage (functions) ===")
    for fn in sorted(functions):
        e = functions[fn]
        print("  %-24s control=%2d  state=%2d  src=%s" % (
            fn, len(e["control_fields"]), len(e["state_fields"]), e["control_source"] or "-"))
    print("=== unresolved control constructors: %d ===" % len(unresolved))
    for cm in unresolved:
        print("  %-14s ctrl=%s  field_sets=%d" % (cm["file"], cm["ctrl_uuid"], len(cm["blogs"])))


if __name__ == "__main__":
    raise SystemExit(main())
