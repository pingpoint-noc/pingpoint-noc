"""Browser test: the alert banner shows its explanation open only on the Overview (it used to push every view down by ~300 px). Optional (Playwright)."""

import json
import unittest

from .support import BrowserTestCase


class AlertGuidanceFoldTest(BrowserTestCase):
    WITH_USER = False

    def one_alert(self, page):
        def handler(route):
            data = route.fetch().json()
            data["active"] = data["active"][:1]
            data["active_count"] = len(data["active"])
            route.fulfill(body=json.dumps(data), content_type="application/json")
        page.route("**/api/alerts", handler)

    def guidance_open(self, page):
        page.wait_for_selector("#active-alerts-list .alert-item-card details.alert-item-guidance")
        return page.evaluate("document.querySelector('#active-alerts-list details.alert-item-guidance').open")

    def test_a_single_alert_starts_open_on_the_overview(self):
        page = self.open_page()
        self.one_alert(page)
        page.reload()
        self.assertTrue(self.guidance_open(page))

    def test_a_single_alert_starts_folded_on_any_other_view(self):
        page = self.open_page(path="/#/syslog")
        self.one_alert(page)
        page.reload()
        page.wait_for_function("document.getElementById('tab-syslog').classList.contains('active')")
        self.assertFalse(self.guidance_open(page))


if __name__ == "__main__":
    unittest.main()
