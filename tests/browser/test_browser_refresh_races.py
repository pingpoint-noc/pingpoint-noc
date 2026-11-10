"""Browser test: races and failures in the load/refresh logic (stale answers, failed feeds, focus kept, hold, a11y).

Delayed or failed requests are simulated with page.route. Optional, needs Playwright + Chromium.
"""

import json
from urllib.parse import urlparse

from .support import BrowserTestCase


def path_of(url):
    return urlparse(url).path


def route_path(page, path, handler):
    page.route(lambda url: path_of(url) == path, handler)


class RefreshRacesTest(BrowserTestCase):
    SERVER_PER_TEST = False  # nothing here changes stored data

    def ready_page(self):
        page = self.open_page()
        page.wait_for_selector(".alert-item-card")
        page.wait_for_function("STATE.cacheDevices.length > 1 && STATE.cacheDeviceDetail !== undefined")
        return page

    # ---- 1. a late answer for the previous device -----------------------------------------------------------
    def test_late_answer_for_the_previous_device_does_not_repaint_the_view(self):
        page = self.ready_page()
        ids = page.evaluate("STATE.cacheDevices.map(d => d.id)")
        current = page.evaluate("STATE.deviceId")
        other = [i for i in ids if i != current][0]
        held = []
        route_path(page, "/api/devices/" + current, lambda route: held.append(route))
        page.evaluate("() => { window.__late = loadOverviewAndDevice(); }")
        for _ in range(60):
            if held:
                break
            page.wait_for_timeout(50)
        self.assertTrue(held, "the request for the first device was not issued")
        page.select_option("#device-select", other)
        page.wait_for_function("STATE.cacheDeviceDetail && STATE.cacheDeviceDetail.id === " + json.dumps(other))
        for route in held:
            route.continue_()
        page.evaluate("() => window.__late")
        page.wait_for_timeout(300)
        self.assertEqual(page.evaluate("STATE.cacheDeviceDetail.id"), other)
        self.assertEqual(page.input_value("#device-select"), other)
        self.assertEqual(page.evaluate("STATE.deviceId"), other)

    def test_a_loader_helper_tells_a_stale_request_from_a_current_one(self):
        page = self.ready_page()
        self.assertTrue(page.evaluate(
            "() => { const k = viewKey(); STATE.range = '1h'; const r = isStale(k); STATE.range = '15m'; return r; }"))
        self.assertFalse(page.evaluate("isStale(viewKey())"))

    # ---- 2. the alert feed fails ----------------------------------------------------------------------------
    def test_failed_alert_feed_says_unknown_once_and_recovers(self):
        page = self.ready_page()
        route_path(page, "/api/alerts", lambda route: route.fulfill(status=500, body="boom"))
        page.evaluate("loadAlerts()")
        page.evaluate("loadAlerts()")
        self.assertEqual(page.inner_text("#global-status-text"), "Alerts unknown")
        self.assertIn("global-status stale", page.get_attribute("#global-status-badge", "class"))
        self.assertEqual(page.inner_text("#alerts-title"), "Alert feed unavailable")
        self.assertIn("freshness-down", page.get_attribute("#alerts-freshness", "class"))
        self.assertNotIn("0 Firing", page.inner_text("#kpi-alerts-count"))
        self.assertEqual(page.locator("#toast-container .toast", has_text="Alert feed unavailable").count(), 1)
        page.unroute_all()
        page.evaluate("loadAlerts()")
        self.assertNotEqual(page.inner_text("#global-status-text"), "Alerts unknown")
        self.assertNotEqual(page.inner_text("#alerts-title"), "Alert feed unavailable")
        self.assertNotIn("freshness-down", page.get_attribute("#alerts-freshness", "class") or "")

    # ---- 3. /api/overview fails on the first load -----------------------------------------------------------
    def test_failed_first_overview_load_says_so_instead_of_loading_forever(self):
        context = self.browser.new_context(viewport={"width": 1400, "height": 900})
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        route_path(page, "/api/overview", lambda route: route.fulfill(status=500, body="boom"))
        page.goto(self.server.base_url + "/")
        page.wait_for_function(
            "document.getElementById('overview-device-freshness').textContent.indexOf('Overview unavailable') === 0")
        self.assertIn("HTTP 500", page.inner_text("#overview-device-freshness"))
        self.assertIn("freshness-stale", page.get_attribute("#overview-device-freshness", "class"))

    # ---- 4. focus and typed text outside the view -----------------------------------------------------------
    def test_user_is_reading_looks_at_the_whole_document(self):
        page = self.ready_page()
        self.assertFalse(page.evaluate("userIsReading()"))
        page.evaluate("""() => {
            const i = document.createElement('input'); i.id = 'outside-input'; i.type = 'text';
            document.body.insertBefore(i, document.body.firstChild);
            const d = document.createElement('div'); d.id = 'outside-edit'; d.contentEditable = 'true'; d.tabIndex = 0;
            document.body.appendChild(d);
        }""")
        page.focus("#outside-input")
        self.assertFalse(page.evaluate("userIsReading()"), "a focused but untouched field must not hold the refresh")
        page.fill("#outside-input", "typed")
        self.assertTrue(page.evaluate("userIsReading()"))
        page.focus("#outside-edit")
        self.assertTrue(page.evaluate("userIsReading()"))

    def test_alert_note_keeps_its_text_and_focus_when_the_alert_changes(self):
        page = self.open_page()
        counter = {"n": 0}

        def handler(route):
            counter["n"] += 1
            response = route.fetch()
            data = response.json()
            for al in data.get("active", []):
                al["message"] = str(al["message"]) + " v" + str(counter["n"])
            route.fulfill(response=response, body=json.dumps(data))

        route_path(page, "/api/alerts", handler)
        page.wait_for_selector(".alert-item-card")
        note = page.locator(".alert-ack-note").first
        note.focus()
        note.fill("seen it")
        before = page.locator(".alert-item-message").first.inner_text()
        page.evaluate("loadAlerts()")
        after = page.locator(".alert-item-message").first.inner_text()
        self.assertEqual(before, after, "the focused card is kept as it is until the focus leaves")
        self.assertEqual(page.locator(".alert-ack-note").first.input_value(), "seen it")
        self.assertTrue(page.evaluate("document.activeElement.classList.contains('alert-ack-note')"))
        page.locator(".alert-ack-note").first.blur()
        page.evaluate("loadAlerts()")
        self.assertNotEqual(before, page.locator(".alert-item-message").first.inner_text())

    def test_unchanged_select_options_are_not_rebuilt(self):
        page = self.ready_page()
        page.evaluate("document.querySelector('#device-select option').__mark = 1")
        page.evaluate("loadOverviewAndDevice()")
        self.assertEqual(page.evaluate("document.querySelector('#device-select option').__mark"), 1)
        # a real change in the list still rebuilds it
        route_path(page, "/api/overview", self.with_extra_device)
        page.evaluate("loadOverviewAndDevice()")
        self.assertTrue(page.evaluate(
            "[...document.querySelectorAll('#device-select option')].some(o => o.value === 'extra-test-dev')"))

    @staticmethod
    def with_extra_device(route):
        response = route.fetch()
        data = response.json()
        extra = dict(data["devices"][0])
        extra["id"] = "extra-test-dev"
        extra["name"] = "extra"
        data["devices"].append(extra)
        route.fulfill(response=response, body=json.dumps(data))

    # ---- 5. the hold does not block the alerts --------------------------------------------------------------
    def test_hold_keeps_refreshing_alerts_but_not_the_panels(self):
        page = self.ready_page()
        seen = []
        page.on("request", lambda req: seen.append(path_of(req.url)))
        note = page.locator(".alert-ack-note").first
        note.focus()
        note.fill("half a note")
        page.wait_for_timeout(2500)  # let the tail of a refresh that was already running finish
        seen.clear()
        page.evaluate("STATE.countdownRemaining = 1")
        page.wait_for_function("document.getElementById('refresh-countdown').textContent === 'hold'", timeout=5000)
        page.wait_for_timeout(600)
        self.assertIn("/api/alerts", seen)
        # Only the alerts and the small Monitored Nodes count (K119) refresh while held; no panel endpoint is asked for.
        self.assertEqual([path for path in seen if path not in ("/api/alerts", "/api/overview")], [])
        self.assertEqual(page.locator(".alert-ack-note").first.input_value(), "half a note")

    # ---- 6. settings errors ---------------------------------------------------------------------------------
    def test_save_with_an_invalid_value_toasts_and_focuses_the_field(self):
        page = self.open_page()
        self.sign_in(page)
        page.wait_for_function("document.getElementById('settings-state').textContent === 'Editable'")
        page.click("[data-settings-section='rules']")
        page.fill("#set-thresh-bw-bps", "5")
        page.click("#settings-top-save-btn")
        page.wait_for_selector("#toast-container .toast:has-text('not saved')")
        self.assertEqual(page.get_attribute("#set-thresh-bw-bps", "aria-invalid"), "true")
        self.assertEqual(page.evaluate("document.activeElement.id"), "set-thresh-bw-bps")

    def test_add_target_errors_use_the_inventory_message_and_a_toast(self):
        page = self.open_page()
        self.sign_in(page)
        page.wait_for_function("document.getElementById('settings-state').textContent === 'Editable'")
        page.click("[data-settings-section='inventory']")
        page.click("#btn-settings-add-node")
        self.assertIn("Enter an IPv4 address", page.inner_text("#settings-inventory-msg"))
        self.assertEqual(page.inner_text("#settings-msg"), "")
        self.assertEqual(page.locator("#toast-container .toast", has_text="Enter an IPv4 address").count(), 1)

    # ---- 7. syslog severity ---------------------------------------------------------------------------------
    def test_syslog_severity_applies_as_soon_as_it_changes(self):
        page = self.ready_page()
        page.click("button[data-tab=tab-syslog]")
        value = page.evaluate(
            "[...document.querySelectorAll('#log-severity-select option')].map(o => o.value).filter(v => v)[0]")
        with page.expect_request(lambda r: "/logs?" in r.url and "severity=" + value in r.url):
            page.select_option("#log-severity-select", value)
        self.assertEqual(page.evaluate("STATE.logSeverity"), value)

    # ---- 8. no preset password in the first-admin box -------------------------------------------------------
    def test_first_admin_password_is_not_preloaded(self):
        page = self.open_page()
        self.assertEqual(page.input_value("#bootstrap-password"), "")
        self.assertTrue(page.get_attribute("#bootstrap-password", "placeholder"))

    # ---- 9. accessibility -----------------------------------------------------------------------------------
    def test_pause_button_keeps_one_label_and_only_aria_pressed_changes(self):
        page = self.ready_page()
        label = page.get_attribute("#pause-btn", "aria-label")
        page.click("#pause-btn")
        self.assertEqual(page.get_attribute("#pause-btn", "aria-pressed"), "true")
        self.assertEqual(page.get_attribute("#pause-btn", "aria-label"), label)
        page.click("#pause-btn")
        self.assertEqual(page.get_attribute("#pause-btn", "aria-pressed"), "false")
        self.assertEqual(page.get_attribute("#pause-btn", "aria-label"), label)

    def test_palette_returns_the_focus_on_escape_and_traps_tab(self):
        page = self.ready_page()
        page.focus("#cmd-palette-btn")
        page.keyboard.press("Enter")
        page.wait_for_selector("#cmd-palette-modal:not(.hidden)")
        for combo in ("Tab", "Shift+Tab", "Tab"):
            page.keyboard.press(combo)
            self.assertTrue(
                page.evaluate("document.getElementById('cmd-palette-modal').contains(document.activeElement)"), combo)
        page.keyboard.press("Escape")
        page.wait_for_selector("#cmd-palette-modal.hidden", state="attached")
        self.assertEqual(page.evaluate("document.activeElement.id"), "cmd-palette-btn")

    # ---- 10. giving the focus back must not scroll the page -------------------------------------------------
    def scroll_to_bottom(self, page):
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(100)
        y = page.evaluate("window.scrollY")
        self.assertGreater(y, 100, "the page must be scrollable for this test to mean anything")
        return y

    def test_closing_the_shortcut_help_keeps_the_scroll_position(self):
        page = self.ready_page()
        page.focus("#cmd-palette-btn")
        page.keyboard.press("?")
        page.wait_for_selector("#shortcut-help-panel:not(.hidden), #shortcut-help:not(.hidden)")
        y = self.scroll_to_bottom(page)
        page.click("#shortcut-help-close")
        page.wait_for_timeout(200)
        self.assertAlmostEqual(page.evaluate("window.scrollY"), y, delta=2)

    def test_closing_the_palette_keeps_the_scroll_position(self):
        page = self.ready_page()
        page.focus("#cmd-palette-btn")
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-palette-modal:not(.hidden)")
        y = self.scroll_to_bottom(page)
        page.keyboard.press("Escape")
        page.wait_for_selector("#cmd-palette-modal.hidden", state="attached")
        page.wait_for_timeout(200)
        self.assertAlmostEqual(page.evaluate("window.scrollY"), y, delta=2)


if __name__ == "__main__":
    import unittest
    unittest.main()
