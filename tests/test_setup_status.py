"""The first-run guide: four steps computed from what is stored, never written by the status call itself."""

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from api import setup
from api.app import create_app
from storage import create_repository

SUGGESTIONS = {
    "suggestions": {"MININOC_INTERNAL_NETS": "10.0.0.0/24", "MININOC_NOC_IP": "10.0.0.2", "MININOC_TZ": "Europe/Madrid"},
    "configured": {"MININOC_INTERNAL_NETS": False, "MININOC_NOC_IP": False, "MININOC_TZ": False},
    "effective": {"MININOC_INTERNAL_NETS": "", "MININOC_NOC_IP": "", "MININOC_TZ": "UTC"},
}
SET_UP = {
    "suggestions": SUGGESTIONS["suggestions"],
    "configured": {name: True for name in SUGGESTIONS["configured"]},
    "effective": {"MININOC_INTERNAL_NETS": "10.0.0.0/24", "MININOC_NOC_IP": "10.0.0.2", "MININOC_TZ": "Europe/Madrid"},
}
HOUR = 3600


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.repo = create_repository("sqlite", self.tmp.name)
        self.repo.init_storage()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.tmp.name + suffix)
            except OSError:
                pass

    def steps(self, now_ts=1_000_000, **kwargs):
        kwargs.setdefault("suggestions", SUGGESTIONS)
        result = setup.status(self.repo, now_ts, **kwargs)
        return {step["id"]: step for step in result["steps"]}, result

    def add(self, device_id, device_type="router", first_seen=None):
        self.repo.ensure_device(device_id=device_id, name=device_id, ip="10.0.0.%d" % (len(device_id) + 10), device_type=device_type)
        if first_seen is not None:
            conn = self.repo.open_write() if hasattr(self.repo, "open_write") else None
            if conn is not None:
                conn.execute("UPDATE devices SET first_seen_utc=? WHERE id=?", (first_seen, device_id))
                conn.commit()
                conn.close()


class TestSteps(Base):
    def test_a_fresh_install_has_every_step_open_and_is_not_complete(self):
        steps, result = self.steps()
        self.assertEqual(steps["networks"]["state"], "attention")
        self.assertEqual(steps["devices"]["state"], "todo")
        self.assertEqual(steps["learning"]["state"], "todo")
        self.assertEqual(steps["notifications"]["state"], "todo")
        self.assertFalse(result["complete"])

    def test_networks_are_done_when_every_suggested_value_is_set(self):
        steps, _ = self.steps(suggestions=SET_UP)
        self.assertEqual(steps["networks"]["state"], "done")

    def test_a_device_without_a_role_is_listed_for_review(self):
        self.add("host-a", device_type="unknown")
        self.add("host-b", device_type="server")
        steps, _ = self.steps()
        self.assertEqual(steps["devices"]["state"], "attention")
        self.assertEqual([d["id"] for d in steps["devices"]["to_review"]], ["host-a"])

    def test_devices_are_done_when_every_one_has_a_role(self):
        self.add("host-b", device_type="server")
        steps, _ = self.steps()
        self.assertEqual(steps["devices"]["state"], "done")

    def test_learning_waits_for_the_period_then_offers_only_proposals_that_change_something(self):
        self.add("host-b", device_type="server")
        now = 10_000_000
        self.repo.set_settings({"alert_new_dest_learning_hours": "24"}, now)
        proposals = [
            {"key": "a", "setting": "alert_icmp_latency_ms", "unit": "ms", "current": 150, "suggested": 80, "note": "lowered"},
            {"key": "b", "setting": "alert_host_cpu_pct", "unit": "%", "current": 90, "suggested": 90, "note": "kept"},
            {"key": "c", "setting": "alert_host_disk_pct", "unit": "%", "current": 90, "suggested": None, "note": "no data"},
        ]
        self.repo.record_device_metrics("host-b", now, {"cpu_load_pct": 5.0})  # the first sample
        steps, _ = self.steps(now_ts=now + 2 * HOUR, proposals=proposals)
        self.assertEqual(steps["learning"]["state"], "waiting")
        steps, _ = self.steps(now_ts=now + 30 * HOUR, proposals=proposals)
        self.assertEqual(steps["learning"]["state"], "attention")
        self.assertEqual([p["setting"] for p in steps["learning"]["proposals"]], ["alert_icmp_latency_ms"])
        steps, _ = self.steps(now_ts=now + 30 * HOUR, proposals=proposals[1:])
        self.assertEqual(steps["learning"]["state"], "done")

    def test_notifications_need_a_destination_and_then_a_test(self):
        steps, _ = self.steps(destinations=["ntfy"])
        self.assertEqual(steps["notifications"]["state"], "attention")
        self.repo.set_settings({setup.KEY_NOTIFY_TESTED: "1234"}, 1234)
        steps, _ = self.steps(destinations=["ntfy"])
        self.assertEqual(steps["notifications"]["state"], "done")
        self.assertEqual(steps["notifications"]["tested_utc"], 1234)

    def test_everything_done_is_complete(self):
        self.add("host-b", device_type="server")
        self.repo.record_device_metrics("host-b", 1000, {"cpu_load_pct": 5.0})
        self.repo.set_settings({setup.KEY_NOTIFY_TESTED: "1"}, 1)
        _, result = self.steps(now_ts=10**9, suggestions=SET_UP, proposals=[], destinations=["ntfy"])
        self.assertTrue(result["complete"])

    def test_the_status_writes_nothing(self):
        before = self.repo.get_settings()
        self.steps()
        self.assertEqual(self.repo.get_settings(), before)


class TestApi(Base):
    def test_the_endpoint_answers_with_four_steps(self):
        client = TestClient(create_app(self.tmp.name, clock=lambda: 2000))
        res = client.get("/api/setup/status")
        self.assertEqual(res.status_code, 200)
        self.assertEqual([s["id"] for s in res.json()["steps"]], ["networks", "devices", "learning", "notifications"])


if __name__ == "__main__":
    unittest.main()
