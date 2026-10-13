"""Detect app setter methods with NON-TRIVIAL transforms, so the auditor can flag
fields whose meaning a naive `bool(field)` interpreter is likely to get wrong.

Two signatures (the ones that bit us on camping lights):
  - INVERTED write: `!on ? 1 : 0` / `!on ? 0 : 1` — the field's raw value is the
    logical opposite of the user-facing on/off.
  - COMBINED write: the same value written to >=2 field holders in one setter — one
    UI control that drives multiple fields together.

Functions are identified by the `Incoming Data for <Fn>:` debug log that sits in the
same class as the setters. Best-effort + stdlib-only; results are review flags, not
gospel (the decompiled tree is R8-obfuscated).

R8 renames MEMBERS per build too, not only classes: the bit-field stage call was
`.o(Integer.valueOf(x))` in app 5.0.8.3028 and `.l(Integer.valueOf(x))` in 5.4.0.3036.
The regexes below therefore key on the call SHAPE, never on a member name — a name-keyed
regex silently matches nothing after a version bump (COMBINED-CONTROL vanished for every
function on the 5.4.0 tree before this was fixed). Run against the RAW jadx tree, not the
mapping-renamed `named/` tree.
"""

from __future__ import annotations

import os
import re

_INV_WRITE = re.compile(r"!\s*\w+\s*\?\s*1\s*:\s*0|!\s*\w+\s*\?\s*0\s*:\s*1")  # setter inverts value
_INV_READ = re.compile(r"!\s*\(\(Boolean\)")  # readback getter inverts a field (the READ risk)
_WRITE = re.compile(r"\.\w{1,2}\(Integer\.valueOf\((\w+)\)\)")  # <holder>.<r8 name>(Integer.valueOf(local))
_FN = re.compile(r"Incoming Data for (\w+)")


def nontrivial(root: str) -> dict:
    """{function: {"inverted": bool, "combined": bool, "where": relpath}} for every
    function whose feature class has a non-trivial transform. `inverted` means a field
    is stored/displayed as the logical OPPOSITE — a setter inversion (`!on?1:0`) OR,
    more importantly for reads, an inverted readback getter (`!((Boolean)…)`). This is
    the signal that a naive `bool(field)` interpreter will disagree with the app."""
    out: dict = {}
    for dp, _, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".java"):
                continue
            path = os.path.join(dp, fn)
            try:
                txt = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            m = _FN.search(txt)
            if not m:
                continue
            func = m.group(1).lower()
            inverted = bool(_INV_WRITE.search(txt)) or bool(_INV_READ.search(txt))
            writes = _WRITE.findall(txt)
            combined = any(writes.count(v) >= 2 for v in set(writes))  # same value -> 2+ holders
            if inverted or combined:
                e = out.setdefault(
                    func, {"inverted": False, "combined": False, "where": os.path.relpath(path, root)}
                )
                e["inverted"] = e["inverted"] or inverted
                e["combined"] = e["combined"] or combined
    return out
