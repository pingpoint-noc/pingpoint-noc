"""Syslog collector for Mini-NOC.

Reads the per-source files written by rsyslog (/var/log/remote/<ip>/syslog.log,
LabFormat template from lab P2) and stores the events in the database.

The position in the file ((inode, byte offset, first-line head)) is saved in the
same SQLite transaction as the events. Across logrotate renames (.1, .2, .gz) or
copytruncate rotations where the rotated copy is preserved on disk, pending lines
are drained in order before reading the new active file. If a file is truncated
in-place without a rotated copy or a rotated file is already deleted before the
collector runs, those missing lines cannot be recovered and an explicit warning
is logged.

Line format (one per message):
    <rfc3339 time> <source ip> <hostname> <facility>.<severity> <tag+message>
"""

import argparse
from datetime import datetime, timezone
import gzip
import ipaddress
import json
import logging
import os
import time

import scenario
from collectors.discovery import qualify_device
from storage import create_repository


LOGGER = logging.getLogger("mini_noc.syslog_ingestor")

COLLECTOR_NAME = "syslog"
MAX_LINES_PER_BATCH = 1000
MAX_LINE_BYTES = 8192


def parse_line(line):
    """Parse one LabFormat line into a dict. Return None if it is malformed.

    The time is converted to UTC epoch seconds with the offset written in the
    file. RouterOS sends BSD syslog (no year, no zone) and rsyslog stamps it
    with the zone of the COLLECTOR, so when the router and the collector are in
    different zones the event time comes out shifted by the difference. That
    case is not measured yet (not measured in the reference deployment). Linux hosts
    send RFC 5424 with their own zone, which is also written in the file.
    """
    parts = line.rstrip("\r\n").split(" ", 4)
    if len(parts) < 5:
        return None
    time_text, source_ip, hostname, facsev, message = parts
    if "." not in facsev:
        return None
    facility, severity = facsev.split(".", 1)
    if facility == "" or severity == "":
        return None

    try:
        moment = datetime.fromisoformat(time_text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        # No offset: the zone is unknown, so do not guess.
        return None

    return {
        "ts_utc": int(moment.astimezone(timezone.utc).timestamp()),
        "source_ip": source_ip,
        "hostname": hostname,
        "facility": facility,
        "severity": severity,
        "topic": "",
        "message": message,
    }


def _open_log_stream(file_path, is_gz=False):
    if is_gz:
        return gzip.open(file_path, "rb")
    return open(file_path, "rb")


def _read_file_head(file_path, is_gz=False):
    """Return the first line of `file_path` (decoded) to identify files across compression or copytruncate."""
    try:
        with _open_log_stream(file_path, is_gz=is_gz) as handle:
            raw = handle.readline(MAX_LINE_BYTES)
            if not raw:
                return ""
            return raw.decode("utf-8", errors="replace")
    except (OSError, EOFError):
        return ""


def _read_stream_lines(file_path, start_offset, max_lines, is_gz=False):
    """Read up to `max_lines` complete lines from `file_path` starting at `start_offset`.

    Returns `(lines, new_offset, at_eof)`.
    """
    lines = []
    offset = int(start_offset)
    at_eof = False
    with _open_log_stream(file_path, is_gz=is_gz) as handle:
        if offset > 0:
            handle.seek(offset)
        while len(lines) < max_lines:
            raw = handle.readline(MAX_LINE_BYTES)
            if raw == b"":
                at_eof = True
                break
            if not raw.endswith(b"\n"):
                if len(raw) < MAX_LINE_BYTES:
                    # Incomplete last line: wait for writer to finish it.
                    at_eof = False
                    break
                # Over-long line: keep the first part and drop the rest of it.
                while True:
                    rest = handle.readline(MAX_LINE_BYTES)
                    if rest == b"" or rest.endswith(b"\n"):
                        break
            offset = handle.tell()
            lines.append(raw.decode("utf-8", errors="replace"))

        if not at_eof and len(lines) >= max_lines:
            probe = handle.read(1)
            if probe == b"":
                at_eof = True

    return lines, offset, at_eof


def _list_rotated_files(path):
    """List preserved rotated siblings (`<name>.<N>` and `<name>.<N>.gz`) ordered from oldest to newest."""
    dir_path = os.path.dirname(os.fspath(path)) or "."
    base_name = os.path.basename(os.fspath(path))
    prefix = base_name + "."
    try:
        entries = os.listdir(dir_path)
    except OSError:
        return []

    rotated = []
    for name in entries:
        if not name.startswith(prefix):
            continue
        suffix = name[len(prefix) :]
        is_gz = False
        if suffix.endswith(".gz"):
            is_gz = True
            suffix = suffix[:-3]
        if not suffix.isdigit():
            continue
        rot_num = int(suffix)
        if rot_num <= 0:
            continue
        full_path = os.path.join(dir_path, name)
        try:
            st = os.stat(full_path)
        except OSError:
            continue
        if not os.path.isfile(full_path):
            continue
        rotated.append(
            {
                "rot_num": rot_num,
                "path": full_path,
                "is_gz": is_gz,
                "inode": st.st_ino,
                "size": st.st_size,
                "head": _read_file_head(full_path, is_gz=is_gz),
            }
        )

    # Sort descending by rotation number so .3, .2, .1 are drained in chronological order
    rotated.sort(key=lambda r: (-r["rot_num"], r["is_gz"]))
    return rotated


def read_new_lines(path, cursor, max_lines=MAX_LINES_PER_BATCH):
    """Read complete lines written after `cursor`, draining preserved rotations (.1, .2, .gz) first.

    cursor is {"inode": int, "offset": int, "head": str} or None. Returns (lines, new_cursor).
    Only lines ending in a newline are consumed, so a line that rsyslog is still
    writing is read next time instead of being cut in half. If a file was rotated
    or truncated and its unread tail cannot be found in any preserved rotation,
    an explicit warning is logged and `new_cursor["unrecoverable_gap"] = True` is set.
    """
    stat_result = os.stat(path)
    active_inode = stat_result.st_ino
    active_size = stat_result.st_size
    active_head = _read_file_head(path, is_gz=False) if active_size > 0 else ""

    if cursor is None:
        lines, new_offset, _ = _read_stream_lines(path, 0, max_lines, is_gz=False)
        return lines, {
            "inode": active_inode,
            "offset": new_offset,
            "head": active_head if new_offset > 0 else "",
        }

    saved_inode = cursor.get("inode")
    saved_offset = int(cursor.get("offset", 0))
    saved_head = cursor.get("head") or ""

    same_active_file = (
        saved_inode == active_inode
        and saved_offset <= active_size
        and (saved_offset == 0 or not saved_head or active_head == saved_head)
    )
    if same_active_file:
        lines, new_offset, _ = _read_stream_lines(path, saved_offset, max_lines, is_gz=False)
        return lines, {
            "inode": active_inode,
            "offset": new_offset,
            "head": active_head if new_offset > 0 else "",
        }

    # Rotation or truncation detected (or continuing a multi-batch drain from a rotated file).
    rotated = _list_rotated_files(path)
    match_idx = None

    if saved_inode != active_inode:
        for idx, r in enumerate(rotated):
            if r["inode"] == saved_inode and (not saved_head or r["head"] == saved_head):
                match_idx = idx
                break

    if match_idx is None and saved_head:
        for idx, r in enumerate(rotated):
            if r["head"] == saved_head:
                match_idx = idx
                break

    unrecoverable_gap = False
    if match_idx is not None:
        r_match = rotated[match_idx]
        LOGGER.info(
            "Rotation detected on %s: draining from %s at offset %d",
            path,
            r_match["path"],
            saved_offset,
        )
        sources = [
            (r_match["path"], r_match["is_gz"], r_match["inode"], r_match["head"], saved_offset)
        ]
        for r_next in rotated[match_idx + 1 :]:
            sources.append((r_next["path"], r_next["is_gz"], r_next["inode"], r_next["head"], 0))
        sources.append((path, False, active_inode, active_head, 0))
    else:
        if saved_offset > 0 or saved_head:
            unrecoverable_gap = True
            LOGGER.warning(
                "Unrecoverable rotation/truncation gap on %s (previous inode=%s, offset=%d): "
                "rotated file not found or truncated without copy; restarting at offset 0",
                path,
                saved_inode,
                saved_offset,
            )
        sources = [(path, False, active_inode, active_head, 0)]

    lines = []
    cur_state = {
        "inode": active_inode,
        "offset": 0,
        "head": "",
    }

    for idx, (src_path, src_gz, src_inode, src_head, start_off) in enumerate(sources):
        remaining = max_lines - len(lines)
        if remaining <= 0:
            break
        chunk, new_off, at_eof = _read_stream_lines(src_path, start_off, remaining, is_gz=src_gz)
        lines.extend(chunk)
        is_last_source = idx == len(sources) - 1
        if not is_last_source and at_eof:
            next_src = sources[idx + 1]
            cur_state = {
                "inode": next_src[2],
                "offset": 0,
                "head": "",
            }
        else:
            cur_state = {
                "inode": src_inode,
                "offset": new_off,
                "head": src_head if new_off > 0 else "",
            }
            break

    if unrecoverable_gap:
        cur_state["unrecoverable_gap"] = True

    return lines, cur_state


def record_error_safely(repo, device_id, now_ts, message, collector_name=COLLECTOR_NAME):
    """Record a failure without ever raising (the database may be the thing that failed)."""
    try:
        repo.record_collector_error(collector_name, device_id, now_ts, message)
    except Exception as exc:
        LOGGER.error("Could not record the error (%s): %s", message, exc)


def ingest_once(repo, device_id, path, now_ts=None, collector_name=COLLECTOR_NAME):
    """Read everything new in `path` and store it. Returns the number of events stored.

    `collector_name` is only changed for a file that belongs to an alias IP of the device (K107): its cursor must not
    overwrite the one of the device's own file.
    """
    if now_ts is None:
        now_ts = int(datetime.now(timezone.utc).timestamp())

    extra = {}
    if collector_name != COLLECTOR_NAME:
        extra["collector_name"] = collector_name

    stored = 0
    try:
        cursor_json = repo.get_collector_cursor(collector_name, device_id)
        cursor = None
        if cursor_json:
            cursor = json.loads(cursor_json)

        while True:
            lines, new_cursor = read_new_lines(path, cursor, max_lines=MAX_LINES_PER_BATCH)
            events = []
            for line in lines:
                event = parse_line(line)
                if event is None:
                    LOGGER.warning("Skipping malformed line: %r", line[:120])
                    continue
                events.append(event)

            repo.record_syslog_batch(
                device_id=device_id,
                events=events,
                cursor_json=json.dumps(new_cursor),
                ts_utc=now_ts,
                **extra,
            )
            stored += len(events)
            cursor = new_cursor
            if len(lines) < MAX_LINES_PER_BATCH:
                break
    except FileNotFoundError:
        # rsyslog creates the file on the first message from the device.
        record_error_safely(repo, device_id, now_ts, f"file not found: {path}", collector_name)
    except Exception as exc:
        LOGGER.error("Syslog ingest failed for %s: %s", device_id, exc)
        record_error_safely(repo, device_id, now_ts, str(exc), collector_name)

    return stored


def scan_syslog_directory(
    repo,
    log_dir,
    primary_ip=None,
    primary_device_id=None,
    now_ts=None,
    snmp_query_fn=None,
):
    """Scan `<log_dir>/<ip>/syslog.log` folders, auto-discover new syslog sources, and ingest events."""
    if now_ts is None:
        now_ts = int(datetime.now(timezone.utc).timestamp())

    total_events = 0
    discovered_ips = []

    if primary_ip is not None and primary_device_id is not None:
        primary_path = os.path.join(log_dir, primary_ip, "syslog.log")
        total_events += ingest_once(repo, primary_device_id, primary_path, now_ts=now_ts)

    if not os.path.isdir(log_dir):
        return {"events_stored": total_events, "discovered_ips": discovered_ips}

    try:
        entries = sorted(os.listdir(log_dir))
    except OSError as exc:
        LOGGER.warning("Cannot list syslog directory %s: %s", log_dir, exc)
        return {"events_stored": total_events, "discovered_ips": discovered_ips}

    for entry_name in entries:
        if primary_ip is not None and entry_name == primary_ip:
            continue
        try:
            ipaddress.ip_address(entry_name)
        except ValueError:
            continue

        candidate_log = os.path.join(log_dir, entry_name, "syslog.log")
        if not os.path.isfile(candidate_log):
            continue

        try:
            existing = repo.get_device_by_ip(entry_name)
            if existing is None:
                disc = repo.upsert_discovered_device(
                    ip=entry_name,
                    source="syslog",
                    ts_utc=now_ts,
                )
                target_id = disc["device_id"]
                need_qualify = True
            else:
                target_id = existing["id"]
                need_qualify = existing.get("probe_status") == "pending"

            cursor_name = COLLECTOR_NAME
            if existing is not None and existing.get("matched_alias"):
                cursor_name = COLLECTOR_NAME + ":" + entry_name
            count = ingest_once(repo, target_id, candidate_log, now_ts=now_ts, collector_name=cursor_name)
            total_events += count

            if need_qualify:
                LOGGER.info("New source detected from syslog directory: %s (%s)", entry_name, target_id)
                discovered_ips.append(entry_name)
                qualify_device(
                    repo=repo,
                    device_id=target_id,
                    ip=entry_name,
                    now_ts=now_ts,
                    snmp_query_fn=snmp_query_fn,
                    seen_passively=(count > 0),
                )
        except Exception as exc:
            LOGGER.error("Passive syslog discovery failed for %s: %s", entry_name, exc)

    return {"events_stored": total_events, "discovered_ips": discovered_ips}


def main():
    """CLI entry point for the syslog collector service."""
    parser = argparse.ArgumentParser(description="Mini-NOC Syslog Ingestor Service")
    parser.add_argument("--db-path", default=os.environ.get("MININOC_DB_PATH", "data/mini_noc.db"))
    parser.add_argument("--device-id", default=scenario.value("MININOC_DEVICE_ID"))
    parser.add_argument("--device-name", default=scenario.value("MININOC_DEVICE_NAME"))
    parser.add_argument("--router-ip", default=scenario.value("MININOC_ROUTER_IP"))
    parser.add_argument(
        "--log-dir",
        default=os.environ.get("MININOC_SYSLOG_DIR", "/var/log/remote"),
        help="Directory with one <ip>/syslog.log per source",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("MININOC_SYSLOG_INTERVAL", "2")),
    )
    parser.add_argument("--once", action="store_true", help="Read what is new and exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    repo = create_repository("sqlite", args.db_path)
    repo.init_storage()
    if repo.get_device_status(args.device_id) is None:  # never overwrite what the operator already set
        repo.ensure_device(
            device_id=args.device_id,
            name=args.device_name,
            ip=args.router_ip,
            device_type="router",
            tz="Europe/Madrid",
        )

    while True:
        result = scan_syslog_directory(
            repo=repo,
            log_dir=args.log_dir,
            primary_ip=args.router_ip,
            primary_device_id=args.device_id,
        )
        if result["events_stored"] > 0:
            LOGGER.info("Stored %d syslog events across monitored sources", result["events_stored"])
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
