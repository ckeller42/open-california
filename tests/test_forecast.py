"""Water days-left forecast (calictl.forecast.days_left) — pure, no Influx/BLE.

Drives the estimator with synthetic fresh-water series and pins the behaviour the UI relies on:
a steady drain gives a rate + days; a refill mid-window is anchored out (we measure only the
current fill's usage); flat/parked/refilling/short-window cases report NO estimate rather than a
made-up number.
"""
from calictl import forecast

DAY = 86400
_BASE = 1_000_000_000


def _series(points):
    """(day_offset, liters) -> (epoch_seconds, liters)."""
    return [(_BASE + int(d * DAY), l) for d, l in points]


def test_steady_drain_gives_rate_and_days():
    """29 L -> 11 L over 3 days = 6 L/day; 11 L left => ~1.8 days.

    .. test:: forecast estimates days-left from a steady drain
       :id: T_WATER_FORECAST
       :links: R_WATER_FORECAST
       :status: passing
    """
    days, rate = forecast.days_left(_series([(0, 29), (1, 23), (2, 17), (3, 11)]))
    assert rate == 6.0
    assert days == round(11 / 6.0, 1)


def test_refill_midwindow_is_anchored_out():
    # a full window that starts low, refills to 29, then drains: measure only the post-refill decline
    days, rate = forecast.days_left(_series([(0, 5), (1, 2), (1.1, 29), (2, 23), (3, 17), (4, 11)]))
    assert rate is not None and rate > 0 and days is not None and days > 0


def test_flat_parked_gives_no_estimate():
    assert forecast.days_left(_series([(0, 11), (1, 11), (2, 11)])) == (None, None)


def test_refilling_rising_gives_no_estimate():
    # level only rises -> the peak is the last point -> single-point segment -> no estimate
    assert forecast.days_left(_series([(0, 11), (1, 20), (2, 29)])) == (None, None)


def test_short_window_gives_no_estimate():
    assert forecast.days_left([(_BASE, 29), (_BASE + 3600, 28)]) == (None, None)   # 1 h < 6 h min


def test_tiny_drop_gives_no_estimate():
    assert forecast.days_left(_series([(0, 29), (1, 28.6)])) == (None, None)       # 0.4 L < 0.5


def test_insufficient_or_null_series():
    assert forecast.days_left([]) == (None, None)
    assert forecast.days_left([(1, None), (2, None)]) == (None, None)
    assert forecast.days_left([(_BASE, 11)]) == (None, None)
