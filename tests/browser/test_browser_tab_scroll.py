"""Browser test: scroll position when changing views (K122). Optional.

A view seen for the first time, or not seen for five minutes, starts at its top; coming back to it sooner returns to
the same place; pressing the tab you are already on reloads without moving the page.
"""

import unittest

from .support import BrowserTestCase


class TabScrollTest(BrowserTestCase):
    WITH_USER = False

    def _open(self):
        page = self.open_page()
        page.wait_for_selector("#device-select option", state="attached")
        page.wait_for_timeout(1500)
        return page

    def _y(self, page):
        return page.evaluate("Math.round(window.scrollY)")

    def _scroll(self, page, y):
        page.evaluate("window.scrollTo({top: " + str(y) + ", left: 0, behavior: 'instant'})")
        page.wait_for_timeout(150)

    def _tab(self, page, tab_id):
        page.click('.nav-tab-btn[data-tab="' + tab_id + '"]')
        page.wait_for_timeout(500)

    def test_a_view_seen_for_the_first_time_starts_at_the_top(self):
        page = self._open()
        self._scroll(page, 700)
        self.assertGreater(self._y(page), 300, "the Overview must be long enough to scroll for this test")
        for tab_id in ("tab-bandwidth", "tab-health", "tab-timeline"):
            self._scroll(page, 400)
            self._tab(page, tab_id)
            self.assertEqual(self._y(page), 0, tab_id)

    def test_coming_back_soon_returns_to_where_you_were(self):
        page = self._open()
        self._scroll(page, 700)
        saved = self._y(page)
        self._tab(page, "tab-bandwidth")
        self.assertEqual(self._y(page), 0)
        self._tab(page, "tab-overview")
        self.assertAlmostEqual(self._y(page), saved, delta=20)

    def test_coming_back_after_five_minutes_starts_at_the_top(self):
        page = self._open()
        self._scroll(page, 700)
        self._tab(page, "tab-bandwidth")
        page.evaluate("(() => { const real = Date.now.bind(Date); Date.now = () => real() + 6 * 60 * 1000; })()")
        self._tab(page, "tab-overview")
        self.assertEqual(self._y(page), 0)

    def test_pressing_the_current_tab_reloads_without_moving_the_page(self):
        page = self._open()
        self._scroll(page, 500)
        before = self._y(page)
        self.assertGreater(before, 200)
        self._tab(page, "tab-overview")
        self.assertAlmostEqual(self._y(page), before, delta=20)


if __name__ == "__main__":
    unittest.main()
