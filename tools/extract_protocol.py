#!/usr/bin/env python3
"""Extract the VW California Camper Unit control dictionary from the decompiled
CaliforniaOnTour app.

Pure static extraction, keyed by field OBJECT (e.g. f23982e0) so name, width,
default, and bit offset stay aligned even when a class serves two services
(shared fridge/heater control model) or when field counts differ from sg.a counts.

Sources joined per field object:
- name   : the "--> Sending Data:" / "<-- Incoming Data for X:" debug logs
           (`Object o = this.<obj>.f398b` order + the quoted labels)
- width  : `this.<obj> = new sg.a(default, typecode)`  (typecode -> bit width)
- offset : the control model's f() placements  `frame[P] = <local>[k]` where
           `<local> = this.<obj>.f399c`; emitted only when a field's positions are
           unambiguous (a field placed differently across two merged branches is
           left offset: MERGED_AMBIGUOUS).

Usage:  python3 extract_protocol.py <decompile/.../sources> [out.yaml]
Stdlib only. Output holds protocol facts only (names, bits) — no app source.
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
SGA_RE = re.compile(r'this\.(\w+)\s*=\s*new sg\.a\((\d+),\s*(\d+)\)')
BOBJ_RE = re.compile(r'this\.(\w+)\)?\.f398b')           # field objects in log order (tolerate cast `)`)
FLOCAL_RE = re.compile(r'\(Boolean\[\]\)\s*(?:\(sg\.a\)\s*)?this\.(\w+)\.f399c')  # obj -> local (by position)
FLOCAL_DECL_RE = re.compile(r'(\w+)\s*=\s*\(Boolean\[\]\)\s*(?:\(sg\.a\)\s*)?this\.(\w+)\.f399c')  # local = this.obj
FPLACE_RE = re.compile(r'\w+\[(\d+)\]\s*=\s*(\w+)\[(\d+)\]')  # frame[P] = local[k]
SUBLIST_RE = re.compile(r'(\w+)\.p\(\(Boolean\[\]\)[^;]*?subList\((\d+),\s*(\d+)\)')  # state decode aVar<-bits
STATE_LOGOBJ_RE = re.compile(r'=\s*(\w+)\.f398b')     # aVar in the state log, in order
ENUM_RE = re.compile(r'new \w+\("([A-Z][A-Z0-9_]{2,})"')  # command/mode enum constants
TYPECODE_WIDTH = {0: 1, 2: 8, 3: 16, 4: 2, 5: 32, 6: 4, 7: 8}
LOG_END = re.compile(r'\.toString\(\)|xm\.a\.b\(')


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def java_files(root):
    for dp, _, files in os.walk(root):
        for fn in files:
            if fn.endswith(".java"):
                yield os.path.join(dp, fn)


def uuids(text):
    return sorted({m.group(1).lower() for m in UUID_RE.finditer(text)})


def _labels(window):
    out = []
    for q in QUOTED_RE.finditer(window):
        s = re.sub(r'^<-- Incoming Data for [A-Za-z0-9]+:', '', q.group(1))
        s = re.sub(r'^--> Sending Data:?', '', s).strip()
        m = LABEL_RE.match(s)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def _methods_with(text, marker_re):
    """Yield the source window of each method that contains the marker."""
    for m in marker_re.finditer(text):
        end = LOG_END.search(text, m.start())
        # widen slightly past the emit so the obj= assignments before it are included
        lo = text.rfind("\n    public ", 0, m.start())
        lo = lo if lo != -1 else m.start()
        hi = end.start() if end else m.start() + 4000
        yield text[lo:hi], m.start()


def obj_names(text, marker_re):
    """[{obj: name}] — one dict per control debug-log method (uses this.<obj>.f398b)."""
    result = []
    for win, _mstart in _methods_with(text, marker_re):
        objs = BOBJ_RE.findall(win)
        names = _labels(win)   # same order as objs (each field printed once with its label)
        result.append({o: n for o, n in zip(objs, names)})
    return result


def state_field_names(text):
    """State decode (`e()`) uses local aVar objects, not this.<obj> — take labels only."""
    for win, _ in _methods_with(text, INCOMING_RE):
        return _labels(win)
    return []


def constructors(text):
    cm = re.search(r'class\s+(\w+)', text)
    if not cm:
        return []
    ctor_re = re.compile(r'\n    public ' + re.escape(cm.group(1)) + r'\(')
    boundary = re.compile(r'\n    (?:public|private|protected|final|static|void|Object|boolean|int) ')
    blocks = []
    for m in ctor_re.finditer(text):
        nxt = boundary.search(text, m.start() + 5)
        blocks.append(text[m.start(): nxt.start() if nxt else len(text)])
    return blocks


def field_offsets(text):
    """obj -> sorted unique frame-bit positions from all f() placements.

    A field placed at the SAME positions in two merged branches is fine (dedup);
    a field placed at DIFFERENT positions spans more than its width -> the caller
    detects that (run length != field width) and flags it ambiguous.
    """
    local2obj = {loc: obj for loc, obj in FLOCAL_DECL_RE.findall(text)}
    positions: dict[str, set[int]] = {}
    for P, local, _k in FPLACE_RE.findall(text):
        obj = local2obj.get(local)
        if obj:
            positions.setdefault(obj, set()).add(int(P))
    return {obj: sorted(ps) for obj, ps in positions.items()}


def state_offsets(text):
    """[{name, offset, width}] for the state frame, best-effort.

    subList(a,b) placements give aVar -> (offset,width); the "Incoming Data" log's
    `= aVar.f398b` order gives aVar -> name. Joined by aVar; a field whose aVar has
    no subList (jadx aliasing) is emitted name-only.
    """
    bits = {av: (int(a), int(b) - int(a)) for av, a, b in SUBLIST_RE.findall(text)}
    for win, _ in _methods_with(text, INCOMING_RE):
        avars = STATE_LOGOBJ_RE.findall(win)
        names = _labels(win)
        out = []
        for av, name in zip(avars, names):
            f = {"name": name}
            if av in bits:
                f["offset"], f["width"] = bits[av]
            out.append(f)
        return out
    return []


def enums(text):
    """Uppercase command/mode enum constant groups defined in a file."""
    return [m for m in ENUM_RE.findall(text)]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root, out = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "protocol/dictionary.yaml")

    functions = {}
    control_ctors = []  # {file, ctrl_uuid, decls:{obj:(d,w)}, offsets:{obj:(off,w)|None}, blogs:[{obj:name}]}
    command_enums = {}  # file -> [enum constants]  (value-semantic command vocabularies)

    for p in java_files(root):
        t = read(p)
        rel = os.path.relpath(p, root)
        # harvest command-enum vocabularies (drop alert IDs + BLE-internal error enums)
        INTERNAL = ("BLUETOOTH", "SCANNING", "PERMISSION", "TIMEOUT", "TIMED_OUT",
                    "RECONNECT", "VERSION_CHECK", "SUBSCRIPTION", "WIFI_EXLAP", "DISCONNECT")
        ens = [e for e in enums(t)
               if not e.endswith(("_ID", "_NOTIFICATION", "_CONFIRMED", "_INTERACTED"))
               and not any(k in e for k in INTERNAL)]
        cmds = [e for e in ens if any(k in e for k in ("SET_", "MODE", "_TIME", "REQUEST", "PROFILE", "PREVIEW", "WAKEUP"))]
        # keep only where command verbs dominate the group (drops incidental constants)
        if len(cmds) >= 3 and len(cmds) >= 0.5 * len(ens):
            command_enums[rel] = sorted(set(ens))
        im = INCOMING_RE.search(t)
        if im:
            fn = im.group(1)
            sfields = state_offsets(t)
            functions[fn] = {
                "state_fields": sfields, "state_names": [f["name"] for f in sfields],
                "state_uuids": uuids(t),
                "control": [], "control_uuid": None, "control_source": None,
            }
        if SENDING_RE.search(t):
            blogs = obj_names(t, SENDING_RE)
            offsets = field_offsets(t)
            for ctor in constructors(t):
                cu = uuids(ctor)
                decls = {o: (int(d), TYPECODE_WIDTH.get(int(tc), int(tc)))
                         for o, d, tc in SGA_RE.findall(ctor)}
                if cu and decls:
                    control_ctors.append({"file": rel, "ctrl_uuid": cu[0],
                                          "decls": decls, "offsets": offsets, "blogs": blogs})

    fn_by_prefix = {}
    for fn, e in functions.items():
        for u in e["state_uuids"]:
            fn_by_prefix.setdefault(u[:2], fn)

    unresolved = []
    for cc in control_ctors:
        fn = fn_by_prefix.get(cc["ctrl_uuid"][:2])
        if not fn:
            unresolved.append(cc)
            continue
        state_names = set(functions[fn]["state_names"])
        # pick the debug-log name map whose values best overlap this function's state
        blog = max((b for b in cc["blogs"] if b),
                   key=lambda b: len(set(b.values()) & state_names), default={})
        # iterate the full named field set (log order), plus any sg.a-only objs, so
        # nothing is dropped; attach width/default/offset per object where present.
        order = list(blog.keys()) + [o for o in cc["decls"] if o not in blog]
        fields = []
        for obj in order:
            default, width = cc["decls"].get(obj, (None, None))
            ps = cc["offsets"].get(obj, [])
            ok = width is not None and len(ps) == width and ps[-1] - ps[0] + 1 == width
            fields.append({"obj": obj, "name": blog.get(obj, obj),
                           "width": width, "default": default,
                           "offset": ps[0] if ok else None})
        functions[fn]["control"] = fields
        functions[fn]["control_uuid"] = cc["ctrl_uuid"]
        functions[fn]["control_source"] = cc["file"]

    _emit(functions, unresolved, command_enums, out)
    _report(functions, unresolved)
    return 0


def _emit(functions, unresolved, command_enums, out):
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    L = ["# VW California Camper Unit control dictionary",
         "# Auto-extracted (static, object-keyed) by tools/extract_protocol.py.",
         "# name/width/default/offset are per field object. offset MERGED_AMBIGUOUS when a",
         "# field is placed differently across a shared (fridge/heater) control model.",
         "# value_semantics UNVERIFIED (enum meanings/ranges) until a live pass.",
         "functions:"]
    for fn in sorted(functions):
        e = functions[fn]
        L.append("  %s:" % fn.lower())
        L.append("    state_services: [%s]" % ", ".join(e["state_uuids"]))
        if e["control_source"]:
            L.append("    control_source: %s   # control char %s" % (e["control_source"], e["control_uuid"]))
        L.append("    control_fields:")
        if not e["control"]:
            L.append("      []   # no unambiguous control model (see control_models_unresolved)")
        for f in e["control"]:
            off = f["offset"] if f["offset"] is not None else "MERGED_AMBIGUOUS"
            if f["width"] is not None:
                rng = "0..%d" % (2 ** f["width"] - 1)
                wd = "width: %d, default: %d, raw_range: %s" % (f["width"], f["default"], rng)
            else:
                wd = "width: UNKNOWN, default: UNKNOWN"
            L.append("      - {name: %s, offset: %s, %s, value_semantics: UNVERIFIED}"
                     % (f["name"], off, wd))
        L.append("    state_fields:")
        for f in e["state_fields"]:
            if "offset" in f:
                L.append("      - {name: %s, offset: %d, width: %d}" % (f["name"], f["offset"], f["width"]))
            else:
                L.append("      - {name: %s}" % f["name"])
    L += ["", "# Control models with no matching state function (resolve via a live pass).",
          "control_models_unresolved:"]
    for cm in unresolved:
        names = [b for b in cm["blogs"] if b]
        flds = list((names[0] if names else {}).values()) or list(cm["decls"].keys())
        L.append("  - file: %s   # control char %s" % (cm["file"], cm["ctrl_uuid"]))
        L.append("    fields: [%s]" % ", ".join(flds))
    L += ["", "# Command / mode enum vocabularies found in the app (value semantics for the",
          "# Mode-style fields above; map to a service by package).",
          "command_enums:"]
    for f in sorted(command_enums):
        L.append("  %s: [%s]" % (f.replace("/", "_").replace(".java", ""), ", ".join(command_enums[f])))
    with open(out, "w") as f:
        f.write("\n".join(L) + "\n")
    print("wrote %s (%d functions, %d unresolved)" % (out, len(functions), len(unresolved)))


def _report(functions, unresolved):
    print("\n=== coverage ===")
    for fn in sorted(functions):
        e = functions[fn]
        c = e["control"]
        placed = sum(1 for f in c if f["offset"] is not None)
        print("  %-22s control=%2d (offsets %d/%d)  state=%2d  src=%s" % (
            fn, len(c), placed, len(c), len(e["state_names"]), e["control_source"] or "-"))
    print("=== unresolved: %d ===" % len(unresolved))
    for cm in unresolved:
        print("  %-12s ctrl=%s" % (cm["file"], cm["ctrl_uuid"]))


if __name__ == "__main__":
    raise SystemExit(main())
