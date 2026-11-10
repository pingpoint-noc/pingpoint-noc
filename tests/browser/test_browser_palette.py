"""Browser test: the Ctrl+K command palette (fuzzy search, devices, alerts, settings sections, shortcut hints)."""

import unittest

from .support import BrowserTestCase


def open_palette(page):
    page.keyboard.press("Control+k")
    page.wait_for_selector("#cmd-palette-modal:not(.hidden)")


def titles(page):
    return page.eval_on_selector_all("#cmd-palette-results .cmd-item .cmd-item-title", "els => els.map(e => e.textContent)")


def active_tab(page):
    return page.evaluate("document.querySelector('.nav-tab-btn.active').getAttribute('data-tab')")


class PaletteTest(BrowserTestCase):
    def test_fuzzy_query_finds_a_view_by_scattered_letters(self):
        page = self.open_page()
        open_palette(page)
        page.fill("#cmd-palette-input", "bndw")
        self.assertIn("Bandwidth", titles(page)[0])
        page.keyboard.press("Enter")
        self.assertEqual(active_tab(page), "tab-bandwidth")

    def test_views_and_actions_show_their_keyboard_shortcut(self):
        page = self.open_page()
        open_palette(page)
        page.fill("#cmd-palette-input", "syslog")
        self.assertEqual(page.inner_text("#cmd-palette-results .cmd-item.active .cmd-item-keys kbd"), "5")
        page.fill("#cmd-palette-input", "pause")
        self.assertEqual(page.inner_text("#cmd-palette-results .cmd-item.active .cmd-item-keys kbd"), "P")

    def test_a_device_can_be_found_by_name_or_address(self):
        page = self.open_page()
        open_palette(page)
        page.fill("#cmd-palette-input", "192.168.88.4")
        self.assertTrue(any("noc-host" in t for t in titles(page)))
        page.keyboard.press("Enter")
        self.assertEqual(page.input_value("#device-select"), "node-192-168-88-4")

    def test_an_active_alert_is_listed_and_opens_its_timeline(self):
        page = self.open_page()
        page.wait_for_selector(".alert-item-card")
        open_palette(page)
        page.fill("#cmd-palette-input", "interface_down")
        self.assertTrue(page.locator("#cmd-palette-results .cmd-item-badge", has_text="Alert").count() >= 1)
        page.keyboard.press("Enter")
        self.assertEqual(active_tab(page), "tab-timeline")

    def test_a_settings_section_opens_straight_to_it(self):
        page = self.open_page()
        open_palette(page)
        page.fill("#cmd-palette-input", "notification channels")
        page.keyboard.press("Enter")
        self.assertEqual(active_tab(page), "tab-settings")
        self.assertEqual(
            page.get_attribute(".settings-sec-btn[data-settings-section='channels']", "aria-pressed"), "true"
        )

    def test_no_match_says_so_and_enter_does_nothing(self):
        page = self.open_page()
        open_palette(page)
        page.fill("#cmd-palette-input", "zzzzqq")
        self.assertEqual(titles(page), [])
        self.assertTrue(page.is_visible("#cmd-palette-results .cmd-palette-empty"))
        page.keyboard.press("Enter")
        self.assertFalse(page.evaluate("document.getElementById('cmd-palette-modal').classList.contains('hidden')"))

    def test_arrow_keys_move_the_selection_and_the_mouse_follows(self):
        page = self.open_page()
        open_palette(page)
        page.keyboard.press("ArrowDown")
        self.assertEqual(page.evaluate("Number(document.querySelector('.cmd-item.active').dataset.index)"), 1)
        page.hover("#cmd-palette-results .cmd-item:nth-child(3)")
        self.assertEqual(page.evaluate("Number(document.querySelector('.cmd-item.active').dataset.index)"), 2)

    def test_key_and_category_are_aligned_in_every_row(self):
        page = self.open_page()
        open_palette(page)
        rows = page.evaluate("""() => [...document.querySelectorAll('#cmd-palette-results .cmd-item')].map(row => {
            const r = row.getBoundingClientRect(), b = row.querySelector('.cmd-item-badge').getBoundingClientRect();
            const k = row.querySelector('.cmd-item-keys kbd'), kr = k ? k.getBoundingClientRect() : null;
            return { mid: r.top + r.height / 2, badgeMid: b.top + b.height / 2, badgeLeft: Math.round(b.left),
                     badgeH: Math.round(b.height), badgeW: Math.round(b.width),
                     keyMid: kr ? kr.top + kr.height / 2 : null, keyH: kr ? Math.round(kr.height) : null };
        })""")
        self.assertGreater(len(rows), 10)
        self.assertEqual(len({r["badgeLeft"] for r in rows}), 1, "category badge shifts between rows")
        self.assertEqual(len({r["badgeW"] for r in rows}), 1, "category badges differ in width")
        for r in rows:
            self.assertAlmostEqual(r["badgeMid"], r["mid"], delta=0.6)
            if r["keyMid"] is not None:
                self.assertAlmostEqual(r["keyMid"], r["badgeMid"], delta=0.6)
                self.assertEqual(r["keyH"], r["badgeH"])

    def test_the_list_has_listbox_semantics_for_screen_readers(self):
        page = self.open_page()
        open_palette(page)
        self.assertEqual(page.get_attribute("#cmd-palette-results", "role"), "listbox")
        self.assertEqual(page.get_attribute(".cmd-item.active", "aria-selected"), "true")
        self.assertEqual(page.get_attribute("#cmd-palette-input", "aria-activedescendant"), page.get_attribute(".cmd-item.active", "id"))


if __name__ == "__main__":
    unittest.main()
