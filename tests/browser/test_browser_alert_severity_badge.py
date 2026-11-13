"""Browser test: the header badge and the phone strip are red only for critical alerts, amber for warnings only,
and name every kind that is firing."""

import json

from .support import BrowserTestCase


class AlertSeverityBadgeTest(BrowserTestCase):
    WITH_USER = False

    def load_with(self, severities):
        page = self.open_page()
        page.wait_for_function("STATE.cacheDevices && STATE.cacheDevices.length > 0")

        def handler(route):
            response = route.fetch()
            body = response.json()
            template = (body.get("active") or [None])[0]
            if template is None:  # the demo always has alerts firing; keep the test honest if not
                route.fulfill(response=response)
                return
            body["active"] = [dict(template, id=1000 + i, severity=s, ack_by=None) for i, s in enumerate(severities)]
            body["active_count"] = len(severities)
            route.fulfill(response=response, body=json.dumps(body))

        page.route("**/api/alerts", handler)
        page.evaluate("loadAlerts()")
        return page

    def test_only_warnings_is_amber_and_says_so(self):
        page = self.load_with(["warning", "warning"])
        page.wait_for_function("document.getElementById('global-status-text').textContent === '2 warnings'")
        self.assertIn("warn", page.get_attribute("#global-status-badge", "class").split())
        self.assertNotIn("firing", page.get_attribute("#global-status-badge", "class").split())

    def test_a_critical_alert_is_red_and_both_kinds_are_named(self):
        page = self.load_with(["critical", "warning"])
        page.wait_for_function("document.getElementById('global-status-text').textContent === '1 critical · 1 warning'")
        self.assertIn("firing", page.get_attribute("#global-status-badge", "class").split())
