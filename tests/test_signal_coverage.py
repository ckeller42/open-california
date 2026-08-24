import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tools import catalog


def test_catalog_schema_surface_needs_name(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("energy:\n  state:\n    Foo: {decision: surface}\n")
    with pytest.raises(catalog.SchemaError):
        catalog.load_catalog(str(p))

def test_catalog_schema_omit_needs_reason(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("energy:\n  state:\n    Foo: {decision: omit}\n")
    with pytest.raises(catalog.SchemaError):
        catalog.load_catalog(str(p))

def test_catalog_loads_valid():
    c = catalog.load_catalog()
    assert "energy" in c and "state" in c["energy"]

def test_dictionary_keys_and_emitted():
    from calictl import overrides, protocol
    from tools import catalog
    f = protocol.load(); overrides.apply(f)
    dk = catalog.dictionary_keys(f)
    assert "energy.state.ITwoBattBemAfs" in dk
    assert "cooler.control.State" in dk
    emit = catalog.emitted_state_names("energy", f)
    assert "batt2_current" in emit and "batt2_v" in emit


# --- Guardrail: the three invariants that prevent a silent drop/mislabel ---
def _guard_funcs():
    from calictl import overrides, protocol
    f = protocol.load(); overrides.apply(f); return f


def test_coverage_every_field_has_a_decision():
    f = _guard_funcs(); cat = catalog.load_catalog()
    dk, ck = catalog.dictionary_keys(f), catalog.keys(cat)
    assert dk - ck == set(), "dictionary fields with no catalog decision: %s" % sorted(dk - ck)
    assert ck - dk == set(), "stale catalog entries: %s" % sorted(ck - dk)


def test_surfaced_state_fields_are_emitted():
    f = _guard_funcs(); cat = catalog.load_catalog()
    missing = []
    for fn, kinds in cat.items():
        for field, e in (kinds.get("state") or {}).items():
            if e.get("decision") == "surface" and e["name"] not in catalog.emitted_state_names(fn, f):
                missing.append("%s.%s->%s" % (fn, field, e["name"]))
    assert not missing, "surfaced but not emitted by semantics: %s" % missing


def test_surfaced_control_fields_are_placed():
    f = _guard_funcs(); cat = catalog.load_catalog()
    bad = []
    for fn, kinds in cat.items():
        placed = {cf.name for cf in f[fn].control_fields if cf.placed}
        for field, e in (kinds.get("control") or {}).items():
            if e.get("decision") == "surface" and field not in placed:
                bad.append("%s.control.%s" % (fn, field))
    assert not bad, "surfaced control fields not placed/resolved: %s" % bad
