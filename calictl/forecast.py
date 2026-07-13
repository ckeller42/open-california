"""Water-duration forecast — a pure, stdlib-only estimator over a level time series.

The web UI answers "how long will the fresh water last?" from the InfluxDB history of the
fresh-water level. The *query* lives in :mod:`calictl.influx` (lazy `influxdb_client`); this
module is the pure computation, so it is trivially unit-testable with synthetic series and stays
importable in the bleak/influx-less test env (hard rule: runtime is stdlib-only at import).

The van is used intermittently and the tank is refilled in one go, so a naive first-to-last
slope is wrong (a refill mid-window would read as "not draining"). We instead anchor at the most
recent refill — the latest time the level was at its window maximum — and measure the drain since
*that* point, which is the current fill's real consumption.
"""
from __future__ import annotations

_DAY_S = 86400.0


def days_left(series, *, min_span_s: float = 6 * 3600, min_drop_l: float = 0.5):
    """Estimate days until the tank empties at the recent drain rate.

    :param series: iterable of ``(epoch_seconds, liters)`` samples, any order; ``None``
        values are dropped.
    :param min_span_s: require at least this much time since the last refill before
        estimating (too short a window = noise, not a trend).
    :param min_drop_l: require at least this much net drain since the last refill; below
        it the tank is effectively flat (parked / just refilled) and we report no estimate.
    :returns: ``(days_left, drain_l_per_day)`` rounded to 1 dp, or ``(None, None)`` when
        there isn't enough of a declining trend to say.

    .. req:: Forecast remaining water days from the level history
       :id: R_WATER_FORECAST
       :status: implemented
       :tags: water, forecast, ui

       Given the fresh-water level time series, ``calictl`` shall estimate the days
       remaining from the drain rate since the most recent refill, and report no
       estimate when the tank is flat/refilling or the window is too short.
    """
    pts = sorted((t, l) for t, l in series if t is not None and l is not None)
    if len(pts) < 2:
        return (None, None)
    # Anchor at the most recent refill: the LATEST time the level was at its window maximum.
    peak_l = max(l for _, l in pts)
    peak_t = max(t for t, l in pts if l == peak_l)
    seg = [(t, l) for t, l in pts if t >= peak_t]
    if len(seg) < 2:
        return (None, None)
    (t0, l0), (t1, l1) = seg[0], seg[-1]
    span, drop = t1 - t0, l0 - l1
    if span < min_span_s or drop < min_drop_l:
        return (None, None)
    rate_lpd = drop / (span / _DAY_S)
    if rate_lpd <= 0:
        return (None, None)
    return (round(l1 / rate_lpd, 1), round(rate_lpd, 1))
