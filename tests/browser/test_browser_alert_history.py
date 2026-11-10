"""Browser test: the alert history keeps its column header outside the scrolling rows (K124, K129). Optional.

The history has its own bounded scroll so the button that folds it stays in reach (K124). Its column header lives in a
separate strip above the rows, so the scroll bar runs only next to the rows and the header never moves; the real <thead>
stays in the table, hidden from the eye, for screen readers.
"""

import json
import unittest

from .support import BrowserTestCase

HEAD = "#alert-history-box .alert-history-head"
BODY = "#alert-history-box .alert-history-body"


class AlertHistoryWindowTest(BrowserTestCase):
    WITH_USER = False

    def open_history(self):
        context = self.browser.new_context(color_scheme="dark", viewport={"width": 1400, "height": 900})
        self.addCleanup(context.close)

        def alerts(route):
            response = route.fetch()
            data = response.json()
            base = (data.get("history") or [{}])[0] if data.get("history") else {}
            rows = []
            for i in range(60):
                rows.append({
                    "device_id": base.get("device_id", "edge-router-1"), "rule_id": "high_bandwidth", "target": "if:%d" % (i % 7),
                    "severity": "warning", "status": "resolved", "fired_utc": 1_794_000_000 - i * 600,
                    "resolved_utc": 1_794_000_300 - i * 600, "message": "Interface rate %d bit/s exceeds 10000000 bit/s" % (i * 1000),
                })
            data["history"] = rows
            route.fulfill(response=response, body=json.dumps(data), headers={"content-type": "application/json"})

        context.route("**/api/alerts", alerts)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(self.server.base_url + "/")
        page.wait_for_selector("#toggle-alert-history-btn")
        page.click("#toggle-alert-history-btn")
        page.wait_for_selector("#alert-history-box:not(.hidden)")
        page.wait_for_function("document.querySelectorAll('#alert-history-tbody tr').length >= 40")
        page.wait_for_timeout(300)
        return page

    def test_the_rows_are_bounded_and_the_hide_button_stays_on_screen_while_reading_them(self):
        page = self.open_history()
        box = page.evaluate(
            """(() => {
              const c = document.querySelector('%s'); const s = getComputedStyle(c);
              return { maxHeight: s.maxHeight, overflowY: s.overflowY, height: c.getBoundingClientRect().height,
                       scrolls: c.scrollHeight > c.clientHeight, viewport: window.innerHeight };
            })()""" % BODY)
        self.assertNotEqual(box["maxHeight"], "none")
        self.assertEqual(box["overflowY"], "auto")
        self.assertTrue(box["scrolls"], "sixty rows must need the scroll")
        self.assertLess(box["height"], box["viewport"] * 0.7)
        page.evaluate("document.querySelector('%s').scrollTop = 99999" % BODY)
        page.wait_for_timeout(200)
        rect = page.evaluate(
            "(() => { const r = document.getElementById('toggle-alert-history-btn').getBoundingClientRect();"
            " return { top: r.top, bottom: r.bottom, viewport: window.innerHeight,"
            " label: document.getElementById('toggle-alert-history-btn').textContent.trim() }; })()")
        self.assertGreaterEqual(rect["top"], 0)
        self.assertLessEqual(rect["bottom"], rect["viewport"])
        self.assertEqual(rect["label"], "Hide Alert History")

    def test_the_header_sits_above_the_scroller_and_does_not_move_with_the_rows(self):
        page = self.open_history()
        geometry = """() => { const th = document.querySelector('%s th').getBoundingClientRect();
          const head = document.querySelector('%s').getBoundingClientRect(); const body = document.querySelector('%s').getBoundingClientRect();
          return { thTop: th.top, headBottom: head.bottom, bodyTop: body.top,
                   inside: !!document.querySelector('%s').closest('%s') }; }""" % (HEAD, HEAD, BODY, HEAD, BODY)
        before = page.evaluate(geometry)
        self.assertFalse(before["inside"])
        self.assertLessEqual(before["headBottom"], before["bodyTop"] + 1, "the scroller starts below the header")
        page.evaluate("document.querySelector('%s').scrollTop = 99999" % BODY)
        page.wait_for_timeout(200)
        after = page.evaluate(geometry)
        self.assertAlmostEqual(before["thTop"], after["thTop"], delta=0.5)

    def test_the_header_columns_line_up_with_the_rows(self):
        page = self.open_history()
        lefts = page.evaluate(
            """() => ({ head: [...document.querySelectorAll('%s th')].map(t => t.getBoundingClientRect().left),
                         row: [...document.querySelector('#alert-history-tbody tr').children].map(t => t.getBoundingClientRect().left) })""" % HEAD)
        self.assertEqual(len(lefts["head"]), 6)
        self.assertEqual(len(lefts["row"]), 6)
        for head, row in zip(lefts["head"], lefts["row"]):
            self.assertAlmostEqual(head, row, delta=1.5)

    def test_screen_readers_still_get_the_real_header(self):
        page = self.open_history()
        self.assertEqual(page.get_attribute(HEAD, "aria-hidden"), "true")
        names = page.evaluate("[...document.querySelectorAll('%s thead th')].map(t => t.textContent.replace(/\s+/g, ' ').trim())" % BODY)
        self.assertEqual(len(names), 6)
        self.assertTrue(names[0].startswith("Rule"))


if __name__ == "__main__":
    unittest.main()
