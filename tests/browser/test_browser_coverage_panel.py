"""Browser test: the Telemetry Coverage table in Health & Inventory. Optional (Playwright)."""

import unittest

from .support import BrowserTestCase

COVERAGE = {
    "generated_utc": 1, "sources": ["snmp", "agent", "syslog", "ipfix", "icmp", "dns", "proxmox"],
    "how": {"snmp": "python -m tools.add_device --id {id} --ip {ip} --snmp", "agent": "curl -fsS \"{noc}/api/bootstrap.sh\" | sudo bash",
            "syslog": "x", "ipfix": "x", "icmp": "x", "dns": "x", "proxmox": "x"},
    "devices": [{"id": "gw", "name": "Gateway", "ip": "192.0.2.1", "device_type": "router",
                 "cells": {"snmp": "missing", "agent": "n/a", "syslog": "ok", "ipfix": "n/a", "icmp": "down", "dns": "n/a", "proxmox": "n/a"},
                 "missing": ["snmp"]},
                {"id": "srv", "name": "Server", "ip": "192.0.2.2", "device_type": "linux",
                 "cells": {"snmp": "ok", "agent": "missing", "syslog": "stale", "ipfix": "n/a", "icmp": "ok", "dns": "n/a", "proxmox": "n/a"},
                 "missing": ["agent"]}],
}


class CoveragePanelTest(BrowserTestCase):
    WITH_USER = False

    def open_coverage(self):
        page = self.open_page()
        page.wait_for_function("document.querySelectorAll('#device-select option').length > 0")
        page.route("**/api/coverage", lambda route: route.fulfill(json=COVERAGE))
        page.evaluate("() => { switchTab('tab-health'); document.getElementById('health-group-trends').open = true; }")
        page.evaluate("() => loadCoverage()")
        page.wait_for_function("document.querySelectorAll('#coverage-tbody tr').length === 2")
        return page

    def test_one_row_per_device_and_a_button_for_each_gap(self):
        page = self.open_coverage()
        self.assertEqual(page.evaluate("document.querySelectorAll('#coverage-thead th').length"), 8)
        self.assertEqual(page.evaluate("document.querySelectorAll('#coverage-tbody .coverage-gap').length"), 2)
        self.assertTrue(page.is_hidden("#coverage-how"))

    def test_clicking_a_gap_shows_the_command_with_this_noc_and_the_device(self):
        page = self.open_coverage()
        page.click("#coverage-tbody .coverage-gap[data-device='srv']")
        text = page.inner_text("#coverage-how")
        origin = page.evaluate("window.location.origin")
        self.assertIn(origin + "/api/bootstrap.sh", text)
        self.assertNotIn("{noc}", text)
        page.click("#coverage-tbody .coverage-gap[data-device='gw']")
        self.assertIn("--id gw --ip 192.0.2.1", page.inner_text("#coverage-how"))


if __name__ == "__main__":
    unittest.main()
