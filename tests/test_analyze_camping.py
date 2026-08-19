"""Unit tests for the camping-vs-engine-start analysis (pure, no InfluxDB)."""
from tools import analyze_camping as A


def test_engine_starts_detects_both_signals():
    # ignition rises at t=100; dcdc_charging rises at t=200 -> two engine-start edges.
    named = {
        "ignition_on":   [(90, 0), (100, 1), (150, 0)],
        "dcdc_charging": [(90, 0), (200, 1)],
    }
    edges = A.engine_starts(named)
    assert edges == [(100, "ignition_on"), (200, "dcdc_charging")]


def test_transitions_ignores_first_sample_and_unchanged():
    named = {"master_on": [(10, 1), (20, 1), (30, 0), (40, 1)]}
    tr = A.transitions(named, ("master_on",))
    # first sample (10,1) establishes baseline (no transition); then 1->0 at 30, 0->1 at 40
    assert tr == [(30, "master_on", 1, 0), (40, "master_on", 0, 1)]


def test_flip_flop_flagged_when_master_toggles_twice_after_edge():
    # engine start at 100; camping sheds (1->0) at 110, comes back (0->1) at 160, sheds again 210.
    named = {
        "ignition_on": [(90, 0), (100, 1)],
        "master_on":   [(50, 1), (110, 0), (160, 1), (210, 0)],
    }
    edge = A.engine_starts(named)[0][0]
    assert A.flip_flops(named, edge, window_s=300) == 3     # three master transitions after the edge
    assert A.flip_flops(named, edge, window_s=5) == 0        # none within a tiny window


def test_clean_shed_is_not_a_flip_flop():
    # engine start at 100; camping sheds once (1->0) at 105 and stays off -> exactly one transition.
    named = {
        "ignition_on": [(90, 0), (100, 1), (400, 0)],
        "master_on":   [(50, 1), (105, 0)],
    }
    edge = A.engine_starts(named)[0][0]
    assert A.flip_flops(named, edge, window_s=300) == 1      # one = clean, not a flip-flop (>=2)


def test_value_at_forward_fills():
    s = [(10, 70.0), (40, 100.0)]
    assert A.value_at(s, 5) is None       # before the first sample
    assert A.value_at(s, 10) == 70.0
    assert A.value_at(s, 39) == 70.0      # holds until the next sample
    assert A.value_at(s, 40) == 100.0
    assert A.value_at(s, 999) == 100.0


def test_no_engine_start_when_signal_never_rises():
    named = {"ignition_on": [(10, 0), (20, 0)], "dcdc_charging": [(10, 0)]}
    assert A.engine_starts(named) == []
