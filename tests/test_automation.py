"""Unit tests for the auto-camper RESTORE-after-park decision logic (pure, no BLE/serve)."""
from calictl import automation as A


def _d(**kw):
    base = dict(ignition_on=False, prev_ignition=False, master_on=False, prev_master=False,
                usb_on=False, lights_on=False, prev_usb=False, prev_lights=False,
                soc=80, warning=False, now=1000.0, owe_restore=False, pre_drive=None,
                restore_until=None, fails=0)
    base.update(kw)
    return A.auto_camper_restore_decide(**base)


def test_engine_shed_arms_restore_and_remembers_pre_drive():
    # ignition rising, camping was on before (+ usb on, lights off) -> owe a restore of that config.
    r = _d(ignition_on=True, prev_ignition=False, prev_master=True, prev_usb=True, prev_lights=False,
           master_on=False)
    assert r["owe_restore"] is True
    assert r["pre_drive"] == {"master": True, "usb": True, "lights": False}
    assert r["actuate"] is False        # can't restore while the engine runs (unit refuses)


def test_camping_off_before_drive_owes_nothing():
    r = _d(ignition_on=True, prev_ignition=False, prev_master=False)   # camping already off pre-drive
    assert r["owe_restore"] is False and r["actuate"] is False


def test_manual_off_while_parked_clears_debt():
    # camping on->off with ignition steady OFF = a manual off, not an engine shed -> drop the debt.
    r = _d(ignition_on=False, prev_ignition=False, prev_master=True, master_on=False,
           owe_restore=True, pre_drive={"master": True, "usb": False, "lights": False})
    assert r["owe_restore"] is False and r["actuate"] is False


def test_park_triggers_restore_write():
    # ignition falling edge with a debt owed -> open the window and write the remembered config.
    cfg = {"master": True, "usb": True, "lights": False}
    r = _d(ignition_on=False, prev_ignition=True, master_on=False, owe_restore=True, pre_drive=cfg)
    assert r["actuate"] is True
    assert r["restore_config"] == cfg
    assert r["restore_until"] == 1000.0 + A.AUTO_CAMPER_WINDOW_S
    assert r["fails"] == 1


def test_ignition_bounce_does_not_reclobber_pre_drive():
    # already owe a restore (remembered usb=True); a second ignition rising edge while the shed has
    # landed (prev_usb now False) must NOT overwrite the remembered pre-drive config (crank bounce).
    pre = {"master": True, "usb": True, "lights": False}
    r = _d(ignition_on=True, prev_ignition=False, prev_master=True, prev_usb=False, prev_lights=False,
           owe_restore=True, pre_drive=pre)
    assert r["pre_drive"] == pre        # the `not owe_restore` arm guard held — usb=True preserved


def test_restore_confirmed_disarms():
    # parked, camping now reads on -> success, debt cleared, no further write.
    r = _d(ignition_on=False, prev_ignition=False, master_on=True, owe_restore=True,
           restore_until=2000.0, pre_drive={"master": True, "usb": True, "lights": False})
    assert r["restored"] is True and r["owe_restore"] is False and r["actuate"] is False


def test_low_battery_stands_down_at_park():
    r = _d(ignition_on=False, prev_ignition=False, master_on=False, soc=12, owe_restore=True,
           restore_until=2000.0, pre_drive={"master": True, "usb": False, "lights": False})
    assert r["actuate"] is False and r["owe_restore"] is False and "battery" in r["notice"]


def test_battery_warning_stands_down_even_if_soc_ok():
    r = _d(ignition_on=False, prev_ignition=False, master_on=False, soc=90, warning=True,
           owe_restore=True, restore_until=2000.0, pre_drive={"master": True})
    assert r["actuate"] is False and r["owe_restore"] is False and "battery" in r["notice"]


def test_gives_up_after_max_fails():
    r = _d(ignition_on=False, prev_ignition=False, master_on=False, fails=A.AUTO_CAMPER_MAX_FAILS,
           owe_restore=True, restore_until=2000.0, pre_drive={"master": True})
    assert r["actuate"] is False and "would not re-enable" in r["notice"] and r["owe_restore"] is False


def test_window_expiry_gives_up():
    r = _d(ignition_on=False, prev_ignition=False, master_on=False, now=3000.0, owe_restore=True,
           restore_until=2500.0, pre_drive={"master": True})
    assert r["actuate"] is False and "within the window" in r["notice"] and r["owe_restore"] is False


def test_no_loop_full_cycle_engine_shed_then_park_then_refused():
    """End-to-end: engine start (arm) -> drive -> park -> unit keeps refusing the restore: exactly
    MAX_FAILS writes, then one give-up, then quiet. Proves it can't loop.

    .. test:: Restore-after-park arms, retries bounded, and never loops
       :id: T_AUTO_CAMPER_RESTORE
       :links: R_AUTO_CAMPER_RESTORE
    """
    st = dict(owe_restore=False, pre_drive=None, restore_until=None, fails=0)
    pign, pm, pusb, plt = False, True, False, False   # camping WAS on before the drive
    now, acts, notices = 0.0, 0, 0
    plan = [(1, 0)] + [(1, 0)] * 2 + [(0, 0)] + [(0, 0)] * 8   # start, drive×2, park, restoring×8
    for ign, master in plan:
        r = A.auto_camper_restore_decide(
            ignition_on=bool(ign), prev_ignition=pign, master_on=bool(master), prev_master=pm,
            usb_on=False, lights_on=False, prev_usb=pusb, prev_lights=plt,
            soc=80, warning=False, now=now, **st)
        st = dict(owe_restore=r["owe_restore"], pre_drive=r["pre_drive"],
                  restore_until=r["restore_until"], fails=r["fails"])
        pign, pm, pusb, plt = r["prev_ignition"], r["prev_master"], r["prev_usb"], r["prev_lights"]
        acts += 1 if r["actuate"] else 0
        notices += 1 if r["notice"] else 0
        now += 10.0
    assert acts == A.AUTO_CAMPER_MAX_FAILS      # bounded — no endless restore loop
    assert notices == 1                         # one give-up, then silence


def test_no_loop_full_cycle_restore_succeeds():
    # same as above but the unit ACCEPTS on the 2nd attempt -> one 'restored', then quiet.
    st = dict(owe_restore=False, pre_drive=None, restore_until=None, fails=0)
    pign, pm, pusb, plt = False, True, False, False
    now, acts, restored = 0.0, 0, 0
    # start, drive, park+restore(off), restore(off), then camping comes on, then steady on
    plan = [(1, 0), (1, 0), (0, 0), (0, 0), (0, 1), (0, 1), (0, 1)]
    for ign, master in plan:
        r = A.auto_camper_restore_decide(
            ignition_on=bool(ign), prev_ignition=pign, master_on=bool(master), prev_master=pm,
            usb_on=False, lights_on=False, prev_usb=pusb, prev_lights=plt,
            soc=80, warning=False, now=now, **st)
        st = dict(owe_restore=r["owe_restore"], pre_drive=r["pre_drive"],
                  restore_until=r["restore_until"], fails=r["fails"])
        pign, pm, pusb, plt = r["prev_ignition"], r["prev_master"], r["prev_usb"], r["prev_lights"]
        acts += 1 if r["actuate"] else 0
        restored += 1 if r["restored"] else 0
        now += 10.0
    assert restored == 1 and st["owe_restore"] is False and acts >= 1


def test_autocamper_step_restores_via_injected_actuate():
    """AutoCamper.step drives the pure decision AND actuates through an INJECTED coroutine (no BLE):
    engine start (arm) -> park -> it calls actuate('campingmode','master','on'). Previously this glue
    was only reachable via the mock daemon; the extraction makes it a plain unit test.

    .. test:: AutoCamper restores after park via the injected actuate coroutine
       :id: T_AUTO_CAMPER_CONTROLLER
       :links: R_AUTO_CAMPER_CONTROLLER
    """
    import asyncio
    ac = A.AutoCamper()
    ac.set_enabled(True)
    calls = []

    async def fake_actuate(function, what, value):
        calls.append((function, what, value))

    def step(states, now):
        asyncio.run(ac.step(states, actuate=fake_actuate, read_only=False, now=now))

    on = {"vehicle": {"ignition_on": False},
          "campingmode": {"master_on": True, "usb_charger": False, "lights_on": False}, "energy": {}}
    driving = {"vehicle": {"ignition_on": True}, "campingmode": {"master_on": False}, "energy": {}}
    parked = {"vehicle": {"ignition_on": False}, "campingmode": {"master_on": False},
              "energy": {"soc2_pct": 80}}
    step(on, 100.0)          # baseline: camping on, stationary
    step(driving, 110.0)     # engine start -> arm the restore
    assert ac.owe_restore is True and ac.pre_drive == {"master": True, "usb": False, "lights": False}
    step(parked, 120.0)      # park -> restore fires the injected actuate
    assert ("campingmode", "master", "on") in calls


def test_autocamper_persistence_round_trip():
    """to_state_dict / load round-trips the restore debt (what serve persists across a restart)."""
    ac = A.AutoCamper()
    ac.owe_restore = True
    ac.pre_drive = {"master": True, "usb": True, "lights": False}
    ac.restore_until = 5.0
    ac.fails = 2
    ac.prev_ignition = True
    ac2 = A.AutoCamper()
    ac2.load(True, ac.to_state_dict())
    assert ac2.enabled is True and ac2.owe_restore is True and ac2.fails == 2
    assert ac2.pre_drive == {"master": True, "usb": True, "lights": False} and ac2.prev_ignition is True
    # corrupt/null fails must not crash (mirrors serve._load_last contract)
    ac3 = A.AutoCamper(); ac3.load(True, {"fails": None, "owe_restore": None})
    assert ac3.fails == 0 and ac3.owe_restore is False


def test_missing_ignition_read_freezes_the_machine():
    """LIVE BUG 2026-08-26: a missed vehicle read (ignition_on=None) was bool()-coerced to False,
    fabricating a falling edge — the journal shows `ignition 1->-` firing an immediate 'restored'.
    Had camping been shed at that moment, the restore would have run WHILE DRIVING (unit refuses,
    burning the give-up budget), and the phantom None->1 re-arm could capture a shed pre_drive or
    silently drop the debt. A None read must FREEZE the machine: no edges, no window countdown,
    prevs unchanged, debt kept."""
    r = _d(ignition_on=None, prev_ignition=True, prev_master=True, master_on=False,
           owe_restore=True, pre_drive={"master": True, "usb": True, "lights": False})
    assert r["actuate"] is False
    assert r["owe_restore"] is True                       # debt kept
    assert r["restore_until"] is None                     # no phantom park window
    assert r["prev_ignition"] is True                     # prev NOT clobbered to False
    assert r["prev_master"] is True
    assert r["notice"] is None and r["restored"] is False


def test_missing_camping_read_does_not_fabricate_manual_cancel():
    """master_on=None (campingmode read missing) + bool() used to look like camping on->off while
    parked = a 'manual cancel' that dropped the restore debt. None must freeze instead."""
    r = _d(ignition_on=False, prev_ignition=False, master_on=None, prev_master=True,
           owe_restore=True, pre_drive={"master": True, "usb": False, "lights": False})
    assert r["owe_restore"] is True                       # debt survives the read gap
    assert r["prev_master"] is True
    assert r["actuate"] is False


def test_restore_window_survives_a_read_gap():
    """Mid-restore (window open), one bad poll must not give up, actuate, or advance anything."""
    r = _d(ignition_on=None, prev_ignition=False, master_on=None, prev_master=False,
           owe_restore=True, pre_drive={"master": True, "usb": True, "lights": False},
           restore_until=1300.0, fails=1, now=1200.0)
    assert r["owe_restore"] is True and r["restore_until"] == 1300.0
    assert r["fails"] == 1 and r["actuate"] is False
