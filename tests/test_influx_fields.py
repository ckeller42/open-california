"""Tests for calictl.influx's pure field-mapping rules and the Influx read/write
degrade paths (numeric_fields, points_for, field_series).

These are the Grafana-facing value rules — a wrong mapping silently corrupts the
buspi dashboards, so pin them here without a live InfluxDB.
"""

import sys
import types
from types import SimpleNamespace

import pytest

from calictl import influx

# --------------------------------------------------------------------- numeric_fields


def test_numeric_fields_maps_scalars_and_drops_text():
    out = influx.numeric_fields(
        {"state": True, "fan": False, "temp_c": 21.5, "fan_speed": 2, "note": "ok", "missing": None}
    )
    assert out == {"state": 1.0, "fan": 0.0, "temp_c": 21.5, "fan_speed": 2.0}


def test_numeric_fields_lists_become_count_fields():
    out = influx.numeric_fields({"zones": [1, 2, 3], "empty": []})
    assert out == {"zones_count": 3.0, "empty_count": 0.0}


@pytest.mark.parametrize(
    ("interp", "field", "code"),
    [
        ({"fault": "error"}, "fault_code", 1.0),
        ({"fault": "emergency"}, "fault_code", 2.0),
        ({"fault": "door_open"}, "fault_code", 3.0),
        # unknown/absent enum value -> 0 (ok) so the series stays continuous
        ({"fault": "bogus"}, "fault_code", 0.0),
        ({"fault": None}, "fault_code", 0.0),
        ({"alert": "low_battery"}, "alert_code", 6.0),
        ({"alert": "driving"}, "alert_code", 7.0),
        # nested water alerts flatten to <group>_<alert> before the code lookup
        ({"fresh": {"alert": "pump_error"}}, "fresh_alert_code", 4.0),
        ({"waste": {"alert": "full"}}, "waste_alert_code", 1.0),
        ({"waste": {"alert": "pump_protection"}}, "waste_alert_code", 0.0),  # not a waste code
    ],
)
def test_enum_codes_follow_curation_table(interp, field, code):
    assert influx.numeric_fields(interp)[field] == code


def test_enum_fields_still_emit_numeric_sibling_values():
    out = influx.numeric_fields({"fresh": {"alert": "empty", "percent": 12.5}})
    assert out == {"fresh_alert_code": 5.0, "fresh_percent": 12.5}


# ---------------------------------------------------------------- points_for / build_points


class _FakePoint:
    """Just enough of influxdb_client.Point to assert structure."""

    def __init__(self, measurement):
        self.measurement = measurement
        self.tags = {}
        self.fields = {}

    def tag(self, k, v):
        self.tags[k] = v
        return self

    def field(self, k, v):
        self.fields[k] = v
        return self


@pytest.fixture
def fake_influx(monkeypatch):
    mod = types.ModuleType("influxdb_client")
    mod.Point = _FakePoint
    monkeypatch.setitem(sys.modules, "influxdb_client", mod)
    return mod


def test_points_for_one_point_per_nonempty_function(fake_influx):
    pts = influx.points_for(
        {
            "cooler": {"state": True, "temp_c": 5.0},
            "water": {"fresh": {"percent": 80.0}},
            "stairs": {"mode": None},  # nothing numeric -> no point at all
        }
    )
    assert len(pts) == 2
    by_fn = {p.tags["function"]: p for p in pts}
    assert set(by_fn) == {"cooler", "water"}
    for p in pts:
        assert p.measurement == "camper"
        assert p.tags["vehicle"] == "vwcamper"
    assert by_fn["cooler"].fields == {"state": 1.0, "temp_c": 5.0}
    assert by_fn["water"].fields == {"fresh_percent": 80.0}


def test_build_points_shares_points_for(fake_influx):
    states = {"cooler": {"state": True}}
    b, p = influx.build_points(states), influx.points_for(states)
    assert [(x.measurement, x.tags, x.fields) for x in b] == [(x.measurement, x.tags, x.fields) for x in p]


# --------------------------------------------------------------------- field_series


class _Rec:
    def __init__(self, value, seconds):
        import datetime

        self._v = value
        self._t = datetime.datetime.fromtimestamp(seconds, tz=datetime.UTC)

    def get_value(self):
        return self._v

    def get_time(self):
        return self._t


def test_field_series_no_token_returns_empty(monkeypatch):
    monkeypatch.delenv("INFLUXDB_TOKEN", raising=False)
    assert influx.field_series("temp_c", "cooler") == []


def test_field_series_rejects_unknown_aggregate_fn(monkeypatch):
    monkeypatch.setenv("INFLUXDB_TOKEN", "t")
    with pytest.raises(ValueError, match="unsupported aggregate fn"):
        influx.field_series("temp_c", "cooler", fn="median")


def test_field_series_client_failure_degrades_to_empty(monkeypatch):
    monkeypatch.setenv("INFLUXDB_TOKEN", "t")
    mod = types.ModuleType("influxdb_client")

    class _Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("no network")

    mod.InfluxDBClient = _Boom
    monkeypatch.setitem(sys.modules, "influxdb_client", mod)
    assert influx.field_series("temp_c", "cooler") == []


def test_field_series_maps_records_ascending(monkeypatch):
    monkeypatch.setenv("INFLUXDB_TOKEN", "t")
    mod = types.ModuleType("influxdb_client")
    # out of order on purpose: the helper must sort ascending by time
    table = SimpleNamespace(records=[_Rec(5.5, 40), _Rec(None, 30), _Rec(1.0, 10)])

    class _QueryApi:
        def query(self, flux, org=None):
            assert "temp_c" in flux and "cooler" in flux and "last" in flux
            return [table]

    class _Client:
        def __init__(self, url, token, org):
            assert token == "t"

        def __enter__(self, *_):
            return self

        def __exit__(self, *exc):
            return False

        def query_api(self):
            return _QueryApi()

    mod.InfluxDBClient = _Client
    monkeypatch.setitem(sys.modules, "influxdb_client", mod)
    out = influx.field_series("temp_c", "cooler", fn="last")
    assert out == [(10, 1.0), (40, 5.5)]  # None value dropped, time-sorted
