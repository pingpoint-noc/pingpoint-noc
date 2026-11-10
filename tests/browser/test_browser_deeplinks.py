"""Browser test: deep links. The URL hash mirrors the view, target, window, interface, Settings section,
Syslog filters and Timeline moment, and a link restores that state (K68)."""

import unittest

from .support import BrowserTestCase


def active_tab(page):
    return page.evaluate("document.querySelector('.nav-tab-btn.active').getAttribute('data-tab')")


def device_ids(page):
    return page.eval_on_selector_all("#device-select option", "els => els.map(e => e.value)")


def active_range(page):
    return page.evaluate("document.querySelector('.range-pill-btn.active').getAttribute('data-range')")


def wait_ready(page):
    page.wait_for_function("document.querySelectorAll('#device-select option').length > 1")
    page.wait_for_function("window.ROUTE && window.ROUTE.ready === true")


class DeepLinkTest(BrowserTestCase):
    def open_ready(self, path="/"):
        page = self.open_page(path=path)
        self.errors = []
        page.on("pageerror", lambda err: self.errors.append(str(err)))
        wait_ready(page)
        return page

    def other_device(self, page):
        ids = device_ids(page)
        return ids[0], ids[1]

    def test_default_state_leaves_the_url_clean(self):
        page = self.open_ready()
        self.assertEqual(page.evaluate("location.hash"), "")
        page.locator("button[data-tab='tab-flows']").click()
        self.assertEqual(page.evaluate("location.hash"), "#/flows")
        page.locator("button[data-tab='tab-overview']").click()
        self.assertEqual(page.evaluate("location.hash"), "")

    def test_a_link_restores_view_target_and_window(self):
        probe = self.open_ready()
        _, other = self.other_device(probe)
        page = self.open_ready("/#/flows?device=" + other + "&range=1h")
        self.assertEqual(active_tab(page), "tab-flows")
        self.assertEqual(page.input_value("#device-select"), other)
        self.assertEqual(active_range(page), "1h")
        self.assertEqual(page.evaluate("location.hash"), "#/flows?device=" + other + "&range=1h")
        self.assertEqual(self.errors, [])

    def test_back_and_forward_restore_the_previous_view_and_target(self):
        page = self.open_ready()
        first, other = self.other_device(page)
        page.locator("button[data-tab='tab-syslog']").click()
        page.select_option("#device-select", other)
        self.assertEqual(page.evaluate("location.hash"), "#/syslog?device=" + other)
        page.locator("button[data-tab='tab-health']").click()
        self.assertEqual(active_tab(page), "tab-health")
        page.go_back()
        page.wait_for_function("document.querySelector('.nav-tab-btn.active').dataset.tab === 'tab-syslog'")
        self.assertEqual(page.input_value("#device-select"), other)
        page.go_back()
        page.wait_for_function("document.querySelector('.nav-tab-btn.active').dataset.tab === 'tab-syslog'")
        page.wait_for_function("document.getElementById('device-select').value === '" + first + "'")
        page.go_back()
        page.wait_for_function("document.querySelector('.nav-tab-btn.active').dataset.tab === 'tab-overview'")
        self.assertEqual(page.evaluate("location.hash"), "")
        page.go_forward()
        page.wait_for_function("document.querySelector('.nav-tab-btn.active').dataset.tab === 'tab-syslog'")
        self.assertEqual(self.errors, [])

    def test_the_window_uses_replace_so_back_skips_it(self):
        page = self.open_ready()
        length = page.evaluate("history.length")
        page.locator(".range-pill-btn[data-range='24h']").click()
        self.assertEqual(page.evaluate("location.hash"), "#/overview?range=24h")
        self.assertEqual(page.evaluate("history.length"), length)
        page.locator(".range-pill-btn[data-range='15m']").click()
        self.assertEqual(page.evaluate("location.hash"), "")

    def test_invalid_links_fall_back_to_defaults_without_errors(self):
        page = self.open_ready("/#/nowhere?device=ghost&range=999d&if=abc&sev=bad&at=-1&x=%3Cb%3E")
        self.assertEqual(active_tab(page), "tab-overview")
        self.assertEqual(active_range(page), "15m")
        self.assertEqual(page.input_value("#device-select"), device_ids(page)[0])
        self.assertEqual(page.evaluate("location.hash"), "")
        self.assertEqual(self.errors, [])
        page.evaluate("location.hash = '#/syslog?device=%E0%A4%A&sev=%3Cimg%20src=x%3E&q=' + 'a'.repeat(900)")
        page.wait_for_function("document.querySelector('.nav-tab-btn.active').dataset.tab === 'tab-syslog'")
        self.assertEqual(page.input_value("#log-severity-select"), "")
        self.assertLessEqual(len(page.input_value("#log-search-input")), 200)
        self.assertEqual(page.locator("img[src='x']").count(), 0)
        self.assertEqual(self.errors, [])

    def test_the_parser_ignores_unknown_and_duplicate_parameters(self):
        page = self.open_ready()
        route = page.evaluate("parseRoute('#/bandwidth?range=1h&range=24h&evil=1&if=7&at=12x')")
        self.assertEqual(route["view"], "bandwidth")
        self.assertEqual(route["range"], "1h")
        self.assertEqual(route["ifIndex"], 7)
        self.assertIsNone(route["at"])
        self.assertEqual(page.evaluate("parseRoute('#/settings/channels').section"), "channels")
        self.assertEqual(page.evaluate("parseRoute('garbage').view"), "overview")

    def test_settings_section_filters_and_interface_are_in_the_link(self):
        page = self.open_ready("/#/settings/channels")
        self.assertEqual(active_tab(page), "tab-settings")
        self.assertEqual(
            page.get_attribute(".settings-sec-btn[data-settings-section='channels']", "aria-pressed"), "true"
        )
        page.locator(".settings-sec-btn[data-settings-section='inventory']").click()
        self.assertEqual(page.evaluate("location.hash"), "#/settings/inventory")

        page.locator("button[data-tab='tab-syslog']").click()
        page.select_option("#log-severity-select", "err")
        page.fill("#log-search-input", "link down")
        page.click("#log-filter-btn")
        self.assertEqual(page.evaluate("location.hash"), "#/syslog?sev=err&q=link%20down")
        reopened = self.open_ready("/" + page.evaluate("location.hash"))
        self.assertEqual(reopened.input_value("#log-severity-select"), "err")
        self.assertEqual(reopened.input_value("#log-search-input"), "link down")

        page.locator("button[data-tab='tab-bandwidth']").click()
        options = page.eval_on_selector_all("#bw-if-select option", "els => els.map(e => e.value)")
        pick = [o for o in options if o != "0"][-1]
        page.select_option("#bw-if-select", pick)
        self.assertIn("if=" + pick, page.evaluate("location.hash"))
        reopened = self.open_ready("/" + page.evaluate("location.hash"))
        self.assertEqual(active_tab(reopened), "tab-bandwidth")
        self.assertEqual(reopened.input_value("#bw-if-select"), pick)

    def test_old_timeline_links_from_notifications_still_work(self):
        probe = self.open_ready()
        first, _ = self.other_device(probe)
        page = self.open_ready("/#timeline/" + first + "/1700000000")
        self.assertEqual(active_tab(page), "tab-timeline")
        self.assertEqual(page.evaluate("STATE.timelineAtUtc"), 1700000000)
        self.assertEqual(self.errors, [])
        # The same moment in the new format round-trips.
        self.assertRegex(page.evaluate("location.hash"), r"^#/timeline\?(if=\d+&)?at=1700000000$")
        again = self.open_ready("/" + page.evaluate("location.hash"))
        self.assertEqual(again.evaluate("STATE.timelineAtUtc"), 1700000000)

    def test_the_link_the_server_puts_in_a_notification_opens_the_timeline(self):
        from urllib.parse import quote

        probe = self.open_ready()
        first, _ = self.other_device(probe)
        # alerts/engine.py builds "<public url>/#timeline/<quoted device>/<epoch>"
        page = self.open_ready("/#timeline/%s/%d" % (quote(first, safe=""), 1700000123))
        self.assertEqual(active_tab(page), "tab-timeline")
        self.assertEqual(page.input_value("#device-select"), first)
        self.assertEqual(page.evaluate("STATE.timelineAtUtc"), 1700000123)
        self.assertIn("centered", page.inner_text("#toast-container").lower())

    def test_copy_link_button_fits_in_both_themes_and_at_every_width(self):
        for scheme in ("dark", "light"):
            for width in (320, 390, 768, 1440):
                context = self.browser.new_context(color_scheme=scheme, viewport={"width": width, "height": 900})
                self.addCleanup(context.close)
                page = context.new_page()
                page.goto(self.server.base_url)
                wait_ready(page)
                page.locator("#tools-menu summary").click()
                box = page.locator("#copy-link-btn").bounding_box()
                self.assertGreaterEqual(box["height"], 24)
                self.assertGreaterEqual(box["x"], 0)
                self.assertLessEqual(box["x"] + box["width"], width)
                self.assertEqual(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), True)
                context.close()

    def test_legacy_link_with_an_encoded_device_id(self):
        page = self.open_ready()
        self.assertEqual(page.evaluate("parseTimelineHash('#timeline/a%20b%2Fc/42').device"), "a b/c")
        self.assertIsNone(page.evaluate("parseTimelineHash('#timeline/x/notanumber')"))
        self.assertEqual(page.evaluate("parseRoute('#timeline/edge-router-1/42').at"), 42)

    def test_copy_link_puts_the_current_url_on_the_clipboard(self):
        page = self.open_ready()
        page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        page.locator("button[data-tab='tab-flows']").click()
        page.locator("#tools-menu summary").click()
        page.click("#copy-link-btn")
        page.wait_for_selector(".toast")
        self.assertIn("copied", page.inner_text("#toast-container").lower())
        self.assertEqual(page.evaluate("navigator.clipboard.readText()"), page.evaluate("location.href"))
        self.assertFalse(page.evaluate("document.getElementById('tools-menu').open"))

    def test_tools_menu_closes_after_choosing_an_action(self):
        page = self.open_ready()
        is_open = "document.getElementById('tools-menu').open"
        page.locator("#tools-menu summary").click()
        page.click("#tv-mode-btn")
        self.assertTrue(page.evaluate("STATE.tvMode"))
        self.assertFalse(page.evaluate(is_open))
        page.evaluate("toggleTvMode(false)")
        # The alert-sound switch shows its state inside the menu, so it stays open
        page.locator("#tools-menu summary").click()
        page.click("#sound-btn")
        self.assertTrue(page.evaluate(is_open))
        # The V key also reaches toggleTvMode while the menu is open
        page.evaluate("toggleTvMode(true)")
        self.assertFalse(page.evaluate(is_open))

    def test_copy_link_works_from_the_palette(self):
        page = self.open_ready("/#/syslog?range=1h")
        page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        page.keyboard.press("Control+k")
        page.fill("#cmd-palette-input", "copy link")
        page.keyboard.press("Enter")
        page.wait_for_selector(".toast")
        self.assertTrue(page.evaluate("navigator.clipboard.readText()").endswith("#/syslog?range=1h"))

    def test_copy_link_falls_back_when_the_clipboard_api_is_missing(self):
        page = self.open_ready("/#/flows")
        page.evaluate("""() => {
            Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
            window.__copied = null;
            document.execCommand = (cmd) => {
                const t = document.activeElement;
                window.__copied = t && t.value;
                return true;
            };
        }""")
        page.locator("#tools-menu summary").click()
        page.click("#copy-link-btn")
        page.wait_for_selector(".toast")
        self.assertTrue(page.evaluate("window.__copied").endswith("#/flows"))
        self.assertIn("copied", page.inner_text("#toast-container").lower())
        self.assertEqual(page.locator("textarea[data-copy-helper]").count(), 0)

    def test_copy_link_says_where_the_link_is_when_nothing_can_copy(self):
        page = self.open_ready()
        page.evaluate("""() => {
            Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
            document.execCommand = () => false;
        }""")
        page.locator("#tools-menu summary").click()
        page.click("#copy-link-btn")
        page.wait_for_selector(".toast")
        self.assertIn("address bar", page.inner_text("#toast-container"))

    def test_tv_mode_does_not_write_to_the_url(self):
        page = self.open_ready()
        before = page.evaluate("history.length")
        page.evaluate("toggleTvMode(true)")
        page.evaluate("switchTab('tab-bandwidth')")
        self.assertEqual(page.evaluate("location.hash"), "")
        self.assertEqual(page.evaluate("history.length"), before)
        page.evaluate("toggleTvMode(false)")
        self.assertRegex(page.evaluate("location.hash"), r"^#/bandwidth(\?if=\d+)?$")

    def test_pressing_the_current_tab_still_reloads_without_a_history_entry(self):
        page = self.open_ready()
        page.locator("button[data-tab='tab-flows']").click()
        length = page.evaluate("history.length")
        with page.expect_response("**/flows/top*"):
            page.locator("button[data-tab='tab-flows']").click()
        self.assertEqual(page.evaluate("history.length"), length)


if __name__ == "__main__":
    unittest.main()
