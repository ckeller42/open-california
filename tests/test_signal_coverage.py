import pytest, sys, os
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
    from calictl import protocol, overrides
    from tools import catalog
    f = protocol.load(); overrides.apply(f)
    dk = catalog.dictionary_keys(f)
    assert "energy.state.ITwoBattBemAfs" in dk
    assert "cooler.control.State" in dk
    emit = catalog.emitted_state_names("energy", f)
    assert "batt2_current" in emit and "batt2_v" in emit
