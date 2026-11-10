"""Browser test: the configured time zone (MININOC_TZ, K82) in the header clock, the labels and the layout.

Optional (Playwright + Chromium). The server is started with the zone in its environment. A long zone name must not
overflow or misalign anything at phone and desktop widths: measured in pixels, not by eye.
"""

import os
import unittest

from .support import BrowserTestCase

WIDTHS = (320, 390, 1001, 1440)
TABS = ("tab-overview", "tab-topology", "tab-bandwidth", "tab-flows", "tab-syslog", "tab-timeline", "tab-health", "tab-settings")

MEASURE = """() => {
  const box = (el) => { const b = el.getBoundingClientRect(); return {left: b.left, right: b.right, top: b.top, bottom: b.bottom, width: b.width}; };
  const clock = document.querySelector('.clock-display');
  const label = document.querySelector('.clock-label');
  const value = document.getElementById('clock-local');
  const first = document.querySelector('.noc-navbar-controls .control-group');
  return {
    viewport: document.documentElement.clientWidth,
    pageWidth: document.documentElement.scrollWidth,
    clock: box(clock), value: box(value), labelShown: getComputedStyle(label).display !== 'none', label: box(label),
    labelClipped: label.scrollWidth > label.clientWidth + 1,
    labelText: label.textContent, labelTitle: label.getAttribute('title'),
    clockText: value.textContent, first: box(first), controls: box(document.querySelector('.noc-navbar-controls')),
  };
}"""

OVERFLOW = """(selector) => {
  const width = document.documentElement.clientWidth;
  const bad = [];
  if (document.documentElement.scrollWidth > width) bad.push('page ' + document.documentElement.scrollWidth);
  for (const el of document.querySelectorAll(selector)) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || el.closest('.table-container')) continue;
    if (r.right > width + 1 || r.left < -1) bad.push((el.id || el.className || el.tagName) + ' ' + Math.round(r.left) + '..' + Math.round(r.right));
  }
  return bad;
}"""


class ZoneBase(BrowserTestCase):
    WITH_USER = False
    ZONE = ""
    saved_zone = None

    @classmethod
    def setUpClass(cls):
        cls.saved_zone = os.environ.get("MININOC_TZ")
        os.environ["MININOC_TZ"] = cls.ZONE
        try:
            super().setUpClass()
        except BaseException:
            cls.restore_zone()
            raise

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.restore_zone()

    @classmethod
    def restore_zone(cls):
        if cls.saved_zone is None:
            os.environ.pop("MININOC_TZ", None)
        else:
            os.environ["MININOC_TZ"] = cls.saved_zone

    def page_at(self, width):
        context = self.browser.new_context(viewport={"width": width, "height": 800})
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(self.server.base_url + "/", wait_until="networkidle")
        page.wait_for_function("/^[0-9]{2}:[0-9]{2}:[0-9]{2}$/.test(document.getElementById('clock-local').textContent)")
        return page


class LongZoneTest(ZoneBase):
    ZONE = "America/Argentina/ComodRivadavia"

    def test_the_clock_shows_the_zone_and_stays_inside_the_header_at_every_width(self):
        for width in WIDTHS:
            with self.subTest(width=width):
                page = self.page_at(width)
                m = page.evaluate(MEASURE)
                self.assertEqual(m["labelText"], self.ZONE)
                self.assertEqual(m["labelTitle"], self.ZONE)
                self.assertLessEqual(m["pageWidth"], m["viewport"], "page scrolls sideways")
                self.assertGreaterEqual(m["clock"]["left"], 0)
                self.assertLessEqual(m["clock"]["right"], m["viewport"] + 1)
                if m["labelShown"]:
                    # Label above the time, not over it, and the clock no wider than Europe/Madrid makes it (105 px plus the divider padding).
                    self.assertLessEqual(m["label"]["bottom"], m["value"]["top"] + 1)
                    self.assertLessEqual(m["clock"]["width"], 122)
                    self.assertTrue(m["labelClipped"])
                    # Same row as the controls on a wide header: the clock must not drop to a row of its own.
                    self.assertAlmostEqual(m["clock"]["top"], m["controls"]["top"], delta=2)
                    # And the Target select keeps its width (240 px) instead of being squeezed.
                    self.assertGreaterEqual(m["first"]["width"], 150)

    def test_no_tab_overflows_with_a_long_zone_in_its_labels(self):
        for width in (320, 390, 1440):
            with self.subTest(width=width):
                page = self.page_at(width)
                for tab in TABS:
                    page.evaluate("switchTab(%r)" % tab)
                    if tab == "tab-settings":
                        page.locator("[data-settings-section='maintenance']").first.click()
                        page.wait_for_selector("#maint-start", state="visible")
                        self.assertEqual(page.evaluate(OVERFLOW, "label, th, .clock-display, caption"), [], tab)
                        page.locator("[data-settings-section='channels']").first.click()
                        page.wait_for_selector("#set-digest-time", state="visible")
                    self.assertEqual(page.evaluate(OVERFLOW, "label, th, .clock-display, caption"), [], tab)

    def test_the_labels_name_the_zone(self):
        page = self.page_at(1440)
        self.assertEqual(page.locator("meta[name='mini-noc-tz']").get_attribute("content"), self.ZONE)
        page.evaluate("switchTab('tab-settings')")
        page.locator("[data-settings-section='maintenance']").first.click()
        self.assertEqual(page.inner_text("label[for='maint-start']"), "Start (%s)" % self.ZONE)
        page.locator("[data-settings-section='channels']").first.click()
        self.assertEqual(page.inner_text("label[for='set-digest-time']"), "Send at (%s)" % self.ZONE)
        page.evaluate("switchTab('tab-syslog')")
        self.assertIn(self.ZONE, page.inner_text("#syslog-table-caption"))


class HalfHourZoneTest(ZoneBase):
    ZONE = "Asia/Kolkata"

    def test_the_clock_is_the_wall_time_of_the_zone(self):
        page = self.page_at(1440)
        shown = page.inner_text("#clock-local")
        expected = page.evaluate(
            "new Intl.DateTimeFormat('es-ES', {timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false}).format(new Date())"
        )
        self.assertRegex(shown, r"^\d\d:\d\d:\d\d$")
        self.assertEqual(shown[:5], expected)
        self.assertEqual(page.inner_text(".clock-label").strip().upper(), "ASIA/KOLKATA")

    def test_the_maintenance_form_reads_the_typed_time_in_the_zone(self):
        page = self.page_at(1440)
        # 12:00 in Kolkata (+05:30, no summer time) is 06:30 UTC all year.
        got = page.evaluate("[localToUtc('2026-07-01T12:00'), localToUtc('2026-01-15T12:00'), utcToLocalInput(1782907200)]")
        self.assertEqual(got[0], 1782887400)
        self.assertEqual(got[1], 1768458600)
        self.assertEqual(got[2], "2026-07-01T17:30")


if __name__ == "__main__":
    unittest.main()
