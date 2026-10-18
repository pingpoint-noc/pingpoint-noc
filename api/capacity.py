"""Percentiles, utilisation and growth forecasts (pure functions, no I/O).

Everything here works on values the platform already derived: bit/s rates from 64-bit counters
(never a spike after a reset) and hourly samples of the database size. Nothing is invented:
with too little data a function answers with a reason instead of a number.

Percentile method: **nearest-rank** (the rank is ceil(p/100 * n), 1-based, over the sorted values),
so the result is always a rate that was really measured, with no interpolation between samples.
The 95th percentile is used, and not the maximum, because one 15 s burst (a speed test, a backup
starting) sets the maximum while the p95 says what the link carries 95 % of the time: it is the
number that sizes a link (the same idea as 95th-percentile billing). The maximum is still shown.
"""

import math

# The SNMP poller samples every 15 s; a 64-bit rate needs two samples.
POLL_SECONDS = 15
# An interval longer than four polls means samples were lost (collector down, router rebooted):
# its rate is an average over the hole, not a measurement, so it is left out.
MAX_GAP_SECONDS = 4 * POLL_SECONDS
# Fewer valid intervals than this and the p95 would just be the maximum of a handful of values.
MIN_P95_INTERVALS = 20
DAY_SECONDS = 86400
# A day counts for the trend when at least half of its polls are there.
MIN_DAY_INTERVALS = (DAY_SECONDS // POLL_SECONDS) // 2
MIN_FORECAST_DAYS = 3
FORECAST_DAYS = 7
HORIZON_DAYS = 365

# Two-sided 95 % Student t for 1..30 degrees of freedom.
_T975 = (
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
)


def t_value_95(degrees_of_freedom):
    """Two-sided 95 % critical value of Student's t."""
    if degrees_of_freedom < 1:
        return None
    if degrees_of_freedom <= 30:
        return _T975[degrees_of_freedom - 1]
    if degrees_of_freedom <= 60:
        return 2.0
    if degrees_of_freedom <= 120:
        return 1.98
    return 1.96


def nearest_rank(values, percent):
    """Nearest-rank percentile of a non-empty list (integer percent), or None for an empty one."""
    if not values:
        return None
    ordered = sorted(values)
    rank = (int(percent) * len(ordered) + 99) // 100
    rank = max(1, min(len(ordered), rank))
    return ordered[rank - 1]


def valid_intervals(intervals):
    """Keep intervals with a real rate: no counter reset, no hole longer than MAX_GAP_SECONDS."""
    kept = []
    for item in intervals:
        if item.get("rx_bps") is None or item.get("tx_bps") is None:
            continue
        if item["dt_seconds"] <= 0 or item["dt_seconds"] > MAX_GAP_SECONDS:
            continue
        kept.append(item)
    return kept


def sum_interfaces(per_interface):
    """Total rate of several interfaces: only timestamps where every interface has a valid interval.

    A missing term would understate the total (and the p95), so those timestamps are dropped.
    `per_interface` is a list of valid_intervals() lists, one per interface that has data.
    """
    if not per_interface:
        return []
    merged = {}
    for intervals in per_interface:
        for item in intervals:
            slot = merged.setdefault(
                item["ts_utc"], {"ts_utc": item["ts_utc"], "dt_seconds": item["dt_seconds"], "rx_bps": 0.0, "tx_bps": 0.0, "n": 0}
            )
            slot["rx_bps"] += item["rx_bps"]
            slot["tx_bps"] += item["tx_bps"]
            slot["n"] += 1
    total = []
    for ts in sorted(merged):
        if merged[ts]["n"] == len(per_interface):
            total.append(merged[ts])
    return total


def _direction_stats(intervals, key):
    seconds = 0.0
    weighted = 0.0
    values = []
    for item in intervals:
        seconds += item["dt_seconds"]
        weighted += item[key] * item["dt_seconds"]
        values.append(item[key])
    if not values:
        return {"avg_bps": None, "max_bps": None, "p95_bps": None}
    p95 = None
    if len(values) >= MIN_P95_INTERVALS:
        p95 = nearest_rank(values, 95)
    return {"avg_bps": weighted / seconds, "max_bps": max(values), "p95_bps": p95}


def window_stats(intervals, window_seconds):
    """avg (time-weighted), max and nearest-rank p95 of RX and TX over valid intervals.

    `reason` is None when the p95 exists, "no_data" with no valid interval, "needs_samples" when
    there are fewer than MIN_P95_INTERVALS of them.
    """
    intervals = valid_intervals(intervals)
    covered = sum(item["dt_seconds"] for item in intervals)
    reason = None
    if not intervals:
        reason = "no_data"
    elif len(intervals) < MIN_P95_INTERVALS:
        reason = "needs_samples"
    return {
        "window_seconds": window_seconds,
        "valid_intervals": len(intervals),
        "min_intervals": MIN_P95_INTERVALS,
        "covered_seconds": int(round(covered)),
        # True when the data covers less than 90 % of the window (the interface is new or there were gaps).
        "partial": covered < 0.9 * window_seconds,
        "rx": _direction_stats(intervals, "rx_bps"),
        "tx": _direction_stats(intervals, "tx_bps"),
        "reason": reason,
        "method": "nearest-rank p95 over valid intervals (counter resets and gaps over %d s excluded)" % MAX_GAP_SECONDS,
    }


def utilization(stats, speed_bps):
    """p95 of each direction as % of the link speed (full duplex: RX and TX are separate lanes).

    With an unknown speed (0, the case of the lab) there is no percentage, only the reason.
    """
    if not speed_bps or speed_bps <= 0:
        return {"known": False, "speed_bps": 0, "rx_p95_pct": None, "tx_p95_pct": None, "reason": "link_speed_unknown"}
    result = {"known": True, "speed_bps": int(speed_bps), "rx_p95_pct": None, "tx_p95_pct": None, "reason": None}
    for direction in ("rx", "tx"):
        p95 = stats[direction]["p95_bps"]
        if p95 is not None:
            result[direction + "_p95_pct"] = round(100.0 * p95 / speed_bps, 2)
    if result["rx_p95_pct"] is None and result["tx_p95_pct"] is None:
        result["reason"] = stats["reason"]
    return result


def fit_line(xs, ys):
    """Ordinary least squares. Returns slope, intercept, slope standard error (None if n < 3), n."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    stderr = None
    if n >= 3:
        residual = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
        stderr = math.sqrt(residual / (n - 2) / sxx)
    return {"slope": slope, "intercept": intercept, "stderr": stderr, "n": n}


def project_growth(xs, ys, target, current, min_points, min_span_days, unit_note=""):
    """Days until a growing quantity reaches `target`, by simple linear regression.

    xs are days relative to now (<= 0), ys the quantity, `current` its latest real value.
    The answer always carries its uncertainty: `days_low`/`days_high` come from the 95 % interval
    of the slope, and `days_high` is None when that interval reaches zero or below (the growth
    may be nil, so no upper bound). `confidence` is "noisy" in that case.
    """
    out = {
        "available": False, "reason": None, "n_points": len(xs), "span_days": 0.0,
        "slope_per_day": None, "slope_low": None, "slope_high": None,
        "days_to_target": None, "days_low": None, "days_high": None,
        "confidence": None, "beyond_horizon": False,
    }
    if xs:
        out["span_days"] = round(max(xs) - min(xs), 2)
    if len(xs) < min_points or out["span_days"] < min_span_days:
        out["reason"] = "needs_data"
        return out
    fit = fit_line(xs, ys)
    if fit is None:
        out["reason"] = "needs_data"
        return out
    out["available"] = True
    slope = fit["slope"]
    out["slope_per_day"] = slope
    if fit["stderr"] is not None:
        half = t_value_95(fit["n"] - 2) * fit["stderr"]
        out["slope_low"] = slope - half
        out["slope_high"] = slope + half
    if current >= target:
        out["reason"] = "reached"
        out["days_to_target"] = 0.0
        out["days_low"] = 0.0
        out["days_high"] = 0.0
        out["confidence"] = "clear"
        return out
    if slope <= 0:
        out["reason"] = "no_growth"
        out["confidence"] = "clear" if out["slope_high"] is not None and out["slope_high"] <= 0 else "noisy"
        return out
    gap = target - current
    out["days_to_target"] = gap / slope
    low, high = out["slope_low"], out["slope_high"]
    if high is not None and high > 0:
        out["days_low"] = gap / high
    else:
        out["days_low"] = out["days_to_target"]
    if low is not None and low > 0:
        out["days_high"] = gap / low
        out["confidence"] = "clear"
    else:
        out["days_high"] = None
        out["confidence"] = "noisy"
    out["beyond_horizon"] = out["days_to_target"] > HORIZON_DAYS
    for key in ("days_to_target", "days_low", "days_high"):
        if out[key] is not None:
            out[key] = round(out[key], 1)
    return out


def daily_p95(intervals, now_ts, days=FORECAST_DAYS):
    """p95 of the busier direction for each rolling 24 h window ending at now (index 0 = last 24 h)."""
    intervals = valid_intervals(intervals)
    rows = []
    for k in range(days):
        end = now_ts - k * DAY_SECONDS
        start = end - DAY_SECONDS
        chunk = [item for item in intervals if start < item["ts_utc"] <= end]
        stats = window_stats(chunk, DAY_SECONDS)
        p95_rx = stats["rx"]["p95_bps"]
        p95_tx = stats["tx"]["p95_bps"]
        usable = len(chunk) >= MIN_DAY_INTERVALS and p95_rx is not None and p95_tx is not None
        rows.append(
            {
                "day": k,
                "start_utc": start,
                "end_utc": end,
                "valid_intervals": len(chunk),
                "rx_p95_bps": p95_rx,
                "tx_p95_bps": p95_tx,
                "p95_bps": max(p95_rx, p95_tx) if usable else None,
                "usable": usable,
            }
        )
    return rows


def interface_forecast(intervals, now_ts, speed_bps):
    """Trend of the daily p95 and days until 80 % and 95 % of the link speed, or why there is none."""
    daily = daily_p95(intervals, now_ts)
    usable = [row for row in daily if row["usable"]]
    result = {
        "available": False,
        "reason": None,
        "message": None,
        "usable_days": len(usable),
        "min_days": MIN_FORECAST_DAYS,
        "daily": [
            {"end_utc": row["end_utc"], "p95_bps": row["p95_bps"], "valid_intervals": row["valid_intervals"]}
            for row in reversed(daily)
        ],
        "thresholds": {},
    }
    if not speed_bps or speed_bps <= 0:
        result["reason"] = "link_speed_unknown"
        result["message"] = "Link speed unknown (reported as 0): no capacity forecast."
        return result
    if len(usable) < MIN_FORECAST_DAYS:
        result["reason"] = "needs_days"
        result["message"] = "Needs %d days of data (%d so far)." % (MIN_FORECAST_DAYS, len(usable))
        return result
    xs = [-(row["day"] + 0.5) for row in usable]
    ys = [row["p95_bps"] for row in usable]
    fit = fit_line(xs, ys)
    level_now = fit["intercept"]
    for name, fraction in (("80", 0.80), ("95", 0.95)):
        result["thresholds"][name] = project_growth(
            xs, ys, fraction * speed_bps, level_now, MIN_FORECAST_DAYS, 0.0
        )
    result["available"] = True
    result["level_now_bps"] = level_now
    result["slope_bps_per_day"] = fit["slope"]
    result["speed_bps"] = int(speed_bps)
    return result


def source_projection(rows, rows_24h, span_seconds, bytes_now, retention_days, horizon_days=FORECAST_DAYS):
    """Growth of one data source and its size `horizon_days` from now (pure, integer bytes).

    Rows per day come from the last 24 h, scaled up when the source holds less than a day. The size per row is the
    average of what is stored now. The projection stops at the steady state (rows per day times the retention days),
    because the retention job deletes what is older, so a source does not grow for ever. Under an hour of data there
    is no number, only the reason.
    """
    result = {
        "horizon_days": horizon_days,
        "rows_per_day": None,
        "bytes_per_day": None,
        "bytes_in_horizon": None,
        "steady_state_bytes": None,
        "reason": None,
    }
    if rows <= 0 or bytes_now <= 0:
        result["reason"] = "no_data"
        return result
    if span_seconds < 3600:
        result["reason"] = "too_little_data"
        return result
    window = min(span_seconds, DAY_SECONDS)
    rows_per_day = rows_24h * DAY_SECONDS / window
    bytes_per_day = rows_per_day * bytes_now / rows
    steady = bytes_per_day * retention_days
    result["rows_per_day"] = int(round(rows_per_day))
    result["bytes_per_day"] = int(round(bytes_per_day))
    result["steady_state_bytes"] = int(round(steady))
    result["bytes_in_horizon"] = int(round(min(bytes_now + bytes_per_day * horizon_days, max(steady, bytes_now))))
    return result
