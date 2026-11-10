"""Browser test: dropdowns centre their text in browsers without appearance: base-select (K125). Optional.

A global rule gave every <select> padding 0 above and 3 px below, tuned for the engines that draw the control with
appearance: base-select. A browser that draws a native select and centres the text itself (measured in Falkon on the lab:
text centre 1.5 px above the centre of its box, arrow exactly on it) then lifts the text above its arrow.

Chromium here supports base-select, so the other engine is simulated with the real stylesheet: the @supports block that
switches the base-select look on is made to fail.
"""

import unittest

from .support import BrowserTestCase

TABS = ("tab-overview", "tab-bandwidth", "tab-flows", "tab-syslog", "tab-timeline", "tab-health", "tab-settings")

PADDING = """() => [...document.querySelectorAll('select:not([multiple]):not([size])')]
  .filter(s => { const r = s.getBoundingClientRect(); return r.width > 40 && r.height > 12 && s.offsetParent !== null; })
  .map(s => { const c = getComputedStyle(s);
    return { name: s.id || s.className, top: parseFloat(c.paddingTop), bottom: parseFloat(c.paddingBottom) }; })"""


class SelectCenteringTest(BrowserTestCase):
    WITH_USER = False

    def open_without_base_select(self):
        context = self.browser.new_context(color_scheme="dark", viewport={"width": 1400, "height": 900})
        self.addCleanup(context.close)

        def old_engine(route):
            response = route.fetch()
            body = response.text().replace("@supports (appearance: base-select)", "@supports (appearance: no-such-value)")
            route.fulfill(response=response, body=body)

        context.route("**/styles.css*", old_engine)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(self.server.base_url + "/")
        return page

    def paddings(self, page, tab_id):
        page.evaluate("t => document.querySelector('.nav-tab-btn[data-tab=\"' + t + '\"]').click()", tab_id)
        page.wait_for_timeout(500)
        return page.evaluate(PADDING)

    def test_a_native_select_has_the_same_padding_above_and_below_its_text(self):
        page = self.open_without_base_select()
        page.wait_for_selector("#device-select option", state="attached")
        page.wait_for_timeout(1000)
        self.assertFalse(page.evaluate("CSS.supports('appearance', 'no-such-value')"))
        checked = 0
        for tab_id in TABS:
            for item in self.paddings(page, tab_id):
                checked += 1
                self.assertAlmostEqual(item["top"], item["bottom"], delta=0.1, msg=tab_id + " " + item["name"])
        self.assertGreater(checked, 5)

    def test_base_select_engines_keep_the_lift_that_was_measured_for_them(self):
        page = self.open_page()
        page.wait_for_selector("#device-select option", state="attached")
        page.wait_for_timeout(1000)
        header = next(i for i in self.paddings(page, "tab-overview") if i["name"] == "device-select")
        self.assertEqual((header["top"], header["bottom"]), (0.0, 3.0))


if __name__ == "__main__":
    unittest.main()
