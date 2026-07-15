"""The energy history ring buffer: bounded, corruption-tolerant, and Influx-free."""
from calictl import history


def _p(tmp_path):
    return str(tmp_path / "sub" / "history.jsonl")      # 'sub' also proves parents are created


def test_append_load_round_trip(tmp_path):
    """.. test:: Energy history round-trip
       :id: T_ENERGY_HISTORY
       :links: R_ENERGY_HISTORY

       A sample survives append->load with its timestamp, volts and amps intact.
    """
    p = _p(tmp_path)
    assert history.append(p, 1000.0, 13.4, 3.4) is True
    assert history.append(p, 1030.0, 13.3, -2.0) is True
    assert history.load(p) == [[1000.0, 13.4, 3.4], [1030.0, 13.3, -2.0]]


def test_missing_file_reads_empty(tmp_path):
    """No history yet is 'no data', not a crash."""
    assert history.load(str(tmp_path / "nope.jsonl")) == []


def test_half_read_sample_is_not_recorded(tmp_path):
    """A None volts/amps must never be written -- the chart would render a fake 0."""
    p = _p(tmp_path)
    assert history.append(p, 1000.0, None, 3.4) is False
    assert history.append(p, 1000.0, 13.4, None) is False
    assert history.load(p) == []


def test_corrupt_line_loses_one_sample_not_the_history(tmp_path):
    """A torn line (power cut mid-append) must not blank the whole chart."""
    p = _p(tmp_path)
    history.append(p, 1000.0, 13.4, 3.4)
    with open(p, "a") as f:
        f.write("{not json\n")            # torn write
        f.write('["nan", 1, 2]\n')        # well-formed JSON, bogus ts
        f.write("[1, 2]\n")               # wrong arity
    history.append(p, 1060.0, 13.2, 1.0)
    assert history.load(p) == [[1000.0, 13.4, 3.4], [1060.0, 13.2, 1.0]]


def test_since_filter(tmp_path):
    p = _p(tmp_path)
    for ts in (1000.0, 2000.0, 3000.0):
        history.append(p, ts, 13.0, 1.0)
    assert [r[0] for r in history.load(p, since=2000.0)] == [2000.0, 3000.0]


def test_trim_drops_old_keeps_recent(tmp_path):
    p = _p(tmp_path)
    now = 100_000.0
    history.append(p, now - 50 * 3600, 12.0, 0.0)      # older than the 48 h retention
    history.append(p, now - 10 * 3600, 13.4, 3.4)      # inside it
    assert history.trim(p, now=now) == 1
    assert history.load(p) == [[now - 10 * 3600, 13.4, 3.4]]


def test_trim_leaves_a_missing_file_missing(tmp_path):
    """Trim must not conjure an empty history file just by running."""
    import os
    p = _p(tmp_path)
    assert history.trim(p) == 0
    assert not os.path.exists(p)
