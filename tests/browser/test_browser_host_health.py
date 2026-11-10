"""Browser test: the Device Health panel in the Health tab (K78; one row per device since K113). Optional, needs Playwright + Chromium.

The demo database has a normal router (memory 83 %, disk 20 %) and a `demo-switch` above every threshold.
"""

import unittest

from .support import BrowserTestCase

VIEWPORTS = (320, 390, 1440)


class HostHealthPanelTest(BrowserTestCase):
    WITH_USER = False

    def open_health(self, width=1400):
        page = self.open_page()
        page.set_viewport_size({"width": width, "height": 900})
        page.keyboard.press("7")
        page.wait_for_function("document.querySelectorAll('#host-health-tbody tr').length > 0")
        return page

    def test_panel_lists_each_device_in_one_row_with_cpu_memory_and_disks_and_the_age(self):
        page = self.open_health()
        text = page.text_content("#host-health-tbody")
        self.assertIn("Demo switch", text)
        self.assertIn("flash", text)
        self.assertIn("usb1", text)
        self.assertIn("main memory", text)
        self.assertIn("94.0 %", text)
        self.assertIn("old", text)
        self.assertIn("Source: OK", page.text_content("#host-health-freshness"))
        devices = page.evaluate("fetch('/api/host-health').then(r => r.json()).then(d => d.devices.length)")
        self.assertEqual(page.locator("#host-health-tbody tr").count(), devices)
        labels = page.eval_on_selector_all(
            "#host-health-tbody tr[data-reports] > td[data-label]", "els => els.map(e => e.dataset.label)")
        self.assertEqual(labels[:3], ["Source", "CPU", "Memory"])
        self.assertIn("Disks", labels)

    def test_a_device_takes_one_compact_row_on_a_desktop(self):
        page = self.open_health()
        heights = page.eval_on_selector_all(
            "#host-health-tbody tr", "els => els.map(e => e.getBoundingClientRect().height)")
        self.assertTrue(heights)
        self.assertLessEqual(max(heights), 120)

    def test_values_over_the_threshold_are_amber_and_the_others_are_not(self):
        page = self.open_health()
        tone = dict(page.eval_on_selector_all(
            "#host-health-tbody .health-disk",
            "els => els.map(e => [e.dataset.disk, e.className])",
        ))
        self.assertIn("text-amber", tone["flash"])
        self.assertIn("text-emerald", tone["usb1"])
        self.assertIn("text-emerald", tone["main memory"])

    def test_the_panel_does_not_overflow_the_page_at_phone_widths(self):
        for width in VIEWPORTS:
            page = self.open_health(width)
            overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            self.assertLessEqual(overflow, 0, "horizontal scroll at %d px" % width)
            panel = page.eval_on_selector("#host-health-panel", "e => e.getBoundingClientRect().right")
            self.assertLessEqual(panel, width, "panel wider than the viewport at %d px" % width)
            page.close()


if __name__ == "__main__":
    unittest.main()
