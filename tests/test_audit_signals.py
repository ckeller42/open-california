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
