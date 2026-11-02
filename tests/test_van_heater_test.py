"""The at-the-van heater runner: start counting, the sampled fields, and the write gate (no daemon)."""

from tools import van_heater_test as v


def test_count_start_only_on_a_rising_edge():
    assert v.count_start({"normal": False, "permanent": False}, {"normal": True, "permanent": False})
    assert v.count_start({"normal": False, "permanent": False}, {"normal": False, "permanent": True})
    assert not v.count_start({"normal": True}, {"normal": True})
    assert not v.count_start({}, {"normal": True})  # unknown before: not counted as a start
    assert not v.count_start(None, {"normal": True})


def test_sample_of_reads_the_heater_battery_and_ignition_fields():
    st = {
        "airheater": {"running": True, "normal": True, "level": 5, "running_time_remaining": 12},
        "energy": {"batt2_current": -9.1, "batt2_v": 12.9},
        "vehicle": {"ignition_on": False},
        "_meta": {"age_s": 3},
    }
    s = v.sample_of(st)
    assert (s["running"], s["level"], s["remaining"], s["batt2_current"], s["age_s"]) == (
        True,
        5,
        12,
        -9.1,
        3,
    )


def test_lone_runtime_write_needs_a_running_heater_and_a_typed_yes(monkeypatch):
    sent = []

    class Dae:
        def post(self, path, body):
            sent.append(body)
            return {"ok": True}

    class Rec:
        last = {"running": False}

        def write(self, rec):
            pass

    assert v.lone_runtime_write(Dae(), Rec(), 30, "w") == {"skipped": "not running"}
    Rec.last = {"running": True, "remaining": 20}
    monkeypatch.setattr(v, "ask", lambda _p: "yes")  # anything but the exact YES refuses
    assert v.lone_runtime_write(Dae(), Rec(), 30, "w") == {"skipped": "no consent"}
    assert sent == []
