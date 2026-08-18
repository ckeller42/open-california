"""Unit tests for the pure metric functions in tools.analyze_battery (no InfluxDB needed)."""
from tools import analyze_battery as ab


def test_frozen_runs_groups_consecutive_equal_values():
    # value 80 held for 0..30, then 90 for 40..50, then a single 85 at 60
    series = [(0, 80), (10, 80), (30, 80), (40, 90), (50, 90), (60, 85)]
    runs = ab.frozen_runs(series)
    assert [(r[2], r[3]) for r in runs] == [(80, 30), (90, 10), (85, 0)]   # (value, duration_s)


def test_summarize_field_counts_changes_and_longest_freeze():
    # frozen at 12.5 V for an hour, then two quick changes
    series = [(0, 12.5), (1800, 12.5), (3600, 12.5), (3660, 12.6), (3720, 12.4)]
    s = ab.summarize_field(series)
    assert s["n"] == 5
    assert s["changes"] == 2                       # 12.5->12.6, 12.6->12.4
    assert s["longest_frozen_s"] == 3600           # the 1 h freeze
    assert s["min"] == 12.4 and s["max"] == 12.6


def test_summarize_field_empty():
    assert ab.summarize_field([]) == {"n": 0}


def test_polling_gaps_finds_deepsleep_stretches():
    ts = [0, 60, 120, 4000, 4060]                  # a ~65 min gap between 120 and 4000
    gaps = ab.polling_gaps(ts, gap_s=600)          # >10 min
    assert len(gaps) == 1
    assert gaps[0] == (120, 4000, 3880)


def test_age_summary_flags_sentinel_stale():
    # 255 = unit reports its starter data stale; here stale for the first two samples
    age = [(0, 255), (60, 255), (120, 5), (180, 10)]
    a = ab.age_summary(age)
    assert a["stale_frac"] == 0.5                  # 2 of 4 samples at the sentinel
    assert a["longest_stale_s"] == 60              # 0..60
    assert a["max_fresh_age_min"] == 10


def test_verdict_labels_a_frozen_reading_stale_risk():
    frozen = [(i * 3600, 90) for i in range(48)]   # never changes over 2 days
    v = ab.verdict("Leisure SoC % [soc2_pct]", ab.summarize_field(frozen))
    assert "STALE-RISK" in v
    lively = [(i * 60, 80 + (i % 5)) for i in range(200)]   # changes constantly
    assert "TRUSTWORTHY" in ab.verdict("x", ab.summarize_field(lively))
