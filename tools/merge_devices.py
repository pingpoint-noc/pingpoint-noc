"""Merge a duplicate inventory device into the one it really is (K107).

    python -m tools.merge_devices --from node-192-168-89-1 --into edge-router-1            # dry run
    python -m tools.merge_devices --from node-192-168-89-1 --into edge-router-1 --apply

The same router can show up twice (once per address it answers or exports from). The merge moves everything the
duplicate stored (interfaces, samples, metrics, syslog, flows, alerts, silences, windows, known destinations,
collector state, services, Proxmox links) to the target in ONE transaction. When a row collides with a row the
target already has (same primary or unique key) the target row wins and the duplicate's row is dropped. The open
alerts of the duplicate are resolved. The duplicate row is NOT deleted: it stays with status 'merged', which the
SNMP poller, passive discovery, the API and the Overview ignore, and its IP becomes an alias of the target
(table device_aliases) so syslog, flows and discovery that arrive with that IP are attributed to the target.

Without --apply the whole thing runs inside a transaction that is rolled back, so the counts are exactly what
--apply would do. Take a copy of the database first (sqlite3 mini_noc.db ".backup copy.db"). Running it again after
a merge changes nothing.
"""

import argparse
import os
import sys
import time

from storage import create_repository

# (table, column that holds the device id, columns of the unique/primary key that must not collide or None)
MOVES = (
    ("interfaces", "device_id", ("if_index",)),
    ("interface_state_changes", "device_id", None),
    ("interface_samples", "device_id", None),
    ("device_metrics", "device_id", None),
    ("syslog_events", "device_id", None),
    (
        "flows",
        "device_id",
        ("ts_start_utc", "ts_end_utc", "proto", "src_ip", "src_port", "dst_ip", "dst_port"),
    ),
    ("alerts", "device_id", None),
    ("alert_silences", "device_id", ("rule_id", "target")),
    ("maintenance_windows", "device_id", None),
    ("known_destinations", "device_id", ("internal_host", "dst_ip")),
    ("collector_status", "device_id", ("collector_name",)),
    ("host_services", "device_id", ("service_name",)),
    ("proxmox_guests", "hypervisor_id", ("vmid",)),
    ("proxmox_guests", "matched_device_id", None),
    ("proxmox_storages", "hypervisor_id", ("storage_name",)),
)


def build_parser():
    parser = argparse.ArgumentParser(description="Merge a duplicate Mini-NOC device into another one")
    parser.add_argument("--db-path", default=os.environ.get("MININOC_DB_PATH", "data/mini_noc.db"))
    parser.add_argument("--from", dest="source", required=True, help="duplicate device id (kept, marked 'merged')")
    parser.add_argument("--into", dest="target", required=True, help="device id that receives the data")
    parser.add_argument("--apply", action="store_true", help="make the change (default: dry run)")
    return parser


def _device(conn, device_id):
    return conn.execute("SELECT id, ip, device_type, status FROM devices WHERE id = ?;", (device_id,)).fetchone()


def run_merge(conn, source, target, now_ts):
    """Do the merge on `conn` (a transaction must be open). Returns a list of (what, moved, dropped) lines."""
    src = _device(conn, source)
    dst = _device(conn, target)
    if src is None:
        raise ValueError("no such device: " + source)
    if dst is None:
        raise ValueError("no such device: " + target)
    if source == target:
        raise ValueError("--from and --into are the same device")
    if "platform" in (src["device_type"], dst["device_type"]):
        raise ValueError("the Mini-NOC host (platform) cannot be merged")
    if dst["status"] == "merged":
        raise ValueError("%s is itself a merged duplicate; use the device it was merged into" % target)
    if src["status"] == "merged":
        return None

    holder = conn.execute(
        "SELECT id FROM devices WHERE ip = ? AND id NOT IN (?, ?) AND status != 'merged' AND device_type != 'platform';",
        (src["ip"], source, target),
    ).fetchone()
    if holder is not None:
        raise ValueError("the IP %s of %s also belongs to the device %s" % (src["ip"], source, holder["id"]))

    report = []

    # Open alerts of the duplicate end here: the target raises its own if the problem is real.
    closed = conn.execute(
        "UPDATE alerts SET status = 'resolved', resolved_utc = ?, last_eval_utc = ?, "
        "message = message || ' [closed: device merged into ' || ? || ']' "
        "WHERE device_id = ? AND status IN ('pending', 'firing');",
        (now_ts, now_ts, target, source),
    ).rowcount
    report.append(("open alerts resolved", closed, 0))

    # The duplicate's syslog file has its own cursor: keep it under another name, it must not replace the target's.
    conn.execute(
        "UPDATE OR IGNORE collector_status SET collector_name = 'syslog:' || ? WHERE device_id = ? "
        "AND collector_name = 'syslog';",
        (src["ip"], source),
    )

    for table, column, key in MOVES:
        moved = conn.execute(
            "UPDATE OR IGNORE %s SET %s = ? WHERE %s = ?;" % (table, column, column), (target, source)
        ).rowcount
        # What is still left collided with a row of the target (or points at the target twice): the target's wins.
        dropped = 0
        if key is not None:
            dropped = conn.execute("DELETE FROM %s WHERE %s = ?;" % (table, column), (source,)).rowcount
        label = table if column == "device_id" else "%s.%s" % (table, column)
        report.append((label, moved, dropped))

    # Inventory links. A device cannot depend on or hang from itself.
    for column in ("depends_on", "parent_device_id"):
        relinked = conn.execute(
            "UPDATE devices SET %s = ? WHERE %s = ? AND id != ?;" % (column, column), (target, source, target)
        ).rowcount
        conn.execute("UPDATE devices SET %s = NULL WHERE id IN (?, ?) AND %s IN (?, ?);" % (column, column),
                     (source, target, source, target))
        report.append(("devices." + column, relinked, 0))

    # Aliases of the duplicate (from older merges) and its own IP now point at the target.
    conn.execute("UPDATE device_aliases SET device_id = ? WHERE device_id = ?;", (target, source))
    if src["ip"] != dst["ip"]:
        conn.execute(
            "INSERT OR REPLACE INTO device_aliases (ip, device_id, created_utc) VALUES (?, ?, ?);",
            (src["ip"], target, now_ts),
        )
        report.append(("alias " + src["ip"], 1, 0))

    conn.execute(
        "UPDATE devices SET "
        "has_syslog = MAX(has_syslog, (SELECT has_syslog FROM devices WHERE id = ?)), "
        "has_ipfix = MAX(has_ipfix, (SELECT has_ipfix FROM devices WHERE id = ?)), "
        "has_telemetry = MAX(has_telemetry, (SELECT has_telemetry FROM devices WHERE id = ?)), "
        "first_seen_utc = COALESCE(MIN(first_seen_utc, (SELECT first_seen_utc FROM devices WHERE id = ?)), "
        "first_seen_utc, (SELECT first_seen_utc FROM devices WHERE id = ?)), "
        "last_seen_utc = COALESCE(MAX(last_seen_utc, (SELECT last_seen_utc FROM devices WHERE id = ?)), "
        "last_seen_utc, (SELECT last_seen_utc FROM devices WHERE id = ?)) "
        "WHERE id = ?;",
        (source,) * 7 + (target,),
    )
    conn.execute(
        "UPDATE devices SET status = 'merged', has_snmp = 0, reachable = 0, depends_on = NULL, "
        "parent_device_id = NULL WHERE id = ?;",
        (source,),
    )
    report.append(("device %s marked 'merged'" % source, 1, 0))
    return report


def run(args):
    repo = create_repository("sqlite", args.db_path)
    repo.init_storage()
    conn = repo.open_write()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            report = run_merge(conn, args.source.strip(), args.target.strip(), int(time.time()))
        except Exception:
            conn.execute("ROLLBACK;")
            raise
        if report is None:
            conn.execute("ROLLBACK;")
            print("%s is already merged: nothing to do" % args.source)
            return 0
        if args.apply:
            conn.execute("COMMIT;")
        else:
            conn.execute("ROLLBACK;")
    finally:
        conn.close()

    print("%s %s -> %s" % ("MERGED" if args.apply else "DRY RUN (nothing changed)", args.source, args.target))
    print("%-40s %8s %8s" % ("WHAT", "MOVED", "DROPPED"))
    for label, moved, dropped in report:
        print("%-40s %8d %8d" % (label, moved, dropped))
    if not args.apply:
        print("Add --apply to do it (after a database backup).")
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except ValueError as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
