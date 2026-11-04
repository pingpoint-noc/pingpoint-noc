"""Retention job for Mini-NOC.

Deletes records older than the retention window, enforces the target SQLite size
cap (including -wal) while protecting the most recent 24 h of telemetry, and
records the result in collector_status and settings.
"""

import argparse
from datetime import datetime, timezone
import logging
import os
import sys

from storage import create_repository


LOGGER = logging.getLogger("mini_noc.retention")

COLLECTOR_NAME = "retention"
HOST_DEVICE_ID = "mini-noc-host"


def record_capacity_sample(repo, now_ts):
    """Store the size of the database, of its -wal and the free disk space (one row each).

    The timer runs this hourly, so the table holds 24 x 4 rows a day and the normal retention
    purge bounds it. /api/capacity fits a line through them. A failure here only costs one point
    of the line, so it is logged and never fails the retention run.
    """
    try:
        usage = repo.get_storage_usage()
        repo.record_device_metrics(
            HOST_DEVICE_ID,
            now_ts,
            {
                "capacity_db_bytes": usage["db_bytes"],
                "capacity_wal_bytes": usage["wal_bytes"],
                "capacity_disk_total_bytes": usage["disk_total_bytes"],
                "capacity_disk_free_bytes": usage["disk_free_bytes"],
            },
        )
    except Exception as exc:
        LOGGER.warning("Capacity sample skipped: %s", exc)


def run_job(repo, now_ts, retention_days, max_db_bytes=0):
    """Run retention once and record the result. Returns True on success."""
    try:
        repo.run_retention(now_ts, retention_days, max_db_bytes=max_db_bytes)
    except Exception as exc:
        LOGGER.error("Retention failed: %s", exc)
        repo.record_collector_error(COLLECTOR_NAME, HOST_DEVICE_ID, now_ts, str(exc))
        return False
    # After the purge, so the line follows the size the data really keeps, not the peak before it.
    record_capacity_sample(repo, now_ts)
    if hasattr(repo, "get_size_cap_status"):
        cap_status = repo.get_size_cap_status()
        if cap_status and not cap_status.get("satisfied", True):
            LOGGER.warning(
                "Retention completed but size cap (%s bytes) was not met: total=%s bytes (reason=%s)",
                cap_status.get("cap_bytes"),
                cap_status.get("total_bytes"),
                cap_status.get("reason"),
            )
    repo.record_collector_success(COLLECTOR_NAME, HOST_DEVICE_ID, now_ts)
    return True


def main():
    """CLI entry point (one run and exit)."""
    parser = argparse.ArgumentParser(description="Mini-NOC retention job")
    parser.add_argument("--db-path", default=os.environ.get("MININOC_DB_PATH", "data/mini_noc.db"))
    parser.add_argument(
        "--days", type=int, default=int(os.environ.get("MININOC_RETENTION_DAYS", "15"))
    )
    parser.add_argument(
        "--max-mb", type=int, default=int(os.environ.get("MININOC_MAX_DB_MB", "1024")),
        help="size cap in MB (0 = none): the oldest data is deleted first, the last 24 h never",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    repo = create_repository("sqlite", args.db_path)
    repo.init_storage()
    # Component of the platform itself, not a monitored device: needed so that
    # its status row has a valid device_id.
    repo.ensure_device(HOST_DEVICE_ID, "mini-noc host", "127.0.0.1", device_type="platform")

    size_before = os.path.getsize(args.db_path)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    ok = run_job(repo, now_ts, args.days, max_db_bytes=args.max_mb * 1048576)
    size_after = os.path.getsize(args.db_path)
    LOGGER.info("Retention %d days: ok=%s, database %d -> %d bytes", args.days, ok, size_before, size_after)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
