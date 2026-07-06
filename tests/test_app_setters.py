def test_nontrivial_detects_inverted_combined(tmp_path):
    d = tmp_path / "tf"; d.mkdir()
    (d / "a.java").write_text(
        'void e(){ String s="<-- Incoming Data for Campingmode:  State: "; }\n'
        'Object K0(boolean z11){ int i = !z11 ? 1 : 0;'
        ' g0.o(Integer.valueOf(i)); h0.o(Integer.valueOf(i)); return null; }\n')
    from tools import app_setters
    nt = app_setters.nontrivial(str(tmp_path))
    assert nt["campingmode"]["inverted"] is True and nt["campingmode"]["combined"] is True

def test_nontrivial_ignores_normal_setter(tmp_path):
    d = tmp_path / "xx"; d.mkdir()
    (d / "b.java").write_text(
        'void e(){ String s="<-- Incoming Data for Cooler:  State: "; }\n'
        'Object z2(boolean z11){ e0.o(Integer.valueOf(z11 ? 1 : 0)); return null; }\n')
    from tools import app_setters
    assert "cooler" not in app_setters.nontrivial(str(tmp_path))   # single, non-inverted write
