"""F6-06: per-address limits on enrolment, bootstrap downloads and node telemetry."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from api.app import create_app
from api.netguard import ENROLL_PER_MINUTE, SCRIPT_PER_MINUTE, TELEMETRY_PER_MINUTE, RateLimiter
from storage import create_repository


class RateLimiterTests(unittest.TestCase):
    def test_allows_up_to_the_limit_then_gives_retry_after(self):
        limiter = RateLimiter(3, 60)
        self.assertEqual([limiter.check("a", t) for t in (0, 1, 2)], [0, 0, 0])
        wait = limiter.check("a", 3)
        self.assertGreater(wait, 0)
        self.assertLessEqual(wait, 61)

    def test_other_addresses_are_not_affected(self):
        limiter = RateLimiter(1, 60)
        self.assertEqual(limiter.check("a", 0), 0)
        self.assertGreater(limiter.check("a", 1), 0)
        self.assertEqual(limiter.check("b", 1), 0)

    def test_window_slides(self):
        limiter = RateLimiter(1, 60)
        self.assertEqual(limiter.check("a", 0), 0)
        self.assertGreater(limiter.check("a", 30), 0)
        self.assertEqual(limiter.check("a", 61), 0)

    def test_memory_is_bounded(self):
        limiter = RateLimiter(1, 60)
        for i in range(RateLimiter.TRACKED_LIMIT + 50):
            limiter.check("k%d" % i, 0)
        self.assertLessEqual(len(limiter.hits), RateLimiter.TRACKED_LIMIT)


class EndpointLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = str(Path(self.tmp.name) / "noc.db")
        create_repository("sqlite", db).init_storage()
        self.env = mock.patch.dict(
            os.environ, {"MININOC_ENROLL_TOKEN": "enrol-secret-0123456789", "MININOC_NOC_IP": "192.0.2.10"}
        )
        self.env.start()
        self.client = TestClient(create_app(db, clock=lambda: 1000))

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.tmp.cleanup()

    def burst(self, method, path, limit, **kwargs):
        codes = [getattr(self.client, method)(path, **kwargs).status_code for _ in range(limit + 1)]
        self.assertNotIn(429, codes[:limit])
        return codes[-1]

    def test_enrolment_is_limited_even_with_a_wrong_token(self):
        self.assertEqual(self.burst("post", "/api/nodes/register", ENROLL_PER_MINUTE, json={"ip": "192.0.2.5"}), 429)

    def test_bootstrap_script_downloads_are_limited(self):
        self.assertEqual(self.burst("get", "/api/bootstrap.sh", SCRIPT_PER_MINUTE), 429)

    def test_telemetry_is_limited_and_says_when_to_retry(self):
        body = {"device_id": "nope"}
        self.assertEqual(self.burst("post", "/api/nodes/telemetry", TELEMETRY_PER_MINUTE, json=body, headers={"X-Node-Token": "x"}), 429)
        res = self.client.post("/api/nodes/telemetry", json=body, headers={"X-Node-Token": "x"})
        self.assertIn("retry-after", res.headers)


if __name__ == "__main__":
    unittest.main()
