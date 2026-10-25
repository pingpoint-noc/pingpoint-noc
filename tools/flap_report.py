"""Report the busiest status-change window of every interface, to tune the flapping thresholds with real data.

Reads interface_state_changes (read-only) and, for each interface, finds the most changes counted inside any
window of --window seconds during the last --days days. An inferred change counts as 2 (the interface went away
and came back between two polls), the same weight the interface_flapping rule uses. The threshold
MININOC_FLAP_CHANGES must be above the peak of a healthy interface and at or below the peak of a flapping one.

    python -m tools.flap_report --db-path data/mini_noc.db --days 7 --window 300
"""

import argparse
import os
import sqlite3
import time

INFERRED_WEIGHT = 2


def build_report(db_path, now_ts, days, window_seconds):
    """Return one row per (device, interface) with changes: peak in any window and weighted total."""
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT device_id, if_index, ts_utc, kind
            FROM interface_state_changes
            WHERE ts_utc >= ? AND ts_utc <= ?
            ORDER BY device_id, if_index, ts_utc, id;
            """,
            (int(now_ts - days * 86400), int(now_ts)),
        ).fetchall()
    finally:
        conn.close()

    grouped = {}
    order = []
    for row in rows:
        key = (row["device_id"], row["if_index"])
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        weight = 1
        if row["kind"] == "inferred":
            weight = INFERRED_WEIGHT
        grouped[key].append((row["ts_utc"], weight))

    report = []
    for key in order:
        events = grouped[key]
        peak = 0
        start = 0
        running = 0
        total = 0
        for end in range(len(events)):
            running += events[end][1]
            total += events[end][1]
            while events[end][0] - events[start][0] >= window_seconds:
                running -= events[start][1]
                start += 1
            if running > peak:
                peak = running
        report.append(
            {"device_id": key[0], "if_index": key[1], "peak": peak, "total_changes": total}
        )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Busiest status-change window per interface")
    parser.add_argument("--db-path", default=os.environ.get("MININOC_DB_PATH", "data/mini_noc.db"))
    parser.add_argument("--days", type=float, default=7)
    parser.add_argument("--window", type=int, default=300)
    args = parser.parse_args(argv)

    report = build_report(args.db_path, int(time.time()), args.days, args.window)
    print("Status changes per interface, last %.0f days, window %d s (inferred counts as 2)" % (args.days, args.window))
    if len(report) == 0:
        print("No changes recorded: every interface was stable. Any threshold >= 1 would have stayed silent.")
        return
    print("%-24s %-9s %-6s %s" % ("device", "ifIndex", "peak", "total"))
    for row in report:
        print("%-24s %-9d %-6d %d" % (row["device_id"], row["if_index"], row["peak"], row["total_changes"]))
    print("Pick MININOC_FLAP_CHANGES above the peak of the interfaces that are healthy.")


if __name__ == "__main__":
    main()
