"""Unit tests for the auto-camper decision logic (pure, no BLE/serve)."""
from calictl import automation as A


def _d(**kw):
    base = dict(ignition_on=True, prev_ignition=True, camping_on=False, soc=80, warning=False,
                now=1000.0, armed_until=None, fails=0)
    base.update(kw)
    return A.auto_camper_decide(**base)


def test_engine_start_arms_and_reasserts_when_camping_off():
    # ignition rising edge, camper mode off, battery healthy -> arm + re-assert
    r = _d(ignition_on=True, prev_ignition=False, camping_on=False)
    assert r["actuate"] is True
    assert r["armed_until"] == 1000.0 + A.AUTO_CAMPER_WINDOW_S
    assert r["fails"] == 1
    assert r["notice"] is None


def test_manual_off_with_no_ignition_edge_is_ignored():
    # ignition steady on, camper mode off, NOT armed -> do nothing (respects a manual off)
    r = _d(ignition_on=True, prev_ignition=True, camping_on=False, armed_until=None)
    assert r["actuate"] is False and r["notice"] is None and r["armed_until"] is None


def test_confirmed_on_disarms():
    r = _d(camping_on=True, armed_until=2000.0, fails=2)
    assert r["actuate"] is False and r["armed_until"] is None and r["fails"] == 0


def test_low_soc_stands_down_and_does_not_fight():
    r = _d(camping_on=False, soc=12, armed_until=2000.0)
    assert r["actuate"] is False and r["stood_down"] is True
    assert r["armed_until"] is None and "battery low" in r["notice"]


def test_battery_warning_stands_down_even_if_soc_ok():
    r = _d(camping_on=False, soc=90, warning=True, armed_until=2000.0)
    assert r["actuate"] is False and r["stood_down"] is True and "battery low" in r["notice"]


def test_repeated_drop_gives_up_after_max_fails():
    r = _d(camping_on=False, soc=80, fails=A.AUTO_CAMPER_MAX_FAILS, armed_until=2000.0)
    assert r["actuate"] is False and r["stood_down"] is True
    assert "keeps dropping" in r["notice"] and r["armed_until"] is None


def test_window_expiry_gives_up():
    r = _d(camping_on=False, soc=80, now=3000.0, armed_until=2500.0)  # now past deadline
    assert r["actuate"] is False and r["stood_down"] is True and "did not stay on" in r["notice"]


def test_no_loop_full_cycle():
    # simulate: engine starts, unit keeps dropping camper mode on a healthy battery ->
    # exactly MAX_FAILS re-asserts, then a single give-up, then quiet.
    now, armed, fails, acts, notices = 0.0, None, 0, 0, 0
    prev_ign = False
    for i in range(10):
        ign = True                              # engine on the whole time
        r = A.auto_camper_decide(ignition_on=ign, prev_ignition=prev_ign, camping_on=False,
                                 soc=80, warning=False, now=now, armed_until=armed, fails=fails)
        armed, fails, prev_ign = r["armed_until"], r["fails"], r["prev_ignition"]
        acts += 1 if r["actuate"] else 0
        notices += 1 if r["notice"] else 0
        now += 30.0
    assert acts == A.AUTO_CAMPER_MAX_FAILS      # bounded — no endless loop
    assert notices == 1                         # one give-up, then silence
