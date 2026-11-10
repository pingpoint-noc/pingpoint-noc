"""Browser test: pause, keyboard shortcuts and CSV export. Optional (Playwright + Chromium)."""

import unittest

from .support import BrowserTestCase

TAB_ORDER = [
    "tab-overview", "tab-topology", "tab-bandwidth", "tab-flows", "tab-syslog", "tab-timeline", "tab-health", "tab-settings",
]


def active_tab(page):
    return page.evaluate(
        "document.querySelector('.nav-tab-btn.active').getAttribute('data-tab')"
    )


def help_is_hidden(page):
    return page.evaluate("document.getElementById('shortcut-help').classList.contains('hidden')")


class PauseTest(BrowserTestCase):
    def test_button_pauses_and_resumes_the_auto_refresh(self):
        page = self.open_page()
        self.assertEqual(page.get_attribute("#pause-btn", "aria-pressed"), "false")
        page.click("#pause-btn")
        self.assertEqual(page.get_attribute("#pause-btn", "aria-pressed"), "true")
        self.assertEqual(page.inner_text("#refresh-countdown"), "paused")
        page.click("#pause-btn")
        self.assertEqual(page.get_attribute("#pause-btn", "aria-pressed"), "false")
        self.assertNotEqual(page.inner_text("#refresh-countdown"), "paused")

    def test_p_key_toggles_the_pause(self):
        page = self.open_page()
        page.keyboard.press("p")
        self.assertEqual(page.get_attribute("#pause-btn", "aria-pressed"), "true")
        page.keyboard.press("P")
        self.assertEqual(page.get_attribute("#pause-btn", "aria-pressed"), "false")


class ShortcutTest(BrowserTestCase):
    def test_number_keys_switch_the_view(self):
        page = self.open_page()
        index = 0
        for tab_id in TAB_ORDER:
            index += 1
            page.keyboard.press(str(index))
            self.assertEqual(active_tab(page), tab_id)

    def test_question_mark_and_escape_toggle_the_help(self):
        page = self.open_page()
        self.assertTrue(help_is_hidden(page))
        page.keyboard.press("?")
        self.assertFalse(help_is_hidden(page))
        self.assertEqual(page.get_attribute("#help-btn", "aria-expanded"), "true")
        page.keyboard.press("Escape")
        self.assertTrue(help_is_hidden(page))
        self.assertEqual(page.get_attribute("#help-btn", "aria-expanded"), "false")

    def test_shortcuts_are_ignored_while_typing_in_a_field(self):
        page = self.open_page()
        page.keyboard.press("8")
        page.click("#settings-nav-bar [data-settings-section='account']")  # Settings opens on the rules since K117
        page.fill("#login-username", "")
        page.focus("#login-username")
        page.keyboard.type("p1?")
        self.assertEqual(page.input_value("#login-username"), "p1?")
        self.assertEqual(active_tab(page), "tab-settings")
        self.assertEqual(page.get_attribute("#pause-btn", "aria-pressed"), "false")
        self.assertTrue(help_is_hidden(page))

    def test_ctrl_combinations_do_not_trigger_shortcuts(self):
        page = self.open_page()
        page.keyboard.press("Control+p")
        self.assertEqual(page.get_attribute("#pause-btn", "aria-pressed"), "false")

    def test_h_returns_to_the_overview(self):
        page = self.open_page()
        page.keyboard.press("5")
        self.assertEqual(active_tab(page), "tab-syslog")
        page.keyboard.press("h")
        self.assertEqual(active_tab(page), "tab-overview")


class ExportTest(BrowserTestCase):
    def test_alert_history_button_downloads_a_csv(self):
        page = self.open_page()
        with page.expect_download() as download_info:
            page.click("#export-alert-history")
        download = download_info.value
        self.assertTrue(download.suggested_filename.startswith("alerts_history_"))
        self.assertTrue(download.suggested_filename.endswith(".csv"))
        path = download.path()
        with open(path, encoding="utf-8") as handle:
            first_line = handle.readline()
        self.assertIn("id,device_id,rule_id,target,severity,status", first_line)


if __name__ == "__main__":
    unittest.main()


class TabReloadAndScrollHintTest(BrowserTestCase):
    def test_alert_history_json_button_downloads_json(self):
        page = self.open_page()
        with page.expect_download() as info:
            page.click("#export-alert-history-json")
        download = info.value
        self.assertTrue(download.suggested_filename.endswith(".json"))
        import json
        body = json.loads(open(download.path(), encoding="utf-8").read())
        self.assertIn("alerts", body)

    def test_pressing_the_current_tab_fetches_its_data_again(self):
        page = self.open_page()
        page.click("button[data-tab='tab-syslog']")
        page.wait_for_load_state("networkidle")
        with page.expect_request(lambda request: "/logs" in request.url):
            page.click("button[data-tab='tab-syslog']")
        self.assertEqual(active_tab(page), "tab-syslog")

    def test_a_table_wider_than_its_box_shows_the_arrow_on_the_side_that_continues(self):
        page = self.open_page()
        page.set_viewport_size({"width": 1200, "height": 900})
        page.click("button[data-tab='tab-syslog']")
        page.evaluate(
            "const t = document.querySelector('#logs-tbody').closest('table'); t.style.minWidth = '2400px';"
        )
        page.wait_for_function("document.querySelector('.scroll-hint.more-right') !== null")
        self.assertFalse(page.evaluate("document.querySelector('#logs-panel .scroll-hint').classList.contains('more-left')"))
        page.evaluate("const c = document.querySelector('#logs-panel .table-container'); c.scrollLeft = c.scrollWidth;")
        page.wait_for_function("document.querySelector('#logs-panel .scroll-hint').classList.contains('more-left')")
        self.assertFalse(page.evaluate("document.querySelector('#logs-panel .scroll-hint').classList.contains('more-right')"))


    def test_clicking_the_arrow_scrolls_the_table_and_the_arrows_follow(self):
        # K73: the arrow used to be a drawing with pointer-events: none, so clicking it did nothing on every table.
        page = self.open_page()
        page.set_viewport_size({"width": 1200, "height": 900})
        page.click("button[data-tab='tab-syslog']")
        page.evaluate(
            "const t = document.querySelector('#logs-tbody').closest('table'); t.style.minWidth = '2400px';"
        )
        right = "#logs-panel .scroll-hint-right:not([hidden])"
        left = "#logs-panel .scroll-hint-left:not([hidden])"
        page.wait_for_selector(right)
        self.assertEqual(page.locator(left).count(), 0)
        before = page.evaluate("document.querySelector('#logs-panel .table-container').scrollLeft")
        page.click(right)
        page.wait_for_function("document.querySelector('#logs-panel .table-container').scrollLeft > 100")
        self.assertGreater(page.evaluate("document.querySelector('#logs-panel .table-container').scrollLeft"), before)
        page.wait_for_selector(left)  # something now continues on the left
        page.click(left)
        page.wait_for_function("document.querySelector('#logs-panel .table-container').scrollLeft < 5")
        page.wait_for_selector(left, state="hidden")


if __name__ == "__main__":
    unittest.main()
