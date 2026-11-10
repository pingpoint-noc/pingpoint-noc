"""Browser test: the daily summary block of Settings (switch, time, destinations, test button, layout)."""

import unittest

from .support import BrowserTestCase, PASSWORD, USERNAME

WIDTHS = (320, 390, 768, 1440)


def open_channels(page):
    page.wait_for_function("document.getElementById('session-label').textContent !== ''")
    page.wait_for_function("document.getElementById('settings-state').textContent === 'Editable'")
    page.click("[data-settings-section='channels']")
    page.wait_for_selector("#dest-digest", state="visible")


class DigestSettingsTest(BrowserTestCase):
    SERVER_PER_TEST = True

    def test_switch_time_and_destinations_are_saved_and_come_back(self):
        page = self.open_page()
        self.sign_in(page)
        open_channels(page)
        self.assertFalse(page.is_checked("#set-digest-enabled"))
        self.assertEqual(page.input_value("#set-digest-time"), "08:00")
        self.assertTrue(page.is_checked("#set-digest-ntfy"))
        self.assertFalse(page.is_checked("#set-digest-discord"))
        self.assertEqual(page.inner_text("#digest-status"), "Off")

        page.check("#set-digest-enabled")
        page.fill("#set-digest-time", "07:45")
        page.check("#set-digest-email")
        page.click("#settings-save")
        page.wait_for_function("document.getElementById('settings-msg').textContent.indexOf('Saved.') === 0")

        page.reload()
        page.locator("button[data-tab='tab-settings']").click()
        self.sign_in_again_if_needed(page)
        open_channels(page)
        self.assertTrue(page.is_checked("#set-digest-enabled"))
        self.assertEqual(page.input_value("#set-digest-time"), "07:45")
        self.assertTrue(page.is_checked("#set-digest-email"))
        self.assertEqual(page.inner_text("#digest-status"), "On, 07:45")
        self.assertIn("Not sent yet", page.inner_text("#digest-last"))

    def sign_in_again_if_needed(self, page):
        # The session lives in sessionStorage, so a reload keeps it; sign in only if it was lost.
        page.wait_for_timeout(300)
        if page.locator("#login-submit").is_visible():
            self.sign_in(page)

    def test_the_test_button_explains_when_nothing_is_configured(self):
        page = self.open_page()
        self.sign_in(page)
        open_channels(page)
        page.click("#digest-test")
        page.wait_for_function("document.getElementById('digest-msg').textContent.indexOf('failed') !== -1")
        message = page.inner_text("#digest-msg")
        self.assertIn("no notification destination configured", message)
        self.assertIn("error", page.get_attribute("#digest-msg", "class"))

    def test_a_bad_time_is_refused_by_the_server_and_says_why(self):
        page = self.open_page()
        self.sign_in(page)
        open_channels(page)
        page.fill("#set-digest-time", "")
        page.click("#settings-save")
        page.wait_for_function("document.getElementById('settings-msg').textContent.indexOf('Not saved') === 0")
        self.assertIn("HH:MM", page.inner_text("#settings-msg"))

    def test_layout_fits_every_width_and_both_themes(self):
        for scheme in ("dark", "light"):
            for width in WIDTHS:
                context = self.browser.new_context(color_scheme=scheme, viewport={"width": width, "height": 800})
                self.addCleanup(context.close)
                page = context.new_page()
                page.set_default_timeout(10000)
                page.goto(self.server.base_url + "/")
                # Narrow screens fold the tab bar into a menu: switch tab from the page itself.
                page.evaluate("document.querySelector(\"button[data-tab='tab-settings']\").click()")
                page.click("#settings-nav-bar [data-settings-section='account']")  # Settings opens on the rules since K117
                page.fill("#login-username", USERNAME)
                page.fill("#login-password", PASSWORD)
                page.click("#login-submit")
                open_channels(page)
                # Under load the fonts and the first layout pass can still be moving: measure once they have settled
                page.evaluate("document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))")
                label = "%s %dpx" % (scheme, width)
                metrics = page.evaluate(
                    """() => {
                        const section = document.getElementById('dest-digest');
                        const box = section.getBoundingClientRect();
                        const small = [];
                        section.querySelectorAll('input, button, .rule-chip').forEach((el) => {
                            const r = el.getBoundingClientRect();
                            if (r.width === 0 || r.height === 0) { return; }
                            // The checkbox sits inside its chip or label: the clickable box is what counts.
                            if (el.matches('input[type=checkbox]') && el.closest('.rule-chip, .settings-check')) { return; }
                            if (r.height < 24 || r.width < 24) { small.push(el.id || el.className); }
                        });
                        const overflow = [];
                        section.querySelectorAll('*').forEach((el) => {
                            const r = el.getBoundingClientRect();
                            if (r.width === 0 && r.height === 0) { return; }
                            if (r.right > box.right + 1 || r.left < box.left - 1) { overflow.push(el.id || el.className); }
                        });
                        return {
                            pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
                            sectionRight: box.right, viewport: window.innerWidth, small, overflow,
                        };
                    }"""
                )
                self.assertLessEqual(metrics["pageOverflow"], 0, label)
                self.assertLessEqual(metrics["sectionRight"], metrics["viewport"] + 1, label)
                self.assertEqual(metrics["small"], [], label)
                self.assertEqual(metrics["overflow"], [], label)


if __name__ == "__main__":
    unittest.main()
