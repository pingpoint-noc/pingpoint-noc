"""Browser test: the first screen of Overview (K112, point 1 of the interface backlog). Optional (Playwright + Chromium).

The demo database has several firing alerts, so the alerts banner is tall; what is checked is everything below it:
the greeting is one line that does not repeat the alert count, the NOC and the target indicators share one row on a
desktop, and the chart and the top talkers start right under them.
"""

import re
import unittest

from .support import BrowserTestCase

BOXES = """() => {
  const box = (selector) => {
    const el = document.querySelector(selector);
    if (!el) { return null; }
    const r = el.getBoundingClientRect();
    return { top: r.top + scrollY, bottom: r.bottom + scrollY, left: r.left, right: r.right, height: r.height };
  };
  const out = {};
  for (const selector of ['#alerts-section', '#welcome', '#welcome-title', '#welcome-status', '#welcome-toggle',
      '#welcome-devices', '.noc-global-ribbon', '.target-device-ribbon', '.overview-chart-panel',
      '.overview-side-stack > .panel']) {
    out[selector] = box(selector);
  }
  out.scrollWidth = document.documentElement.scrollWidth;
  out.innerWidth = innerWidth;
  out.clientWidth = document.documentElement.clientWidth;
  return out;
}"""


class OverviewFirstScreenTest(BrowserTestCase):
    WITH_USER = False

    def boxes(self, page):
        page.wait_for_selector("#welcome-status:not(:has-text('Checking'))")
        page.wait_for_selector("#active-alerts-list > *")
        return page.evaluate(BOXES)

    def test_the_greeting_does_not_repeat_the_alert_count(self):
        # The count is already in the header badge, the banner and the Incident Alarms indicator.
        page = self.open_page()
        self.boxes(page)
        text = page.inner_text("#welcome-status")
        self.assertIn("Last check", text)
        self.assertNotRegex(text, r"\d+ alerts? (is|are) firing")
        self.assertNotIn("Latest:", text)

    def test_the_greeting_is_one_line_on_a_desktop(self):
        page = self.open_page()
        b = self.boxes(page)
        title, status, toggle = b["#welcome-title"], b["#welcome-status"], b["#welcome-toggle"]
        self.assertLess(abs((title["top"] + title["bottom"]) - (status["top"] + status["bottom"])), 14,
                        "title and status sentence must share one line")
        self.assertLess(abs((title["top"] + title["bottom"]) - (toggle["top"] + toggle["bottom"])), 14)
        # one greeting line plus one row of device pills
        self.assertLessEqual(b["#welcome"]["height"], 110)

    def test_noc_and_target_indicators_share_one_row_on_a_desktop(self):
        page = self.open_page()
        b = self.boxes(page)
        noc, target = b[".noc-global-ribbon"], b[".target-device-ribbon"]
        self.assertLess(abs(noc["top"] - target["top"]), 3, "the two indicator strips must start on the same row")
        self.assertLess(noc["right"], target["left"] + 1)
        self.assertLessEqual(target["right"], b["clientWidth"])
        self.assertLessEqual(b["scrollWidth"], b["clientWidth"])
        # every indicator of both strips sits on that row
        tops = page.evaluate(
            "[...document.querySelectorAll('.kpi-ribbon .kpi-card:not(.hidden)')]"
            ".map(el => Math.round(el.getBoundingClientRect().top + scrollY))"
        )
        self.assertGreaterEqual(len(tops), 6)
        self.assertLess(max(tops) - min(tops), 3)

    def test_chart_and_top_talkers_start_right_under_the_indicators(self):
        page = self.open_page()
        b = self.boxes(page)
        chart, talkers = b[".overview-chart-panel"], b[".overview-side-stack > .panel"]
        indicators_bottom = max(b[".noc-global-ribbon"]["bottom"], b[".target-device-ribbon"]["bottom"])
        self.assertLessEqual(chart["top"] - indicators_bottom, 40)
        self.assertLess(abs(chart["top"] - talkers["top"]), 3)
        # Below the banner the greeting, the indicators and the chart header take at most this much (was 465 px).
        self.assertLessEqual(chart["top"] - b["#alerts-section"]["bottom"], 330)

    def test_every_target_keeps_the_row_inside_the_page(self):
        page = self.open_page()
        self.boxes(page)
        ids = page.evaluate("[...document.querySelectorAll('#device-select option')].map(o => o.value)")
        self.assertGreaterEqual(len(ids), 3)
        for device_id in ids:
            page.select_option("#device-select", device_id)
            page.wait_for_timeout(700)
            width = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
            self.assertLessEqual(width[0], width[1], "horizontal overflow with target " + device_id)
            right = page.evaluate(
                "Math.max(...[...document.querySelectorAll('.kpi-ribbon .kpi-card:not(.hidden)')]"
                ".map(el => el.getBoundingClientRect().right))"
            )
            self.assertLessEqual(right, width[1], "indicator outside the page with target " + device_id)

    def test_a_phone_keeps_one_column_without_sideways_scroll(self):
        context = self.browser.new_context(viewport={"width": 390, "height": 844})
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(self.server.base_url)
        b = self.boxes(page)
        self.assertLessEqual(b["scrollWidth"], b["clientWidth"])
        noc, target = b[".noc-global-ribbon"], b[".target-device-ribbon"]
        self.assertLessEqual(noc["bottom"], target["top"])
        self.assertLessEqual(b["#welcome"]["bottom"], noc["top"])
        self.assertTrue(re.search(r"Last check", page.inner_text("#welcome-status")))


class AlertCountIsNotRepeatedTest(BrowserTestCase):
    WITH_USER = False

    def test_a_phone_shows_the_firing_count_once_at_the_top(self):
        # The sticky strip says "7 alerts firing"; the header badge right under it said it again.
        context = self.browser.new_context(viewport={"width": 390, "height": 844})
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(self.server.base_url)
        page.wait_for_selector("#alert-strip:not(.hidden)")
        self.assertTrue(page.is_visible("#alert-strip"))
        self.assertFalse(page.is_visible("#global-status-badge"))

    def test_a_desktop_keeps_the_header_badge(self):
        page = self.open_page()
        page.wait_for_selector("#active-alerts-list > *")
        self.assertTrue(page.is_visible("#global-status-badge"))
        self.assertFalse(page.is_visible("#alert-strip"))

    def test_the_alert_list_scrolls_with_its_bar_and_no_fade_over_the_cards(self):
        page = self.open_page()
        page.wait_for_selector("#active-alerts-list > *")
        for pseudo in ("::before", "::after"):
            content = page.evaluate(
                "getComputedStyle(document.getElementById('active-alerts-list'), '%s').content" % pseudo)
            self.assertIn(content, ("none", "normal"), "a fade is drawn over the alert cards")
        self.assertGreater(page.evaluate("document.getElementById('active-alerts-list').scrollHeight"),
                           page.evaluate("document.getElementById('active-alerts-list').clientHeight"))


if __name__ == "__main__":
    unittest.main()
