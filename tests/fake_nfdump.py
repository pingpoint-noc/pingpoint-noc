"""Stand-in for nfdump used by the tests: prints samples/flows_t1.csv.

FAKE_NFDUMP_LOG: file where it writes its arguments and the TZ it received.
FAKE_NFDUMP_FAIL: if set, print an error and exit 1.
"""

import os
from pathlib import Path
import sys


SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "flows_t1.csv"

log_path = os.environ.get("FAKE_NFDUMP_LOG")
if log_path:
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write("TZ=" + os.environ.get("TZ", "") + "\n")
        handle.write(" ".join(sys.argv[1:]) + "\n")

if os.environ.get("FAKE_NFDUMP_FAIL"):
    sys.stderr.write("nfdump: no such directory\n")
    sys.exit(1)

sys.stdout.write(SAMPLE.read_text(encoding="utf-8"))
