"""Static clamp extraction + audit for control fields.

Finding (verified by scanning the decompile): the CaliforniaOnTour app performs
**no client-side range checks** on control writes — there is no `Math.min/max`/clamp
near the `.o(Integer.valueOf(...))` field writes. The unit's **firmware** validates and
drops the ATT link with `0x0E` on an out-of-range value (observed live 2026-07-07:
cooler `State=3`, lighting `ProfileNumber=14`). So the statically-available clamp is the
field **bit-width** (`raw_range` in `protocol/dictionary.yaml`); any narrower **semantic**
range must be curated from on-device observation / an app enum in
`calictl/overrides.py::CONTROL_RANGES` and is enforced by `protocol.encode`
(`check_value`).

This tool reports, per placed control field: the width bound, whether a curated semantic
constraint exists, and flags any curated constraint inconsistent with the width bound
(a curated value that can't fit the field — a catalog error). stdlib-only.
"""
from __future__ import annotations


def _allowed_values(valid):
    if isinstance(valid, (set, frozenset)):
        return sorted(valid)
    lo, hi = valid
    return range(lo, hi + 1)


def _fmt(valid):
    if isinstance(valid, (set, frozenset)):
        return "{%s}" % ",".join(str(v) for v in sorted(valid))
    return "%d..%d" % valid


def audit(funcs) -> list[str]:
    """Return report lines. RANGE-INCONSISTENT is a hard error (curated value exceeds
    the field width); the rest is a coverage ledger."""
    lines = []
    for name in sorted(funcs):
        f = funcs[name]
        for cf in f.control_fields:
            if not cf.placed:
                continue
            hi = (1 << cf.width) - 1
            if cf.valid is None:
                lines.append("WIDTH-ONLY   %s.%s  0..%d  (semantic range UNVERIFIED — firmware-validated)"
                             % (name, cf.name, hi))
                continue
            bad = [v for v in _allowed_values(cf.valid) if not (0 <= v <= hi)]
            if bad:
                lines.append("RANGE-INCONSISTENT %s.%s curated %s exceeds field width 0..%d (values %s)"
                             % (name, cf.name, _fmt(cf.valid), hi, bad))
            else:
                lines.append("CURATED      %s.%s  width 0..%d  allowed %s"
                             % (name, cf.name, hi, _fmt(cf.valid)))
    return lines


def inconsistencies(funcs) -> list[str]:
    return [ln for ln in audit(funcs) if ln.startswith("RANGE-INCONSISTENT")]


def main(argv=None):
    from calictl import protocol, overrides
    funcs = protocol.load(); overrides.apply(funcs)
    lines = audit(funcs)
    print("\n".join(lines))
    bad = [ln for ln in lines if ln.startswith("RANGE-INCONSISTENT")]
    print("\n%d control fields, %d curated, %d width-only, %d inconsistent" % (
        len([ln for ln in lines if not ln.startswith("RANGE-INCONSISTENT")]),
        len([ln for ln in lines if ln.startswith("CURATED")]),
        len([ln for ln in lines if ln.startswith("WIDTH-ONLY")]),
        len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
