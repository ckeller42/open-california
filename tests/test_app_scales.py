def test_scale_extracted(tmp_path):
    src = tmp_path / "xf" ; src.mkdir()
    (src / "a.java").write_text(
        'float getUTwoBattBemAfs(){ return this.UTwoBattBemAfs * 0.1f; }')
    from tools import app_scales
    s = app_scales.scales(str(tmp_path))
    assert s["UTwoBattBemAfs"]["scale"] == "0.1"
