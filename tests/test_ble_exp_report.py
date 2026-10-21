"""tools/esplab/ble_exp_report.py: one row per BLE-experiment phase from normalized log events."""

from tools.esplab.ble_exp_report import normalize, phases

H = 3_600_000


def test_phases_rows():
    """
    .. test:: report rows
       :id: T_BLE_EXP_REPORT
       :links: R_BLE_EXP_REPORT
    """
    ev = [
        (-1, "unit_adv", {"seen": True}),
        (0, "ble_mode", {"mode": "quiet", "why": "exp"}),
        (0, "volt", 13.20),
        (H, "volt", 13.15),
        (10 * 60_000, "unit_adv", {"seen": False}),
        (40 * 60_000, "unit_adv", {"seen": True}),
        # live order: the probe's read-all logs its energy ~1.5 s BEFORE the probe evt
        (2 * H - 5_000, "energy", {"batt2_current": -0.4, "soc2_pct": 40, "batt2_v": 13.1}),
        (2 * H - 4_000, "probe", {"ok": True, "mode": "quiet"}),
        (2 * H - 3_000, "cooler", {"power": False}),
        (2 * H, "ble_phase", {"mode": "quiet", "aborted": False}),
    ]
    (row,) = phases(ev)
    assert row["mode"] == "quiet" and row["aborted"] is False
    assert row["minutes"] == 120
    assert row["adv_minutes"] == 90  # seen 0-10 and 40-120
    assert row["batt2_current"] == -0.4 and row["soc2_pct"] == 40
    assert round(row["mv_per_h"]) == -50
    assert row["cooler_on"] is False


def test_unit_adv_unknown_before_the_first_record_is_not_seen():
    """No ``unit_adv`` before the phase: the advert state is unknown (the experiment's start) and those
    minutes are not counted as seen; the first ``seen:true`` starts the count.

    .. test:: report: unknown advert state at the start counts as not seen
       :id: T_BLE_EXP_REPORT_ADV_UNKNOWN
       :links: R_BLE_EXP_REPORT
    """
    ev = [
        (0, "ble_mode", {"mode": "off", "why": "exp"}),
        (30 * 60_000, "unit_adv", {"seen": True}),
        (H, "ble_phase", {"mode": "off", "aborted": False}),
    ]
    (row,) = phases(ev)
    assert row["adv_minutes"] == 30


def test_unit_adv_resets_to_unknown_at_each_experiment_start():
    """A ``ble_exp`` on record resets the advert state to unknown: the previous run's ``seen:true``
    does not carry over.

    .. test:: report: an experiment start resets the advert state
       :id: T_BLE_EXP_REPORT_ADV_RESET
       :links: R_BLE_EXP_REPORT
    """
    ev = [
        (-2 * H, "unit_adv", {"seen": True}),
        (-1, "ble_exp", {"on": True, "phase_min": 60}),
        (0, "ble_mode", {"mode": "off", "why": "exp"}),
        (20 * 60_000, "ble_exp", {"on": True, "phase_min": 60}),  # a restart mid-phase
        (H, "ble_phase", {"mode": "off", "aborted": True}),
    ]
    (row,) = phases(ev)
    assert row["adv_minutes"] == 0
    ev[3] = (20 * 60_000, "unit_adv", {"seen": True})
    (row,) = phases(ev)
    assert row["adv_minutes"] == 40


def test_failed_probe_and_sparse_volt():
    ev = [
        (0, "ble_mode", {"mode": "off", "why": "exp"}),
        (H, "volt", 13.0),
        (H, "energy", {"batt2_current": 1.0}),
        (H, "probe", {"ok": False, "reason": "no_adv"}),
        (H, "ble_phase", {"mode": "off", "aborted": True}),
    ]
    (row,) = phases(ev)
    assert row["probe"] == "fail" and row["batt2_current"] is None
    assert row["mv_per_h"] is None and row["aborted"] is True


class _Replay:
    def process(self, rec, ts):
        return {"cooler": {"on": True}, "energy": {"batt2_current": 1.0}} if rec.get("ev") == "notify" else {}


def test_normalize():
    done = [
        ({"ev": "evt", "kind": "ble_phase", "mode": "quiet", "aborted": False}, 5),
        ({"ev": "ext", "type": "victron_mppt", "dev": "mppt", "battery_voltage": 14.0}, 6),
        ({"ev": "ext", "type": "victron_battery", "dev": "bat", "battery_voltage": 13.2}, 7),
        ({"ev": "ext", "type": "victron_battery", "dev": "other", "battery_voltage": 12.0}, 8),
        ({"ev": "notify", "fn": "cooler"}, 9),
        ({"ev": "evt", "kind": "ble_phase", "mode": "x"}, None),
    ]
    out = normalize(done, _Replay())
    assert [(t, k) for t, k, _ in out] == [(5, "ble_phase"), (7, "volt"), (9, "cooler"), (9, "energy")]
    assert out[1][2] == 13.2 and out[2][2] == {"power": True}
    # explicit alias overrides the type pick
    assert [d for _, k, d in normalize(done, _Replay(), "other") if k == "volt"] == [12.0]


def test_hold_phase_span_comes_from_dur_ms():
    """A hold phase logs no ``ble_mode`` at its start (the mode already is hold): its span is the
    firmware's ``dur_ms``, not the log's first record.

    .. test:: report: the phase span from ble_phase dur_ms
       :id: T_BLE_EXP_REPORT_DUR
       :links: R_BLE_EXP_REPORT
    """
    ev = [
        (0, "volt", 13.0),  # an earlier record: the old fallback start
        (H, "probe", {"ok": True, "mode": "hold"}),
        (2 * H, "ble_phase", {"mode": "hold", "aborted": False, "dur_ms": 30 * 60_000}),
    ]
    (row,) = phases(ev)
    assert row["minutes"] == 30
