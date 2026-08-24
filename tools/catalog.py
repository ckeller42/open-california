"""Load + validate the signal catalog (protocol/signals.yaml)."""
from __future__ import annotations

import os

import yaml

_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "protocol", "signals.yaml")

class SchemaError(Exception): pass

def load_catalog(path=_DEFAULT) -> dict:
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    for fn, kinds in data.items():
        for kind, fields in (kinds or {}).items():
            if kind not in ("state", "control"):
                raise SchemaError("%s: bad kind %r" % (fn, kind))
            for name, e in (fields or {}).items():
                d = e.get("decision")
                if d not in ("surface", "omit"):
                    raise SchemaError("%s.%s.%s: decision must be surface|omit" % (fn, kind, name))
                if d == "surface" and not e.get("name"):
                    raise SchemaError("%s.%s.%s: surface requires name" % (fn, kind, name))
                if d == "omit" and not e.get("reason"):
                    raise SchemaError("%s.%s.%s: omit requires reason" % (fn, kind, name))
    return data

def keys(cat: dict) -> set:
    return {"%s.%s.%s" % (fn, k, f)
            for fn, kinds in cat.items() for k, fields in (kinds or {}).items()
            for f in (fields or {})}

def dictionary_keys(funcs) -> set:
    out = set()
    for fn, f in funcs.items():
        for sf in f.state_fields:
            out.add("%s.state.%s" % (fn, sf.name))
        for cf in f.control_fields:
            out.add("%s.control.%s" % (fn, cf.name))
    return out

def emitted_state_names(fn, funcs) -> set:
    from calictl import mqtt, semantics
    func = funcs[fn]
    zero = {sf.name: 0 for sf in func.state_fields if sf.placed}
    return set(mqtt.flatten(semantics.interpret(fn, zero)))
