"""Replay stored flows through the flow_new_destination rule to see how noisy it would be.

Works on a COPY of the database (the original is only read), so it is safe on a live system.
It re-creates what the alert engine would have done, one cycle every --step seconds, from the first
stored flow to the last, and prints the alerts raised per host and day. Use it to tune
MININOC_NEWDST_* with real traffic instead of waiting days (design: docs/diseno-destino-externo-nuevo.md).

    python -m tools.replay_new_destination --db-path data/mini_noc.db --device-id edge-router-1 \
        --learning-hours 24 --baseline 20 --burst 10

Limits: the replay uses the flows' own timestamps (the router clock may be off by seconds), and the
"first seen" of a destination is the replay time of the cycle that learned it.
"""

import argparse
import os
import shutil
import sqlite3
import tempfile
import time

from alerts import engine
from storage import create_repository


def prepare_copy(source_path, copy_path, device_id):
    """Copy the database, empty the learned destinations and the rule's alerts; return (first_ts, last_ts) of the flows."""
    shutil.copyfile(source_path, copy_path)
    conn = sqlite3.connect(copy_path)
    try:
        # An old database may predate the table: create it through the normal initialisation below.
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'known_destinations'").fetchall()
        if len(tables) > 0:
            conn.execute("DELETE FROM known_destinations")
        conn.execute("DELETE FROM alerts WHERE rule_id = 'flow_new_destination'")
        row = conn.execute(
            "SELECT MIN(ts_start_utc), MAX(ts_start_utc) FROM flows WHERE device_id = ?", (device_id,)
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    return row[0], row[1]


def run_replay(copy_path, device_id, first_ts, last_ts, learning_hours, baseline, burst, window_seconds, step):
    """Run the rule once per step over the whole stored period; return the number of cycles."""
    repo = create_repository("sqlite", copy_path)
    repo.init_storage()
    now_ts = int(first_ts) + 200
    cycles = 0
    while now_ts <= int(last_ts):
        engine.evaluate_new_destination_rule(
            repo,
            device_id,
            now_ts,
            learning_seconds=int(learning_hours * 3600),
            baseline_size=baseline,
            burst_size=burst,
            window_seconds=window_seconds,
        )
        cycles = cycles + 1
        now_ts = now_ts + step
    return cycles


def print_report(copy_path, cycles, seconds):
    conn = sqlite3.connect(copy_path)
    try:
        print("cycles=%d, %.0f s" % (cycles, seconds))
        print("alerts per host and day (UTC date the alert fired):")
        rows = conn.execute(
            "SELECT substr(target, 8) AS host, date(fired_utc, 'unixepoch') AS day, COUNT(*) AS n "
            "FROM alerts WHERE rule_id = 'flow_new_destination' GROUP BY 1, 2 ORDER BY 1, 2"
        ).fetchall()
        for row in rows:
            print("  %-16s %s %d" % (row[0], row[1], row[2]))
        total = conn.execute("SELECT COUNT(*) FROM alerts WHERE rule_id = 'flow_new_destination'").fetchone()[0]
        print("total alerts: %d" % total)
        print("first alerts:")
        samples = conn.execute(
            "SELECT datetime(fired_utc, 'unixepoch'), message FROM alerts "
            "WHERE rule_id = 'flow_new_destination' ORDER BY fired_utc LIMIT 5"
        ).fetchall()
        for row in samples:
            print("  %s %s" % (row[0], row[1][:170]))
        print("destinations learned per host:")
        learned = conn.execute(
            "SELECT internal_host, COUNT(*) FROM known_destinations GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
        for row in learned:
            print("  %-16s %d" % (row[0], row[1]))
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay stored flows through flow_new_destination (on a copy of the database)")
    parser.add_argument("--db-path", default=os.environ.get("MININOC_DB_PATH", "data/mini_noc.db"))
    parser.add_argument("--device-id", default=os.environ.get("MININOC_DEVICE_ID", "edge-router-1"))
    parser.add_argument("--learning-hours", type=float, default=24.0)
    parser.add_argument("--baseline", type=int, default=20)
    parser.add_argument("--burst", type=int, default=10)
    parser.add_argument("--window", type=int, default=600, help="seconds a destination counts as new")
    parser.add_argument("--step", type=int, default=60, help="seconds between simulated cycles (the real engine uses 30)")
    parser.add_argument("--copy-path", default="", help="where to put the working copy (default: a temporary file)")
    args = parser.parse_args(argv)

    copy_path = args.copy_path
    cleanup = False
    if copy_path == "":
        handle, copy_path = tempfile.mkstemp(suffix=".db", prefix="replay_newdst_")
        os.close(handle)
        cleanup = True

    started = time.time()
    try:
        first_ts, last_ts = prepare_copy(args.db_path, copy_path, args.device_id)
        if first_ts is None:
            print("No flows stored for device %s: nothing to replay." % args.device_id)
            return
        cycles = run_replay(
            copy_path, args.device_id, first_ts, last_ts,
            args.learning_hours, args.baseline, args.burst, args.window, args.step,
        )
        print_report(copy_path, cycles, time.time() - started)
    finally:
        if cleanup:
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(copy_path + suffix):
                    os.remove(copy_path + suffix)


if __name__ == "__main__":
    main()
