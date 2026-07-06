def test_report_flags_coverage_and_gui():
    from tools import audit_signals as A
    class F:  # minimal fake funcs
        pass
    # simulate: dictionary has energy.state.NewField, catalog doesn't
    cat = {"energy": {"state": {"Known": {"decision": "omit", "reason": "x"}}}}
    dictkeys = {"energy.state.Known", "energy.state.NewField"}
    lines = A.report_from_keys(dictkeys, cat, gui={"newfield": "infoPage_x"}, app={}, samples={})
    assert any("COVERAGE-GAP energy.state.NewField" in l for l in lines)
    assert any("GUI-SHOWN-BUT-OMITTED" in l or "COVERAGE-GAP" in l for l in lines)

def test_report_flags_out_of_range():
    from tools import audit_signals as A
    cat = {"energy": {"state": {"UTwoBattBemAfs": {"decision": "surface", "name": "batt2_v", "kind": "leisure_battery"}}}}
    dictkeys = {"energy.state.UTwoBattBemAfs"}
    lines = A.report_from_keys(dictkeys, cat, gui={}, app={},
                               samples={"UTwoBattBemAfs": 48.0})   # 48V is impossible for a 12V batt
    assert any(l.startswith("OUT-OF-RANGE energy.state.UTwoBattBemAfs") and "value=48" in l for l in lines)
    # in-range value produces no OUT-OF-RANGE line
    ok = A.report_from_keys(dictkeys, cat, gui={}, app={}, samples={"UTwoBattBemAfs": 13.5})
    assert not any(l.startswith("OUT-OF-RANGE") for l in ok)


def test_report_flags_and_clears_semantic_review():
    from tools import audit_signals as A
    setters = {"campingmode": {"inverted": True, "combined": True, "where": "tf/a.java"}}
    dk = {"campingmode.state.InteriorLight"}
    # naive plain surface of an inverted/combined field -> flagged
    bad = {"campingmode": {"state": {"InteriorLight": {"decision": "surface", "name": "interior_light", "scale": "raw"}}}}
    assert any(l.startswith("SEMANTIC-REVIEW-NEEDED campingmode")
               for l in A.report_from_keys(dk, bad, gui={}, app={}, samples={}, setters=setters))
    # once a surfaced field marks the transform -> cleared
    ok = {"campingmode": {"state": {"InteriorLight": {"decision": "surface", "name": "lights_on", "scale": "inverted-combined"}}}}
    assert not any(l.startswith("SEMANTIC-REVIEW-NEEDED")
                   for l in A.report_from_keys(dk, ok, gui={}, app={}, samples={}, setters=setters))
