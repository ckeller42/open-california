"""The daemon's energy-history recording + the /api/history read path (both Influx-free)."""
import time

from calictl import serve, history


def _server(tmp_path, monkeypatch):
    monkeypatch.setenv("CALICTL_STATE_CACHE", str(tmp_path / "last.json"))
    monkeypatch.setenv("CALICTL_HISTORY_CACHE", str(tmp_path / "history.jsonl"))
    return serve.Server(influx_enabled=False)


def test_poll_records_a_leisure_battery_sample(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    s._last_ok_ts = 1000.0
    s._record_history({"batt2_v": 13.4, "batt2_current": 3.4})
    assert history.load(s._history_cache) == [[1000.0, 13.4, 3.4]]


def test_a_poll_without_energy_records_nothing(tmp_path, monkeypatch):
    """A poll that didn't read `energy` must not fabricate a sample."""
    s = _server(tmp_path, monkeypatch)
    s._last_ok_ts = 1000.0
    s._record_history(None)
    assert history.load(s._history_cache) == []


def test_starter_only_reading_records_nothing(tmp_path, monkeypatch):
    """batt2_* is None on a short/partial read -- no sample, rather than a fake 0 V."""
    s = _server(tmp_path, monkeypatch)
    s._last_ok_ts = 1000.0
    s._record_history({"batt1_v": 12.5, "batt2_v": None, "batt2_current": None})
    assert history.load(s._history_cache) == []


def test_history_is_trimmed_periodically_not_every_append(tmp_path, monkeypatch):
    """Trim is amortised: the file grows past retention until a rewrite is due (SD-card wear)."""
    s = _server(tmp_path, monkeypatch)
    monkeypatch.setattr(history, "TRIM_EVERY", 3)
    old = time.time() - 100 * 3600            # far outside the 48 h retention
    for _ in range(2):
        s._last_ok_ts = old
        s._record_history({"batt2_v": 12.0, "batt2_current": 0.0})
    assert len(history.load(s._history_cache)) == 2          # not trimmed yet
    s._last_ok_ts = time.time()
    s._record_history({"batt2_v": 13.4, "batt2_current": 3.4})   # 3rd append -> trim fires
    assert history.load(s._history_cache) == [[round(s._last_ok_ts, 3), 13.4, 3.4]]


def test_backend_history_window_and_gap_hint(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    now = time.time()
    history.append(s._history_cache, now - 30 * 3600, 12.0, 0.0)     # outside a 24 h window
    history.append(s._history_cache, now - 1 * 3600, 13.4, 3.4)      # inside it
    out = serve.ServeBackend(s, loop=None).history(24)
    assert [r[1] for r in out["samples"]] == [13.4]
    assert out["hours"] == 24
    # the line-break threshold must match _meta.online's definition of "we lost the van"
    assert out["gap_s"] == s.interval * 3 + 30


def test_backend_history_clamps_hours(tmp_path, monkeypatch):
    be = serve.ServeBackend(_server(tmp_path, monkeypatch), loop=None)
    assert be.history("999")["hours"] == 48        # retention ceiling
    assert be.history("0")["hours"] == 1
    assert be.history("garbage")["hours"] == 24    # a junk query string is not a 500


def test_history_never_touches_influx(tmp_path, monkeypatch):
    """The GUI is Influx-free BY REQUIREMENT: the chart must render with Influx down."""
    from calictl import influx

    def _boom(*a, **k):
        raise AssertionError("the history path must not call influx")

    monkeypatch.setattr(influx, "field_series", _boom)
    s = _server(tmp_path, monkeypatch)
    s._last_ok_ts = time.time()
    s._record_history({"batt2_v": 13.4, "batt2_current": 3.4})
    assert len(serve.ServeBackend(s, loop=None).history(24)["samples"]) == 1


def test_record_history_survives_an_unwritable_path(tmp_path, monkeypatch):
    """Telemetry must never take the poll loop down."""
    monkeypatch.setenv("CALICTL_STATE_CACHE", str(tmp_path / "last.json"))
    monkeypatch.setenv("CALICTL_HISTORY_CACHE", "/proc/nope/history.jsonl")
    s = serve.Server(influx_enabled=False)
    s._last_ok_ts = 1000.0
    s._record_history({"batt2_v": 13.4, "batt2_current": 3.4})     # must not raise
