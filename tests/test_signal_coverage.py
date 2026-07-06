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
