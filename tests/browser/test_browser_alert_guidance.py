"""Browser test: an alert's "Why it matters" is opened or folded once, when the alert first appears (K128). Optional.

With one or two alerts on a wide screen it starts open, with more it starts folded. That default used to be recomputed on
every refresh from the number of alerts at that moment, so a third alert folded the two that were open and the page moved
under the reader. Now the default is decided when each alert is first seen; what the person opens or folds always wins.
"""

import json
import unittest

from .support import BrowserTestCase

CARD = "#active-alerts-list .alert-item-card"
OPEN = "document.querySelectorAll('%s details.alert-item-guidance')" % CARD


class AlertGuidanceDefaultTest(BrowserTestCase):
    WITH_USER = False

    def open_with_alert_limit(self, limit):
        context = self.browser.new_context(color_scheme="dark", viewport={"width": 1400, "height": 900})
        self.addCleanup(context.close)

        def alerts(route):
            response = route.fetch()
            data = response.json()
            if limit["n"] is not None:
                data["active"] = data["active"][: limit["n"]]
                data["active_count"] = len(data["active"])
            route.fulfill(response=response, body=json.dumps(data), headers={"content-type": "application/json"})

        context.route("**/api/alerts", alerts)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(self.server.base_url + "/")
        return page

    def states(self, page):
        return page.evaluate("[...%s].map(d => d.open)" % OPEN)

    def test_a_third_alert_does_not_fold_the_two_that_were_open(self):
        limit = {"n": 2}
        page = self.open_with_alert_limit(limit)
        page.wait_for_function("document.querySelectorAll('%s').length === 2" % CARD)
        self.assertEqual(self.states(page), [True, True])
        limit["n"] = None
        page.click("#refresh-btn")
        page.wait_for_function("document.querySelectorAll('%s').length >= 3" % CARD)
        states = self.states(page)
        self.assertGreaterEqual(len(states), 3)
        self.assertEqual(states[:2], [True, True], "the two that were open stay open")
        self.assertTrue(all(s is False for s in states[2:]), "the ones that arrive into a longer list start folded")

    def test_when_alerts_resolve_the_ones_left_do_not_unfold_by_themselves(self):
        limit = {"n": 3}
        page = self.open_with_alert_limit(limit)
        page.wait_for_function("document.querySelectorAll('%s').length === 3" % CARD)
        self.assertEqual(self.states(page), [False, False, False])
        limit["n"] = 2
        page.click("#refresh-btn")
        page.wait_for_function("document.querySelectorAll('%s').length === 2" % CARD)
        page.wait_for_timeout(800)
        self.assertEqual(self.states(page), [False, False], "cards that started folded stay folded")

    def test_what_the_person_folds_stays_folded_after_a_refresh(self):
        limit = {"n": 2}
        page = self.open_with_alert_limit(limit)
        page.wait_for_function("document.querySelectorAll('%s').length === 2" % CARD)
        page.locator(CARD + " details.alert-item-guidance summary").first.click()
        self.assertEqual(self.states(page), [False, True])
        page.click("#refresh-btn")
        page.wait_for_timeout(1200)
        self.assertEqual(self.states(page), [False, True])


if __name__ == "__main__":
    unittest.main()
