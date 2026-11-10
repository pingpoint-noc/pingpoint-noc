"""Browser test: light/dark theme, persistence and prefers-color-scheme. Optional (Playwright + Chromium)."""

import unittest

from .support import BrowserTestCase

THEME_KEY = "mini-noc-theme"


def theme_of(page):
    return page.evaluate("document.documentElement.getAttribute('data-theme')")


class ThemeTest(BrowserTestCase):
    def test_without_a_saved_choice_dark_system_gives_dark(self):
        page = self.open_page(color_scheme="dark")
        self.assertEqual(theme_of(page), "dark")
        self.assertIsNone(page.evaluate("localStorage.getItem('" + THEME_KEY + "')"))
        self.assertEqual(page.get_attribute("#theme-toggle", "aria-pressed"), "false")

    def test_without_a_saved_choice_light_system_gives_light(self):
        page = self.open_page(color_scheme="light")
        self.assertEqual(theme_of(page), "light")
        # Following the system is not a choice: nothing is stored until the user toggles.
        self.assertIsNone(page.evaluate("localStorage.getItem('" + THEME_KEY + "')"))

    def test_the_theme_is_set_before_first_paint(self):
        # theme-init.js runs in <head>: the attribute exists as soon as the document starts parsing.
        context = self.browser.new_context(color_scheme="light")
        self.addCleanup(context.close)
        page = context.new_page()
        page.add_init_script(
            "document.addEventListener('readystatechange', function () {"
            " if (window.__firstTheme === undefined) {"
            "  window.__firstTheme = document.documentElement.getAttribute('data-theme'); } });"
        )
        page.goto(self.server.base_url)
        self.assertEqual(page.evaluate("window.__firstTheme"), "light")

    def test_button_toggles_and_the_choice_survives_a_reload(self):
        page = self.open_page(color_scheme="dark")
        page.click("#theme-toggle")
        self.assertEqual(theme_of(page), "light")
        self.assertEqual(page.evaluate("localStorage.getItem('" + THEME_KEY + "')"), "light")
        self.assertEqual(page.get_attribute("#theme-toggle", "aria-pressed"), "true")
        page.reload()
        self.assertEqual(theme_of(page), "light")
        page.click("#theme-toggle")
        self.assertEqual(theme_of(page), "dark")
        page.reload()
        self.assertEqual(theme_of(page), "dark")

    def test_the_t_key_toggles_but_not_while_typing(self):
        page = self.open_page(color_scheme="dark")
        page.locator("body").press("t")
        self.assertEqual(theme_of(page), "light")
        page.locator("body").press("T")
        self.assertEqual(theme_of(page), "dark")
        # Typing a "t" in a form field must not flip the theme.
        page.locator("button[data-tab='tab-settings']").click()
        page.click("#settings-nav-bar [data-settings-section='account']")  # Settings opens on the rules since K117
        page.fill("#login-username", "")
        page.press("#login-username", "t")
        self.assertEqual(page.input_value("#login-username"), "t")
        self.assertEqual(theme_of(page), "dark")

    def test_a_saved_choice_wins_over_the_system_preference(self):
        page = self.open_page(color_scheme="dark")
        page.click("#theme-toggle")
        self.assertEqual(theme_of(page), "light")
        # Same context, so the same localStorage; now the system says dark but the user chose light.
        page.reload()
        self.assertEqual(theme_of(page), "light")
        page.emulate_media(color_scheme="dark")
        page.reload()
        self.assertEqual(theme_of(page), "light")

    def test_the_theme_changes_the_page_colours(self):
        page = self.open_page(color_scheme="dark")
        dark_bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        page.click("#theme-toggle")
        light_bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        self.assertNotEqual(dark_bg, light_bg)

    def test_a_bad_stored_value_is_ignored(self):
        context = self.browser.new_context(color_scheme="light")
        self.addCleanup(context.close)
        page = context.new_page()
        page.goto(self.server.base_url)
        page.evaluate("localStorage.setItem('" + THEME_KEY + "', 'purple')")
        page.reload()
        self.assertEqual(theme_of(page), "light")


if __name__ == "__main__":
    unittest.main()
