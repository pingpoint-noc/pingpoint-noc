"""Browser test: Health & Inventory is split into Now / Capacity & Trends / Inventory & Onboarding (K113, point 2).

Only "Now" is open at first, so the tab no longer starts with 4500 px (desktop) or 14000 px (phone) of everything.
Optional (Playwright + Chromium).
"""

import unittest

from .support import BrowserTestCase

HEIGHT = "document.getElementById('tab-health').getBoundingClientRect().height"
PANELS_NOW = ("health-panel", "host-health-panel", "icmp-health-panel")
PANELS_TRENDS = ("capacity-panel", "alert-noise-panel", "availability-report-panel")
PANELS_INVENTORY = ("onboarding-panel",)


class HealthGroupsTest(BrowserTestCase):
    WITH_USER = False

    def open_health(self, width=1440):
        context = self.browser.new_context(viewport={"width": width, "height": 900})
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(self.server.base_url)
        page.keyboard.press("7")
        page.wait_for_function("document.querySelectorAll('#icmp-health-tbody tr').length > 0")
        page.wait_for_function("document.getElementById('capacity-cards').textContent.length > 0")
        return page

    def test_three_groups_in_this_order_with_only_now_open(self):
        page = self.open_health()
        groups = page.eval_on_selector_all(
            "#tab-health > .health-group",
            "els => els.map(e => [e.id, e.querySelector('.health-group-title').textContent.trim(),"
            " e.tagName === 'DETAILS' ? e.open : true])",
        )
        self.assertEqual(groups, [
            ["health-group-now", "Now", True],
            ["health-group-trends", "Capacity & Trends", False],
            ["health-group-inventory", "Inventory & Onboarding", False],
        ])

    def test_every_panel_sits_in_its_group(self):
        page = self.open_health()
        for group, panels in (("now", PANELS_NOW), ("trends", PANELS_TRENDS), ("inventory", PANELS_INVENTORY)):
            for panel in panels:
                self.assertTrue(page.evaluate(
                    "!!document.querySelector('#health-group-%s #%s')" % (group, panel)), panel + " not in " + group)

    def test_closed_groups_hide_their_panels_and_opening_shows_them(self):
        page = self.open_health()
        for panel in PANELS_NOW:
            self.assertTrue(page.is_visible("#" + panel), panel)
        for panel in PANELS_TRENDS + PANELS_INVENTORY:
            self.assertFalse(page.is_visible("#" + panel), panel + " should be folded")
        page.click("#health-group-trends > summary")
        page.click("#health-group-inventory > summary")
        for panel in PANELS_TRENDS + PANELS_INVENTORY:
            self.assertTrue(page.is_visible("#" + panel), panel)

    def test_the_tab_is_much_shorter_than_before(self):
        # Before K113: 4506 px on a desktop and 14180 px on a phone, everything open.
        page = self.open_health(1440)
        self.assertLessEqual(page.evaluate(HEIGHT), 2300)
        phone = self.open_health(390)
        self.assertLessEqual(phone.evaluate(HEIGHT), 6500)
        self.assertEqual(phone.evaluate("document.documentElement.scrollWidth"), 390)

    def test_a_phone_gets_compact_rows_for_healthy_collectors_and_for_devices(self):
        # K120: with real data (11 devices, many collectors) the phone still needed ~10000 px; healthy collectors now take less room.
        page = self.open_health(390)
        heights = page.evaluate("""() => {
          const h = (sel) => [...document.querySelectorAll(sel)].map(e => Math.round(e.getBoundingClientRect().height));
          return { collectors: h('#collectors-tbody tr.collector-ok'), host: h('#host-health-tbody tr[data-reports]'),
                   icmp: h('#icmp-health-tbody tr[data-reports]') };
        }""")
        self.assertTrue(heights["collectors"] and heights["host"] and heights["icmp"], heights)
        self.assertLessEqual(max(heights["collectors"]), 115, "healthy collector row")
        self.assertLessEqual(max(heights["host"]), 190, "device health row")
        self.assertLessEqual(max(heights["icmp"]), 165, "reachability row")
        self.assertLessEqual(page.evaluate(HEIGHT), 4050)  # 5261 px before K120 on the demo

    def test_a_failing_collector_keeps_every_field_on_a_phone(self):
        page = self.open_health(390)
        page.evaluate("""() => { const tr = document.querySelector('#collectors-tbody tr.collector-ok');
          tr.classList.remove('collector-ok'); }""")
        shown = page.evaluate("""() => [...document.querySelector('#collectors-tbody tr:not(.collector-ok)').children]
          .map(e => getComputedStyle(e).display !== 'none')""")
        self.assertTrue(all(shown), shown)

    def test_a_summary_opens_with_the_keyboard(self):
        page = self.open_health()
        page.focus("#health-group-trends > summary")
        page.keyboard.press("Enter")
        self.assertTrue(page.evaluate("document.getElementById('health-group-trends').open"))


if __name__ == "__main__":
    unittest.main()
