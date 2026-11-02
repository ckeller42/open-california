"""The mock unit's cabin thermal model (tools/mock_unit.py ``MockCamperUnit.cabin``): a first-order lag
towards the outside temperature plus a heater term while the heater runs. Test tooling for the thermostat's
closed loop, not a tuning tool."""

from tools.mock_unit import MockCamperUnit


def _unit(t_in, t_out, level=0, heat=False, perm=False):
    u = MockCamperUnit()
    u.cabin.update(t_in=t_in, t_out=t_out)
    u.state["airheater"].update(
        HeatingLevel=level, NormalOperation=int(heat), PermanentOperation=int(perm), RunningTimeinAction=999
    )
    return u


def test_cabin_defaults():
    assert MockCamperUnit().cabin == {"t_in": 2000, "t_out": 0, "tau_s": 3600, "gain_per_level": 300}


def test_cabin_cools_without_heat():
    u = _unit(2000, 0)
    for _ in range(3600):
        u.tick(1.0)
    # one tau: 1/e of the way back down (discrete steps, so close to 2000/e = 736)
    assert 700 < u.cabin["t_in"] < 780


def test_cabin_warms_with_level():
    u = _unit(2000, 0, level=5, heat=True)
    for _ in range(36000):  # ten tau: the equilibrium t_out + gain * level
        u.tick(1.0)
    assert abs(u.cabin["t_in"] - 1500) < 5


def test_permanent_operation_heats_too_but_an_idle_heater_does_not():
    on = _unit(1000, 1000, level=4, perm=True)
    off = _unit(1000, 1000, level=4)
    for _ in range(600):
        on.tick(1.0)
        off.tick(1.0)
    assert on.cabin["t_in"] > 1000 and off.cabin["t_in"] == 1000
