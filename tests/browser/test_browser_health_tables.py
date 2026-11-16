"""Browser test: the Collector Health and Device Health tables keep short rows at half-screen widths. Optional (Playwright).

With real data a collector error is a long sentence and a host lists a mount point per container volume; the tables let both push
the other columns out (the last column was cut and a row grew to six lines). Fixed column shares, a two-line error and three-line
disks keep the rows short, with the whole text in the tooltip."""

import unittest

from .support import BrowserTestCase

LONG_ERROR = ("HTTP 500 error from https://192.0.2.69:8006/api2/json/nodes/pve1/status: hostname lookup 'pve1' failed - failed to get "
              "address info for: pve1: Name or service not known and then some more words to make it long enough to need three lines")


def collector(name, device, error=None):
    return {"collector_name": name, "device_id": device, "status": "ok", "last_success_utc": 1_800_000_000, "age_seconds": 15,
            "consecutive_errors": 0, "last_error_msg": error, "last_error_utc": 1_799_990_000 if error else None}


HEALTH = {"status": "ok", "as_of_utc": 1_800_000_000, "db_size_bytes": 300_000_000,
          "table_rows": {"interface_samples": 1, "syslog_events": 2, "flows": 3, "alerts": 4},
          "collectors": [collector("icmp", "apps"), collector("proxmox", "pve1", LONG_ERROR), collector("syslog", "infra-core")]}

SERVICES = [{"service_name": name, "status": "active", "description": name} for name in ("cron", "docker", "rsyslog", "ssh", "sshd")]
MOUNTS = ["/", "/audit", "/boot/grub", "/conf", "/data", "/etc", "/home", "/mnt", "/mnt/storage", "/mnt/storage/NPC-server", "/opt", "/root",
          "/tmp", "/usr", "/var", "/var/db/system", "/var/db/system/configs-ae32c386e13840b2bf9c0083275e7941", "/var/db/system/cores",
          "/var/db/system/netdata", "/var/log", "/var/log/journal"]


def device(name, disks, services):
    return {"name": name, "reports_health": True, "source": "telemetry", "cpu_pct": 7.4, "memory_pct": 30.4, "status": "ok",
            "disks": [{"label": label, "used_pct": 12.3} for label in disks], "services": services, "age_seconds": 5, "reason": ""}


HOST_HEALTH = {"source_status": "ok", "as_of_utc": 1_800_000_000, "thresholds": {"cpu_pct": 90, "memory_pct": 90, "disk_pct": 90},
               "devices": [device("apps", ["/"], SERVICES), device("truenas", MOUNTS, []), device("noc", ["/", "/tmp"], SERVICES)],
               "empty_reason": ""}


class HealthTablesTest(BrowserTestCase):
    WITH_USER = False

    def show(self, width):
        context = self.browser.new_context(viewport={"width": width, "height": 900})
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.route("**/api/health", lambda route: route.fulfill(json=HEALTH))
        page.route("**/api/host-health", lambda route: route.fulfill(json=HOST_HEALTH))
        page.goto(self.server.base_url)
        page.wait_for_function("typeof switchTab === 'function'")
        page.evaluate("() => switchTab('tab-health')")
        page.wait_for_function("document.querySelectorAll('#collectors-tbody tr').length === 3 && document.querySelectorAll('#host-health-tbody tr').length === 3")
        return page

    def heights(self, page, selector):
        heights = page.evaluate("(sel) => [...document.querySelectorAll(sel)].map(r => r.getBoundingClientRect().height)", selector)
        self.assertTrue(all(h > 20 for h in heights), "not on screen: %r" % heights)  # a hidden row measures 0
        return heights

    def test_nothing_scrolls_sideways_and_rows_stay_short_at_half_screen(self):
        for width in (1400, 960):
            page = self.show(width)
            for table in (".table-collectors", ".table-host-health"):
                fits = page.evaluate("(sel) => { const c = document.querySelector(sel).closest('.table-container'); return [c.scrollWidth, c.clientWidth]; }", table)
                self.assertLessEqual(fits[0], fits[1] + 1, "%d px %s: %r" % (width, table, fits))
            self.assertLessEqual(max(self.heights(page, "#collectors-tbody tr")), 75, width)
            self.assertLessEqual(max(self.heights(page, "#host-health-tbody tr")), 95, width)

    def test_a_long_error_is_cut_at_two_lines_and_the_tooltip_has_it_all(self):
        page = self.show(960)
        cell = page.locator("#collectors-tbody tr:nth-child(2) .cell-clamp")
        self.assertIn("Name or service not known", cell.get_attribute("title"))
        lines = page.evaluate("""() => { const e = document.querySelector('#collectors-tbody tr:nth-child(2) .cell-clamp');
          return Math.round(e.getBoundingClientRect().height / parseFloat(getComputedStyle(e).lineHeight)); }""")
        self.assertLessEqual(lines, 2)

    def test_the_services_share_a_row_instead_of_one_per_line(self):
        page = self.show(960)
        tops = page.evaluate("""() => new Set([...document.querySelectorAll('#host-health-tbody tr:first-child td:nth-child(6) .badge')]
          .map(b => Math.round(b.getBoundingClientRect().top))).size""")
        self.assertLessEqual(tops, 2, "the five services are on %d lines" % tops)

    def test_numbers_stay_on_one_line(self):
        page = self.show(960)
        lines = page.evaluate("""() => [3, 4, 7].map(n => { const r = document.createRange();
          r.selectNodeContents(document.querySelector('#host-health-tbody tr:first-child td:nth-child(' + n + ')'));
          return new Set([...r.getClientRects()].map(x => Math.round(x.top))).size; })""")
        self.assertEqual(lines, [1, 1, 1])

    def test_a_phone_still_gets_cards_with_the_whole_error(self):
        page = self.show(390)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
        shown = page.evaluate("""() => { const e = document.querySelector('#collectors-tbody tr:nth-child(2) .cell-clamp');
          return e.scrollHeight <= e.clientHeight + 1; }""")
        self.assertTrue(shown, "the card cuts the error")


if __name__ == "__main__":
    unittest.main()
