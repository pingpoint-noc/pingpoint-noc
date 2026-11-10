"""Browser test: the Reachability & Latency panel in the Health tab (K80; one row per device since K113). Optional, needs Playwright + Chromium.

The demo database has a normal router, `demo-nas` (40 % loss) and `demo-laptop` (230 ms), both without SNMP.
"""

import unittest

from .support import BrowserTestCase

VIEWPORTS = (320, 390, 1440)


class IcmpPanelTest(BrowserTestCase):
    WITH_USER = False

    def open_health(self, width=1400):
        page = self.open_page()
        page.set_viewport_size({"width": width, "height": 900})
        page.keyboard.press("7")
        page.wait_for_function("document.querySelectorAll('#icmp-health-tbody tr').length > 0")
        return page

    def test_panel_lists_devices_in_one_row_with_loss_rtt_jitter_and_the_age(self):
        page = self.open_health()
        text = page.text_content("#icmp-health-tbody")
        self.assertIn("Demo NAS", text)
        self.assertIn("Demo laptop", text)
        self.assertIn("40.0 %", text)
        self.assertIn("230.0 ms", text)
        self.assertIn("old", text)
        self.assertIn("Source: OK", page.text_content("#icmp-health-freshness"))
        labels = page.eval_on_selector_all(
            "#icmp-health-tbody tr[data-reports] > td[data-label]", "els => els.map(e => e.dataset.label)")
        self.assertEqual(labels[:4], ["Packet loss", "Round-trip time", "Jitter", "Slowest ping"])
        devices = page.evaluate("fetch('/api/icmp-health').then(r => r.json()).then(d => d.devices.length)")
        self.assertEqual(page.locator("#icmp-health-tbody tr").count(), devices)

    def test_values_over_the_threshold_are_amber_and_the_others_are_not(self):
        page = self.open_health()
        rows = page.eval_on_selector_all(
            "#icmp-health-tbody tr[data-reports]",
            "els => els.map(r => [r.querySelector('th').firstChild.textContent.trim(), Object.fromEntries("
            "[...r.querySelectorAll('td[data-label]')].map(c => [c.dataset.label, c.className]))])",
        )
        tone = {name: cells for name, cells in rows}
        self.assertIn("text-amber", tone["Demo NAS"]["Packet loss"])
        self.assertIn("text-emerald", tone["Demo NAS"]["Round-trip time"])
        self.assertIn("text-amber", tone["Demo laptop"]["Round-trip time"])
        self.assertIn("text-emerald", tone["Demo laptop"]["Packet loss"])

    def test_the_panel_does_not_overflow_the_page_at_phone_widths(self):
        for width in VIEWPORTS:
            page = self.open_health(width)
            overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            self.assertLessEqual(overflow, 0, "horizontal scroll at %d px" % width)
            panel = page.eval_on_selector("#icmp-health-panel", "e => e.getBoundingClientRect().right")
            self.assertLessEqual(panel, width, "panel wider than the viewport at %d px" % width)
            widest = page.eval_on_selector_all(
                "#icmp-health-tbody td", "els => Math.max(...els.map(e => e.getBoundingClientRect().right))"
            )
            self.assertLessEqual(widest, width, "a cell sticks out at %d px" % width)
            page.close()


if __name__ == "__main__":
    unittest.main()
