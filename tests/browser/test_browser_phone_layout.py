"""Browser test: the phone layout (K116, point 6 of the interface backlog). Optional (Playwright + Chromium).

At 390 px: a header that leaves the content close to the top, the alert list folded outside Overview, and an Overview
that goes alerts, devices, indicators, chart and then folded panels. A desktop must not change.
"""

import unittest

from .support import BrowserTestCase

PHONE = {"width": 390, "height": 844}
FOLDS = ("overview-top-hosts-list", "overview-logs-list", "overview-top-if-table")

TOP = "sel => { const r = document.querySelector(sel).getBoundingClientRect(); return r.top + scrollY; }"
CONTROLS = ("device-select", "refresh-btn", "pause-btn", "theme-toggle", "nav-menu-btn")


def shown(page, element_id):
    """Not display:none (an empty list is "not visible" to Playwright, which is not what is checked here)."""
    return page.evaluate("id => getComputedStyle(document.getElementById(id)).display !== 'none'", element_id)


class PhoneLayoutTest(BrowserTestCase):
    WITH_USER = False

    def phone(self):
        context = self.browser.new_context(viewport=PHONE)
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(self.server.base_url)
        page.wait_for_selector("#active-alerts-list .alert-item-card")
        return page

    def test_the_header_leaves_the_content_near_the_top(self):
        page = self.phone()
        # Was 300 px (strip 36 + header 251 + gap) before K116.
        self.assertLessEqual(page.evaluate(TOP, "#alerts-section"), 235)

    def test_every_header_control_is_still_on_screen_and_the_page_does_not_scroll_sideways(self):
        page = self.phone()
        for control in CONTROLS:
            box = page.evaluate(
                "id => { const r = document.getElementById(id).getBoundingClientRect();"
                " return [r.left, r.right, r.width, r.height]; }", control)
            self.assertGreaterEqual(box[0], 0, control)
            self.assertLessEqual(box[1], 390, control)
            self.assertGreater(box[2], 0, control)
        self.assertEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
        self.assertTrue(page.is_visible(".clock-display"))
        self.assertTrue(page.is_visible("#range-select ~ *, .time-range-toggle"))
        self.assertIn("Refresh", page.inner_text("#refresh-btn") + page.text_content("#refresh-btn"))

    def test_outside_overview_the_alert_list_is_folded_until_asked(self):
        page = self.phone()
        self.assertTrue(shown(page, "active-alerts-list"))
        self.assertFalse(page.is_visible("#alerts-fold-btn"))
        page.locator("#nav-menu-btn").click()
        page.locator("button[data-tab='tab-syslog']").click()
        page.wait_for_function("document.getElementById('tab-syslog').classList.contains('active')")
        self.assertFalse(shown(page, "active-alerts-list"))
        self.assertLessEqual(page.evaluate("document.getElementById('alerts-section').getBoundingClientRect().height"), 130)
        self.assertEqual(page.get_attribute("#alerts-fold-btn", "aria-expanded"), "false")
        page.click("#alerts-fold-btn")
        self.assertTrue(shown(page, "active-alerts-list"))
        self.assertEqual(page.get_attribute("#alerts-fold-btn", "aria-expanded"), "true")
        page.click("#alerts-fold-btn")
        self.assertFalse(shown(page, "active-alerts-list"))

    def test_overview_goes_alerts_devices_indicators_chart_and_folds_the_rest(self):
        page = self.phone()
        order = [page.evaluate(TOP, i) for i in ("#alerts-section", "#welcome", ".kpi-row", "#overview-chart")]
        self.assertEqual(order, sorted(order))
        # twelve indicators were stacked one per line (789 px); two columns now
        self.assertLessEqual(page.evaluate("document.querySelector('.kpi-row').getBoundingClientRect().height"), 520)
        for fold in FOLDS:
            self.assertFalse(shown(page, fold), fold + " should start folded on a phone")
        for fold in FOLDS:
            button = page.locator("button.phone-fold-btn[data-fold='%s']" % fold)
            self.assertEqual(button.get_attribute("aria-expanded"), "false", fold)
            button.click()
            self.assertTrue(shown(page, fold), fold)
            self.assertEqual(button.get_attribute("aria-expanded"), "true", fold)

    def test_a_desktop_is_unchanged(self):
        context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(self.server.base_url)
        page.wait_for_selector("#active-alerts-list .alert-item-card")
        self.assertLessEqual(page.evaluate(TOP, "#alerts-section"), 120)
        self.assertFalse(page.is_visible("#alerts-fold-btn"))
        for fold in FOLDS:
            self.assertFalse(page.is_visible("button.phone-fold-btn[data-fold='%s']" % fold), fold)
        self.assertTrue(shown(page, "overview-top-hosts-list"))
        self.assertTrue(shown(page, "overview-logs-list"))
        page.keyboard.press("5")
        page.wait_for_function("document.getElementById('tab-syslog').classList.contains('active')")
        self.assertTrue(shown(page, "active-alerts-list"))


if __name__ == "__main__":
    unittest.main()
