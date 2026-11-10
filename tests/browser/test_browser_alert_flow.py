"""Browser test: alert -> investigate -> understand (K115, point 5). Optional (Playwright + Chromium).

The demo database fires, among others, icmp_loss_high on demo-nas, icmp_latency_high on demo-laptop and host_cpu_high /
host_disk_high on demo-switch. From the alert card, "Check first" links go to the right view with the device selected, and
the Incident Timeline says which alert it was opened for and marks the moment on the chart.
"""

import unittest

from .support import BrowserTestCase

CARD = "#active-alerts-list .alert-item-card"


class AlertToInvestigationTest(BrowserTestCase):
    WITH_USER = False

    def card_for(self, page, rule_id):
        page.wait_for_selector(CARD)
        return page.locator(CARD, has=page.locator(".alert-item-rule", has_text=rule_id)).first

    def open_guidance(self, card):
        details = card.locator("details.alert-item-guidance")
        if details.get_attribute("open") is None:
            details.locator("summary").click()
        return details

    def test_check_first_links_open_the_view_with_the_device_selected(self):
        page = self.open_page()
        card = self.card_for(page, "icmp_latency_high")
        details = self.open_guidance(card)
        link = details.locator("button.alert-check-link", has_text="Health & Inventory")
        self.assertEqual(link.count(), 1)
        link.click()
        page.wait_for_function("document.getElementById('tab-health').classList.contains('active')")
        self.assertEqual(page.input_value("#device-select"), "demo-laptop")
        page.wait_for_function(
            "(() => { const r = document.getElementById('icmp-health-panel').getBoundingClientRect();"
            " return r.top >= 0 && r.top < 400; })()")

    def test_a_syslog_link_carries_its_filter_and_the_device(self):
        page = self.open_page()
        # failed_logins is not in the demo data: use the generic wiring through a rule that links to syslog.
        page.wait_for_selector(CARD)
        page.evaluate("""() => {
          STATE.cacheActiveAlerts = [{rule_id: 'failed_logins', device_id: 'demo-switch', target: 'x', severity: 'warning',
            fired_utc: 1, message: 'm', status: 'firing', guidance: {why: 'w', check: 'c',
            links: [{view: 'syslog', anchor: 'logs-panel', q: 'login failure'}]}}];
          runAlertCheckLink('demo-switch', {view: 'syslog', anchor: 'logs-panel', q: 'login failure'});
        }""")
        page.wait_for_function("document.getElementById('tab-syslog').classList.contains('active')")
        self.assertEqual(page.input_value("#device-select"), "demo-switch")
        self.assertEqual(page.input_value("#log-search-input"), "login failure")

    def test_a_link_into_a_folded_group_opens_it(self):
        page = self.open_page()
        page.wait_for_selector(CARD)
        page.evaluate("runAlertCheckLink('edge-router-1', {view: 'health', anchor: 'capacity-panel'})")
        page.wait_for_function("document.getElementById('health-group-trends').open")
        page.wait_for_function("document.getElementById('capacity-panel').getBoundingClientRect().top < 400")

    def test_timeline_says_which_alert_it_was_opened_for_and_marks_the_moment(self):
        page = self.open_page()
        card = self.card_for(page, "host_cpu_high")
        card.locator(".alert-timeline-btn").click()
        page.wait_for_function("document.getElementById('tab-timeline').classList.contains('active')")
        origin = page.locator("#tl-origin")
        origin.wait_for(state="visible")
        text = origin.inner_text()
        self.assertIn("host_cpu_high", text)
        self.assertIn("demo-switch", text.lower())
        self.assertIn("Fired", text)
        self.assertEqual(page.input_value("#device-select"), "demo-switch")
        page.wait_for_function("document.getElementById('tl-canvas').getAttribute('aria-label').indexOf('Alert fired') !== -1")
        self.assertTrue(page.is_visible("#tl-origin-legend"))

    def test_resetting_to_live_now_clears_the_alert_origin(self):
        page = self.open_page()
        card = self.card_for(page, "host_cpu_high")
        card.locator(".alert-timeline-btn").click()
        page.locator("#tl-origin").wait_for(state="visible")
        page.click("#tl-reset-now-btn")
        page.locator("#tl-origin").wait_for(state="hidden")
        self.assertFalse(page.is_visible("#tl-origin-legend"))

    def test_opening_the_timeline_without_an_alert_shows_no_origin(self):
        page = self.open_page()
        page.keyboard.press("6")
        page.wait_for_function("document.getElementById('tab-timeline').classList.contains('active')")
        page.wait_for_timeout(500)
        self.assertFalse(page.is_visible("#tl-origin"))


if __name__ == "__main__":
    unittest.main()
