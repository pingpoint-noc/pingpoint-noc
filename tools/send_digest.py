"""Print the daily summary and, unless --dry-run, send it now to the destinations that receive it (lab tests).

    python -m tools.send_digest --db-path data/mini_noc.db --dry-run     # only print the text
    python -m tools.send_digest --db-path data/mini_noc.db               # send one test summary, one attempt each
    python -m tools.send_digest --destination ntfy                       # only to ntfy

It reads the same settings as the alert engine (destinations, web address) and never writes the day already sent,
so it does not stop the scheduled summary. Exit code: 0 when at least one destination accepted it (or --dry-run), 1
when none did, 2 for a usage problem (no database, no destination). Addresses and tokens are never printed.
"""

import argparse
import os
import sys
import time

from alerts import digest
from alerts.notify import DESTINATION_NAMES, build_notifiers
from storage import create_repository


def main(argv=None, now_ts=None, out=print):
    parser = argparse.ArgumentParser(description="Print or send the Mini-NOC daily summary")
    parser.add_argument("--db-path", default=os.environ.get("MININOC_DB_PATH", "data/mini_noc.db"))
    parser.add_argument("--dry-run", action="store_true", help="print the text and send nothing")
    parser.add_argument("--destination", choices=DESTINATION_NAMES, default=None, help="send only to this destination")
    args = parser.parse_args(argv)

    if now_ts is None:
        now_ts = int(time.time())
    if not os.path.exists(args.db_path):
        out("Database not found: " + args.db_path)
        return 2
    repo = create_repository("sqlite", args.db_path)
    saved = repo.get_settings()
    public_url = (saved.get("public_url") or os.environ.get("MININOC_PUBLIC_URL") or "").strip().rstrip("/")

    if args.dry_run:
        title, body, _data = digest.build(repo, now_ts, public_url)
        out(title)
        out("-" * 40)
        out(body)
        return 0

    group = build_notifiers(saved, os.environ)
    try:
        sent = digest.send_now(repo, group, now_ts, public_url, args.destination)
    except digest.NoDigestDestination as exc:
        out("Not sent: " + str(exc))
        return 2
    out(sent["title"])
    out("-" * 40)
    out(sent["body"])
    out("-" * 40)
    accepted = False
    for name, ok in sent["results"].items():
        if ok:
            accepted = True
            out("%s: sent" % name)
        else:
            out("%s: FAILED (see the log for the reason)" % name)
    if accepted:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
