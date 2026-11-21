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

    # --- G20: the Disk health column --------------------------------------------------------------------------------------

    HEALTH = {
        "total": 3, "failing": 0, "max_temp_c": 41, "as_of_utc": 1,
        "items": [
            {"label": "WDC WD10EZEX SN-1", "kind": "disk", "ok": True, "state": "", "temp_c": 33, "wear_pct": None, "bad_blocks": 0},
            {"label": "KINGSTON NVMe SN-2", "kind": "disk", "ok": True, "state": "", "temp_c": 41, "wear_pct": 7, "bad_blocks": 1},
            {"label": "tank", "kind": "zfs", "ok": True, "state": "healthy", "temp_c": None, "wear_pct": None, "bad_blocks": None},
        ],
    }
    FAILING = dict(HEALTH, failing=1, items=[
        {"label": "tank", "kind": "zfs", "ok": False, "state": "degraded", "temp_c": None, "wear_pct": None, "bad_blocks": None}] + HEALTH["items"][:2])

    def with_health(self, **by_name):
        rows = []
        for r in FLEET["rows"]:
            rows.append(dict(r, disk_health=by_name.get(r["name"])))
        return dict(FLEET, rows=rows)

    def test_the_column_is_hidden_while_no_machine_reports_disk_health(self):
        page = self.open_panel(FLEET)
        page.wait_for_function("document.querySelectorAll('#fleet-tbody tr').length === 3")
        self.assertTrue(page.evaluate("document.getElementById('fleet-health-th').hidden"))
        self.assertEqual(page.evaluate("document.querySelectorAll('#fleet-tbody td[data-label=\"Disk health\"]').length"), 0)

    def test_a_machine_with_disk_health_shows_a_summary_a_tooltip_and_the_others_a_dash(self):
        page = self.open_panel(self.with_health(web=self.HEALTH))
        page.wait_for_function("document.querySelectorAll('#fleet-tbody tr').length === 3")
        self.assertFalse(page.evaluate("document.getElementById('fleet-health-th').hidden"))
        cells = page.evaluate("[...document.querySelectorAll('#fleet-tbody tr')].map(tr => [tr.querySelector('th strong').textContent, "
                              "tr.querySelector('td[data-label=\"Disk health\"]').innerText])")
        by_name = {name: text for name, text in cells}
        self.assertIn("OK", by_name["web"])
        self.assertIn("3 disks", by_name["web"])
        self.assertIn("41", by_name["web"])
        self.assertEqual(by_name["db"].strip(), "—")
        tip = page.get_attribute("#fleet-tbody tr:first-child td[data-label='Disk health']", "title")
        self.assertIn("KINGSTON NVMe SN-2", tip)
        self.assertIn("wear 7 %", tip)
        self.assertIn("tank", tip)

    def test_a_failing_pool_is_flagged_and_named_in_the_tooltip(self):
        page = self.open_panel(self.with_health(web=self.FAILING))
        page.wait_for_function("document.querySelectorAll('#fleet-tbody tr').length === 3")
        cell = "#fleet-tbody tr:first-child td[data-label='Disk health']"
        self.assertIn("1 failing", page.inner_text(cell))
        self.assertIn("fleet-crit", page.get_attribute(cell, "class"))
        self.assertIn("tank: degraded", page.get_attribute(cell, "title"))

    def test_the_column_sorts_the_failing_machines_first(self):
        page = self.open_panel(self.with_health(db=self.FAILING, idle=self.HEALTH))
        page.wait_for_function("document.querySelectorAll('#fleet-tbody tr').length === 3")
        page.click(".fleet-sort[data-sort='health']")
        self.assertEqual(self.names(page), ["db", "idle", "web"])

    def test_on_a_phone_the_panel_has_no_horizontal_scroll_with_disk_health(self):
        page = self.open_panel(self.with_health(web=self.FAILING, db=self.HEALTH))
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_function("document.querySelectorAll('#fleet-tbody tr').length === 3")
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)

    def test_on_a_phone_the_panel_has_no_horizontal_scroll(self):
        page = self.open_panel(FLEET)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_function("document.querySelectorAll('#fleet-tbody tr').length === 3")
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)


if __name__ == "__main__":
    unittest.main()
