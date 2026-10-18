"""Size and age of the nfcapd flow files.

nfcapd writes one file per rotation (every 60 s here) into year/month/day folders and never deletes
anything by itself unless it runs with -e and has limits set with `nfexpire` (lab 3, section 13.2).
Without that the directory grows for ever (~25 MB a day in the lab), which breaks the retention
requirement of the assignment ("the disk cannot fill up"). This module only measures: it never deletes.
"""

import os

CAPTURE_PREFIX = "nfcapd."
DEFAULT_FLOW_DIR = "/var/cache/nfdump/mikrotik"


def flow_store_usage(flow_dir, now_ts):
    """Return files, bytes and age of the capture files under `flow_dir` (missing directory = not exists)."""
    usage = {"exists": False, "files": 0, "bytes": 0, "oldest_age_days": None, "oldest_utc": None, "newest_utc": None}
    if not flow_dir or not os.path.isdir(flow_dir):
        return usage
    usage["exists"] = True
    oldest = None
    newest = None
    for folder, _dirs, names in os.walk(flow_dir):
        for name in names:
            if not name.startswith(CAPTURE_PREFIX):
                continue
            try:
                info = os.stat(os.path.join(folder, name))
            except OSError:
                # Rotated or expired while we were looking: it is simply not there any more.
                continue
            usage["files"] += 1
            usage["bytes"] += info.st_size
            stamp = int(info.st_mtime)
            if oldest is None or stamp < oldest:
                oldest = stamp
            if newest is None or stamp > newest:
                newest = stamp
    if oldest is not None:
        usage["oldest_utc"] = oldest
        usage["newest_utc"] = newest
        usage["oldest_age_days"] = max(0.0, (now_ts - oldest) / 86400.0)
    return usage
