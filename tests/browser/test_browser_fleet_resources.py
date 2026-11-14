"""Browser test: Resources by Machine lists every machine, sorts by column and says what is missing. Optional (Playwright)."""

import unittest

from .support import BrowserTestCase


def row(name, kind, cpu, mem, disk, **extra):
    data = {"key": "k-" + name, "name": name, "kind": kind, "status": "running", "cpu_pct": cpu, "mem_pct": mem, "disk_pct": disk,
            "mem_used_bytes": 512 * 1024 * 1024, "mem_total_bytes": 1024 * 1024 * 1024, "cpu_series": [1, 20, 5, 40], "mem_series": [30, 31],
            "source": "proxmox", "device_id": None, "vmid": None, "vcpus": 2, "hypervisor_id": None}
    data.update(extra)
    return data


FLEET = {"range": "24h", "generated_utc": 1, "summary": {"machines": 3, "running": 3}, "rows": [
    row("db", "ct", 40.0, 25.0, 61.0, vmid=101),
    row("web", "host", 95.0, 80.0, None, source="agent", device_id="web"),
    row("idle", "vm", 0.4, 10.0, None, vmid=102),
]}


class FleetResourcesTest(BrowserTestCase):
    WITH_USER = False

    def open_panel(self, payload):
        page = self.open_page()
        page.wait_for_function("typeof switchTab === 'function'")
        page.route("**/api/resources/fleet*", lambda route: route.fulfill(json=payload))
        page.evaluate("() => { switchTab('tab-health'); document.getElementById('health-group-trends').open = true; }")
        return page

    def names(self, page):
        return page.evaluate("() => [...document.querySelectorAll('#fleet-tbody th strong')].map(e => e.textContent)")

    def test_rows_arrive_busiest_cpu_first_with_a_figure_a_bar_and_a_miniature(self):
        page = self.open_panel(FLEET)
        page.wait_for_function("document.querySelectorAll('#fleet-tbody tr').length === 3")
        self.assertEqual(self.names(page), ["web", "db", "idle"])
        self.assertEqual(page.evaluate("document.querySelectorAll('#fleet-tbody .fleet-spark').length > 0"), True)
        self.assertIn("95 %", page.inner_text("#fleet-tbody"))
        self.assertIn("Not monitored", page.inner_text("#fleet-tbody"))
        self.assertTrue(page.is_visible("#fleet-tbody .fleet-open"))

    def test_a_click_on_a_column_sorts_and_a_second_click_reverses(self):
        page = self.open_panel(FLEET)
        page.wait_for_function("document.querySelectorAll('#fleet-tbody tr').length === 3")
        page.click(".fleet-sort[data-sort='name']")
        self.assertEqual(self.names(page), ["db", "idle", "web"])
        page.click(".fleet-sort[data-sort='name']")
        self.assertEqual(self.names(page), ["web", "idle", "db"])
        page.click(".fleet-sort[data-sort='mem']")
        self.assertEqual(self.names(page), ["web", "db", "idle"])

    def test_no_machine_says_how_to_get_one(self):
        page = self.open_panel({"range": "24h", "generated_utc": 1, "rows": [], "summary": {"machines": 0, "running": 0}})
        page.wait_for_function("!document.getElementById('fleet-empty').hidden")
        self.assertIn("enrol a host", page.inner_text("#fleet-empty"))

    def test_on_a_phone_the_panel_has_no_horizontal_scroll(self):
        page = self.open_panel(FLEET)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_function("document.querySelectorAll('#fleet-tbody tr').length === 3")
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)


if __name__ == "__main__":
    unittest.main()
