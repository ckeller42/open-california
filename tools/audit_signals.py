"""Four-source auditor: seed/update the catalog and report discrepancies."""
from __future__ import annotations
import glob, os, re, sys
from tools import catalog

def gui_keys(ui_dir) -> dict:
    out = {}
    for path in glob.glob(os.path.join(ui_dir, "*.yaml")):
        txt = open(path, encoding="utf-8", errors="ignore").read().lower()
        for key in re.findall(r"[a-z][a-z0-9]+page_[a-z0-9_]+", txt):
            for tok in re.findall(r"[a-z0-9]+", key):
                out.setdefault(tok, key)
    return out

# Plausible ranges for INTERPRETED (scaled) sample values, keyed by catalog 'kind'.
# Only kinds with a well-defined band are listed; overloaded kinds like 'level'
# (0-15 SoC vs 0-255 counters) and unscaled 'current' are intentionally excluded to
# avoid false positives. Samples passed to report_from_keys must be scaled values.
_RANGES = {"battery": (8, 16), "leisure_battery": (8, 16),
           "percent": (0, 100), "temperature": (-40, 90)}

def report_from_keys(dictkeys, cat, gui, app, samples, setters=None) -> list:
    catkeys = catalog.keys(cat)
    lines = []
    for k in sorted(dictkeys - catkeys):
        field = k.split(".")[-1]
        hint = " (GUI-SHOWN)" if field.lower() in gui else ""
        lines.append("COVERAGE-GAP %s%s" % (k, hint))
    for k in sorted(catkeys - dictkeys):
        lines.append("STALE %s" % k)
    for fn, kinds in cat.items():
        for kind, fields in (kinds or {}).items():
            for field, e in (fields or {}).items():
                if e.get("decision") == "omit" and field.lower() in gui:
                    lines.append("GUI-SHOWN-BUT-OMITTED %s.%s.%s (%s)" % (fn, kind, field, gui[field.lower()]))
                if e.get("decision") == "surface":
                    a = app.get(field, {}).get("scale")
                    c = str(e.get("scale"))
                    if a and c not in ("UNVERIFIED", a):
                        lines.append("SCALE-MISMATCH %s.%s.%s app=%s cat=%s" % (fn, kind, field, a, c))
                    rng = _RANGES.get(e.get("kind"))
                    val = samples.get(field)
                    if rng is not None and isinstance(val, (int, float)):
                        lo, hi = rng
                        if not (lo <= val <= hi):
                            lines.append("OUT-OF-RANGE %s.%s.%s value=%s kind=%s" % (fn, kind, field, val, e.get("kind")))
    # A function whose app setter is INVERTED or COMBINED must have at least one
    # surfaced field whose scale acknowledges the transform; else a naive interpreter
    # may silently disagree with the app (the camping-lights bug). Review flag.
    for fn, flag in (setters or {}).items():
        kinds = cat.get(fn, {})
        surfaced = [e for k in ("state", "control") for e in (kinds.get(k) or {}).values()
                    if e.get("decision") == "surface"]
        if not surfaced:
            continue
        # INVERTED (setter or readback) is a read risk: a naive bool() disagrees with
        # the app unless a surfaced field's scale marks it inverted.
        if flag.get("inverted") and not any("inverted" in str(e.get("scale", "")) for e in surfaced):
            lines.append("SEMANTIC-REVIEW-NEEDED %s (inverted transform in %s): no surfaced field "
                         "marks it inverted — a naive bool() read likely disagrees with the app"
                         % (fn, flag.get("where")))
        # COMBINED is a control-model note (one control -> many fields); reads are per-field.
        if flag.get("combined"):
            lines.append("COMBINED-CONTROL %s (%s): one control writes multiple fields — reads are "
                         "per-field (usually fine)" % (fn, flag.get("where")))
    return lines

def report(funcs, cat, app, gui, samples, setters=None):
    return report_from_keys(catalog.dictionary_keys(funcs), cat, gui, app, samples, setters)

def seed(funcs, cat, app, gui) -> dict:
    for k in catalog.dictionary_keys(funcs) - catalog.keys(cat):
        fn, kind, field = k.split(".")
        entry = cat.setdefault(fn, {}).setdefault(kind, {})
        if field.lower() in gui:
            entry[field] = {"decision": "surface", "name": field.lower(), "unit": None,
                            "scale": "UNVERIFIED", "kind": "state",
                            "sources": {"app": app.get(field, {}).get("getter"),
                                        "gui": gui[field.lower()], "vwdoc": None},
                            "confidence": "low"}
        else:
            entry[field] = {"decision": "omit", "reason": "TODO: triage (no GUI/getter evidence)",
                            "sources": {"app": app.get(field, {}).get("getter"), "gui": None, "vwdoc": None}}
    return cat

def main(argv=None):
    import yaml
    from calictl import protocol, overrides
    argv = argv or sys.argv[1:]
    root = os.path.join(os.path.dirname(__file__), "..")
    funcs = protocol.load(); overrides.apply(funcs)
    cat = catalog.load_catalog()
    src = os.environ.get("DECOMPILE_SRC", "")
    app = __import__("tools.app_scales", fromlist=["scales"]).scales(src)
    setters = __import__("tools.app_setters", fromlist=["nontrivial"]).nontrivial(src) if src else {}
    gui = gui_keys(os.path.join(root, "ui", "screens"))
    if "--report" in argv:
        print("\n".join(report(funcs, cat, app, gui, {}, setters)) or "clean")
    if "--seed" in argv:
        cat = seed(funcs, cat, app, gui)
        with open(os.path.join(root, "protocol", "signals.yaml"), "w") as fh:
            yaml.safe_dump(cat, fh, sort_keys=True, default_flow_style=False)
        print("seeded %d entries" % len(catalog.keys(cat)))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
