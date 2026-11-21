"""Browser test (U6): at half a screen (800 px, 701-1000 px in general) nothing scrolls sideways.

The Health tables used to need 736-944 px in a 709 px box, and the Syslog console left about 114 px to the message once the machine
column was shown. Optional (Playwright)."""

import unittest

from .support import BrowserTestCase

WIDTHS = (800, 960)
HEALTH_PANELS = ("health-panel", "host-health-panel", "fleet-panel", "coverage-panel")
LONG_TOKEN = "/var/lib/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots/" + "0123456789abcdef" * 12 + "/fs"


class HalfScreenTest(BrowserTestCase):
    WITH_USER = False

    def sideways(self, page, selector):
        return page.evaluate(
            "(s) => { const e = document.querySelector(s); return e.scrollWidth - e.clientWidth; }", selector)

    def open_health(self, width):
        page = self.open_page()
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_function("typeof switchTab === 'function'")
        page.evaluate("() => { switchTab('tab-health'); document.querySelectorAll('details.health-group').forEach(d => d.open = true); }")
        page.wait_for_function("document.querySelectorAll('#fleet-tbody tr').length > 0 && document.querySelectorAll('#host-health-tbody tr').length > 0")
        page.wait_for_timeout(500)
        return page

    def test_the_health_tables_fit_their_box(self):
        for width in WIDTHS:
            page = self.open_health(width)
            for panel in HEALTH_PANELS:
                self.assertLessEqual(self.sideways(page, "#%s .table-container" % panel), 1, "%s at %d px" % (panel, width))

    def test_a_real_device_name_is_not_cut_in_the_device_health_table(self):
        """Measured on the real NOC at 800 px: with the fixed minimum width gone, "proxmox.local" lost its last letters under the badge."""
        page = self.open_page()
        page.set_viewport_size({"width": 800, "height": 900})

        def patch(route):
            response = route.fetch()
            body = response.json()
            for device in body["devices"]:
                device["name"] = "proxmox.local"
            route.fulfill(response=response, json=body)

        page.route("**/api/host-health*", patch)
        page.wait_for_function("typeof switchTab === 'function'")
        page.evaluate("() => { switchTab('tab-health'); document.querySelectorAll('details.health-group').forEach(d => d.open = true); }")
        page.wait_for_function("document.querySelectorAll('#host-health-tbody tr th').length > 0")
        page.wait_for_timeout(400)
        self.assertEqual(
            page.evaluate("[...document.querySelectorAll('#host-health-tbody tr th')].filter(e => e.scrollWidth > e.clientWidth + 1).length"), 0)
        self.assertLessEqual(self.sideways(page, "#host-health-panel .table-container"), 1)

    def test_no_view_makes_the_page_scroll_sideways(self):
        for width in WIDTHS:
            page = self.open_page()
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_function("typeof switchTab === 'function'")
            for tab in ("overview", "bandwidth", "flows", "health", "syslog", "timeline", "topology", "settings"):
                page.evaluate("(t) => switchTab(t)", "tab-" + tab)
                page.wait_for_timeout(300)
                self.assertLessEqual(
                    page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0, "%s at %d px" % (tab, width))

    def open_syslog(self, width):
        page = self.open_page(remember_machine=False)
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_function("document.querySelectorAll('#device-select option').length > 2")
        page.wait_for_function("STATE.allMachines === true")

        def patch(route):
            response = route.fetch()
            body = response.json()
            body["events"][0]["message"] = LONG_TOKEN  # a path with no spaces is as wide as it is
            body["events"][1]["message"] = "A normal sentence that is long enough to need more than one line in a narrow message column " * 3
            route.fulfill(response=response, json=body)

        page.route("**/api/logs?*", patch)
        page.evaluate("() => switchTab('tab-syslog')")
        page.wait_for_function("document.querySelectorAll('#logs-tbody tr').length > 1")
        return page

    def test_the_syslog_console_wraps_a_long_message_instead_of_scrolling(self):
        for width in WIDTHS:
            page = self.open_syslog(width)
            self.assertLessEqual(self.sideways(page, "#logs-panel .log-console-container"), 1, "console at %d px" % width)
            self.assertGreaterEqual(page.evaluate("document.querySelector('#logs-tbody td.col-message').clientWidth"), 180)

    def test_facility_gives_way_at_half_a_screen_and_stays_on_a_wide_one(self):
        half = self.open_syslog(800)
        self.assertFalse(half.is_visible("#logs-panel thead .col-facility"))
        self.assertFalse(half.is_visible("#logs-tbody .col-facility"))
        wide = self.open_syslog(1400)
        self.assertTrue(wide.is_visible("#logs-panel thead .col-facility"))
        self.assertTrue(wide.is_visible("#logs-tbody .col-facility"))
        self.assertLessEqual(self.sideways(wide, "#logs-panel .log-console-container"), 1)


if __name__ == "__main__":
    unittest.main()
