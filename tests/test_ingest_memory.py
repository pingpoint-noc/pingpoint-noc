"""Rule 6 of the assignment: flows are processed on the fly, so memory must not grow with the volume.

Guard test, not a benchmark: it fails if someone makes ingest_rows keep every row (a list or a set that
grows). Measured on 08-11 with 50 000 to 500 000 flows: the Python peak stayed near 1 MB
(docs/evidencias/carga-ingestor-flujos-2026-11-08.txt).
"""

import ipaddress
import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest

from collectors.ipfix.ingestor import build_exporter_resolver, ingest_rows
from storage import create_repository

NETS = [ipaddress.ip_network("192.168.88.0/24")]
NOW = 1_800_000_000


def rows(count):
    for i in range(count):
        yield {
            "firstSeen": "2026-11-08 10:00:00.000",
            "lastSeen": "2026-11-08 10:00:01.000",
            "proto": "6",
            "srcAddr": "192.168.88.%d" % (i % 250 + 1),
            "srcPort": str(1024 + i % 60000),
            "dstAddr": "34.%d.%d.%d" % (i % 200, (i // 200) % 250, (i // 5000) % 250),
            "dstPort": "443",
            "packets": "5",
            "bytes": "600",
            "input": "2",
            "output": "1",
            "srcXIP": "192.168.100.2",
            "dstXIP": "34.%d.%d.%d" % (i % 200, (i // 200) % 250, (i // 5000) % 250),
            "routerIP": "192.168.88.1",
        }


class TestIngestMemory(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def peak_for(self, count, name):
        repo = create_repository("sqlite", Path(self.temp_dir.name) / name)
        repo.init_storage()
        repo.ensure_device("router", "router", "192.168.88.1")
        resolver = build_exporter_resolver(repo, "router")
        tracemalloc.start()
        try:
            ingest_rows(
                repo, "router", rows(count), NETS, {"192.168.100.2"}, NOW,
                json.dumps({"last_end": NOW}), exporter_device_fn=resolver,
            )
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        return peak

    def test_the_peak_does_not_grow_with_the_number_of_flows(self):
        small = self.peak_for(3000, "small.db")
        large = self.peak_for(15000, "large.db")
        # Five times the flows must not cost five times the memory (a growing list would).
        self.assertLess(large, small * 2 + 1048576)
        self.assertLess(large, 8 * 1048576)


if __name__ == "__main__":
    unittest.main()
