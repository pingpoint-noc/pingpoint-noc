"""Coherence of the alert configuration across engine, API, UI and notifier (K109)."""

from pathlib import Path
import os
import re
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from alerts import engine
from alerts.notify import KNOWN_RULE_IDS
from api import app as api_app
from api.app import create_app
from storage import create_repository

ROOT = Path(__file__).resolve().parent.parent
# flow_rare_connection hangs from flow_port_scan by design (docs/ajustes-y-notificaciones.md).
NO_TOGGLE = {"flow_rare_connection"}


class TestToggleCoherence(unittest.TestCase):
    def expected(self):
        return {"rule_" + r + "_enabled" for r in KNOWN_RULE_IDS if r not in NO_TOGGLE}

    def test_engine_api_ui_and_notifier_agree(self):
        self.assertEqual(set(engine.RULE_TOGGLE_KEYS), self.expected())
        self.assertEqual(set(api_app.RULE_TOGGLE_KEYS), self.expected())
        js = (ROOT / "web" / "static" / "js" / "views" / "settings.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "partials" / "tab_settings.html").read_text(encoding="utf-8")
        pairs = re.findall(r'\{ id: "(toggle-rule-[a-z-]+)", key: "(rule_[a-z_]+_enabled)" \}', js)
        self.assertEqual({k for _, k in pairs}, self.expected())
        for element_id, _ in pairs:
            self.assertIn('id="%s"' % element_id, html)

    def test_engine_thresholds_are_editable_in_api(self):
        for key in ("alert_syslog_burst_threshold", "alert_syslog_burst_window_seconds", "alert_pve_storage_pct"):
            self.assertIn(key, api_app.DEFAULT_RULE_THRESHOLDS)
            self.assertIn(key, api_app.THRESHOLD_LIMITS)
        self.assertEqual(api_app.THRESHOLD_LIMITS["alert_syslog_burst_threshold"], (5, 10_000))
        self.assertEqual(api_app.THRESHOLD_LIMITS["alert_syslog_burst_window_seconds"], (10, 3600))
        self.assertEqual(api_app.THRESHOLD_LIMITS["alert_pve_storage_pct"], (10, 100))


class TestPutHonoursEveryToggle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        create_repository("sqlite", self.db).init_storage()
        os.environ["MININOC_ADMIN_TOKEN"] = "tok"
        self.client = TestClient(create_app(self.db))

    def tearDown(self):
        os.environ.pop("MININOC_ADMIN_TOKEN", None)
        self.tmp.cleanup()

    def test_put_saves_and_effective_settings_respect_every_toggle(self):
        keys = sorted(engine.RULE_TOGGLE_KEYS)
        resp = self.client.put("/api/settings", json={k: False for k in keys}, headers={"X-Admin-Token": "tok"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["rule_toggles"], {k: False for k in keys})
        repo = create_repository("sqlite", self.db)
        eff = engine.load_effective_settings(repo, None)
        self.assertEqual(eff["rule_toggles"], {k: False for k in keys})

    def test_put_saves_the_new_thresholds_and_rejects_out_of_range(self):
        ok = {"alert_syslog_burst_threshold": 200, "alert_syslog_burst_window_seconds": 120, "alert_pve_storage_pct": 90}
        resp = self.client.put("/api/settings", json=ok, headers={"X-Admin-Token": "tok"})
        self.assertEqual(resp.status_code, 200)
        for key, value in ok.items():
            self.assertEqual(resp.json()["rule_thresholds"][key]["value"], value)
        for key, bad in (("alert_syslog_burst_threshold", 4), ("alert_syslog_burst_window_seconds", 5), ("alert_pve_storage_pct", 101)):
            r = self.client.put("/api/settings", json={key: bad}, headers={"X-Admin-Token": "tok"})
            self.assertEqual(r.status_code, 400, key)


class TestSeverityEscalationNotifies(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = create_repository("sqlite", Path(self.tmp.name) / "t.db")
        self.repo.init_storage()
        self.repo.ensure_device("d1", "d1", "192.0.2.1")
        self.events = []
        outer = self

        class N:
            def send_transition(self, event, device_id, rule_id, target, severity, message, extra=None):
                outer.events.append((event, severity))

        p = mock.patch.object(engine, "NOTIFIER", N())
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def rec(self, sev, now):
        engine.reconcile_alert(self.repo, "d1", "pve_storage_high", "s", sev, True, now, 0, "m")

    def test_warning_to_critical_sends_a_new_firing_notice_once(self):
        self.rec("warning", 1_800_000_000)
        self.rec("warning", 1_800_000_030)
        self.rec("critical", 1_800_000_060)
        self.rec("critical", 1_800_000_090)
        self.assertEqual(self.events, [("firing", "warning"), ("firing", "critical")])

    def test_critical_to_warning_is_silent(self):
        self.rec("critical", 1_800_000_000)
        self.rec("warning", 1_800_000_030)
        self.assertEqual(self.events, [("firing", "critical")])


if __name__ == "__main__":
    unittest.main()
