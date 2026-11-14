"""Browser test: the Resources panel in Health & Inventory shows CPU, memory and load history. Optional (Playwright).

The demo database gives demo-switch host health samples (it fires host_cpu_high and host_disk_high)."""

import unittest

from .support import BrowserTestCase


class ResourcesPanelTest(BrowserTestCase):
    WITH_USER = False

    def test_three_cards_with_a_value_and_a_sparkline_for_a_device_that_reports_health(self):
        page = self.open_page()
        page.wait_for_function("document.querySelectorAll('#device-select option').length > 0")
        page.evaluate("() => { STATE.deviceId = 'demo-switch'; switchTab('tab-health'); document.getElementById('health-group-trends').open = true; }")
        page.evaluate("() => loadResources()")
        page.wait_for_function("document.querySelectorAll('#resources-cards .card-metric').length === 3")
        labels = page.evaluate("() => [...document.querySelectorAll('#resources-cards .card-metric .label')].map(e => e.textContent)")
        self.assertEqual(labels, ["CPU", "Memory", "Load (1 min)"])
        self.assertTrue(page.is_hidden("#resources-empty"))
        page.wait_for_selector("#resources-spark-0 svg")

    def test_a_device_without_data_says_so_instead_of_drawing_nothing(self):
        page = self.open_page()
        page.wait_for_function("document.querySelectorAll('#device-select option').length > 0")
        page.route("**/api/devices/*/resources*", lambda route: route.fulfill(
            json={"device_id": "x", "range": "24h", "generated_utc": 1, "as_of_utc": None, "source_status": "not_collected",
                  "empty_reason": "not_collected", "series": {}}))
        page.evaluate("() => { STATE.deviceId = 'demo-switch'; switchTab('tab-health'); document.getElementById('health-group-trends').open = true; }")
        page.evaluate("() => loadResources()")
        page.wait_for_function("!document.getElementById('resources-empty').hidden")
        self.assertIn("no SNMP profile and no agent", page.inner_text("#resources-empty"))
        self.assertEqual(page.evaluate("document.getElementById('resources-cards').children.length"), 0)


if __name__ == "__main__":
    unittest.main()
