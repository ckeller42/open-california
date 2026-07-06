"""Harvest per-field scale multipliers + enums from the decompiled app."""
from __future__ import annotations
import os, re

def scales(root: str) -> dict:
    out = {}
    for dp, _, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".java"):
                continue
            path = os.path.join(dp, fn)
            try:
                txt = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for m in re.finditer(r"\b(\w+)\s*\*\s*([0-9.]+)f?", txt):
                field, mul = m.group(1), m.group(2).rstrip(".")
                if field[0].isupper() and field not in out and mul not in ("0", "1"):
                    out[field] = {"scale": mul, "getter": "%s" % os.path.relpath(path, root),
                                  "enum": None}
    return out
