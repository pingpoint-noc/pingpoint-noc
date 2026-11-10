"""Browser test: acknowledge (K63), maintenance windows (K64) and device dependencies (K65).

Optional (Playwright + Chromium). Each test has its own server and database; alerts are written straight into
that database file, the way the engine would.
"""

import time
import unittest

from storage import create_repository

from .support import BrowserTestCase, USERNAME

ROUTER = "edge-router-1"
DNS = "pihole-dns"
VIEWPORTS = (320, 390, 768, 1440)


class AlertManagementBase(BrowserTestCase):
    SERVER_PER_TEST = True

    def repo(self):
        return create_repository("sqlite", self.server.db_path)

    def add_alert(self, device=ROUTER, rule="interface_down", target="if:77", message="Interface is down"):
        now = int(time.time())
        return self.repo().create_alert(device, rule, target, "critical", "firing", now, now, now, message)

    def sign_in_and_wait(self, page):
        self.sign_in(page)
        # "Editable" alone only says the server has an admin; the label shows this browser's session.
        page.wait_for_function("document.getElementById('session-label').textContent !== ''")
        page.wait_for_function("document.getElementById('settings-state').textContent === 'Editable'")

    def go_overview(self, page):
        page.locator("button[data-tab='tab-overview']").click()

    def card(self, page, target="if:77"):
        return page.locator(".alert-item-card", has_text=target)

    def no_overflow(self, page, selector):
        """Nothing of `selector` sticks out of the viewport, and the page itself does not scroll sideways."""
        return page.evaluate(
            """(selector) => {
              const width = document.documentElement.clientWidth;
              const bad = [];
              if (document.documentElement.scrollWidth > width) bad.push('page ' + document.documentElement.scrollWidth);
              for (const el of document.querySelectorAll(selector)) {
                const r = el.getBoundingClientRect();
                // A wide table inside its own scroll box (the inventory) is allowed to be wider than the screen.
                const box = el.closest('.table-container');
                if (box && !el.closest('.table-stack')) continue;
                if (r.width > 0 && (r.right > width + 1 || r.left < -1)) {
                  bad.push((el.className || el.tagName) + ' ' + Math.round(r.left) + '..' + Math.round(r.right));
                }
              }
              return bad;
            }""",
            selector,
        )


class AcknowledgeTest(AlertManagementBase):
    def test_acknowledge_with_a_note_then_remove_it(self):
        self.add_alert()
        page = self.open_page()
        self.sign_in_and_wait(page)
        self.go_overview(page)
        card = self.card(page)
        card.wait_for()
        self.assertEqual(card.locator(".alert-item-ack").count(), 0)
        card.locator(".alert-ack-note").fill("looking at it")
        card.locator(".alert-ack-btn").click()
        acked = self.card(page).locator(".alert-item-ack")
        acked.wait_for()
        text = acked.inner_text()
        self.assertIn("acknowledged", text.lower())
        self.assertIn(USERNAME, text)
        self.assertIn("looking at it", text)
        # The note field is gone while acknowledged; the button now removes it.
        self.assertEqual(self.card(page).locator(".alert-ack-note").count(), 0)
        self.card(page).locator(".alert-unack-btn").click()
        page.wait_for_function("document.querySelectorAll('.alert-item-ack').length === 0")
        self.assertEqual(self.card(page).locator(".alert-ack-btn").count(), 1)

    def test_enter_in_the_note_field_acknowledges(self):
        self.add_alert()
        page = self.open_page()
        self.sign_in_and_wait(page)
        self.go_overview(page)
        field = self.card(page).locator(".alert-ack-note")
        field.fill("enter key")
        field.press("Enter")
        self.card(page).locator(".alert-item-ack").wait_for()

    def test_without_signing_in_it_asks_to_sign_in(self):
        self.add_alert()
        page = self.open_page()
        self.card(page).locator(".alert-ack-btn").click()
        page.wait_for_selector("#toast-container .toast")
        self.assertIn("Sign in", page.locator("#toast-container").inner_text())
        self.assertEqual(self.card(page).locator(".alert-item-ack").count(), 0)

    def test_the_note_field_stops_at_200_characters(self):
        self.add_alert()
        page = self.open_page()
        self.assertEqual(self.card(page).locator(".alert-ack-note").get_attribute("maxlength"), "200")


class MaintenanceTest(AlertManagementBase):
    def open_maintenance(self, page):
        self.sign_in_and_wait(page)
        page.locator("button[data-tab='tab-settings']").click()
        page.click("[data-settings-section='maintenance']")
        page.wait_for_selector("#maint-device option[value='" + DNS + "']", state="attached")

    def test_local_wall_time_converts_to_utc_in_summer_and_winter(self):
        page = self.open_page()
        values = page.evaluate(
            """() => [
              localToUtc('2026-07-01T12:00'), localToUtc('2026-01-15T12:00'),
              utcToLocalInput(localToUtc('2026-07-01T12:00')), utcToLocalInput(localToUtc('2026-01-15T12:00')),
              localToUtc('2026-10-25T02:30'), localToUtc('2026-03-29T03:30'), localToUtc('nonsense')
            ]"""
        )
        self.assertEqual(values[0], 1782900000)  # 12:00 CEST (UTC+2) = 10:00 UTC
        self.assertEqual(values[1], 1768474800)  # 12:00 CET (UTC+1) = 11:00 UTC
        self.assertEqual(values[2], "2026-07-01T12:00")
        self.assertEqual(values[3], "2026-01-15T12:00")
        self.assertIsInstance(values[4], int)  # the repeated hour of the clock change still gives an instant
        self.assertIsInstance(values[5], int)
        self.assertIsNone(values[6])

    def test_create_a_window_see_the_banner_and_the_badge_then_end_it(self):
        self.add_alert()
        page = self.open_page()
        # The banner starts as "(0 firing)" until the first load of the alerts answers: wait for it before taking the count.
        page.wait_for_function("document.getElementById('alerts-title').textContent.indexOf('(0 firing') === -1")
        title_before = page.locator("#alerts-title").inner_text()
        self.open_maintenance(page)
        page.select_option("#maint-device", ROUTER)
        page.fill("#maint-reason", "router upgrade")
        page.click("#btn-maint-create")
        row = page.locator("#maintenance-tbody tr", has_text="router upgrade")
        row.wait_for()
        self.assertIn("active", row.inner_text().lower())
        self.assertIn("edge-router", row.inner_text())
        page.wait_for_function("document.getElementById('maintenance-banner').textContent.indexOf('Maintenance active: 1 window') === 0")
        self.go_overview(page)
        title_after = page.locator("#alerts-title").inner_text()
        badge = self.card(page).locator(".alert-hold-badge")
        badge.wait_for()
        self.assertIn("in maintenance", badge.inner_text().lower())
        self.assertIn("router upgrade", badge.inner_text())
        # still on screen and still counted
        self.assertEqual(title_after, title_before)  # the count did not change: nothing is hidden
        self.assertEqual(page.locator("#alerts-title").inner_text(), title_before)
        page.locator("button[data-tab='tab-settings']").click()
        page.click("[data-settings-section='maintenance']")
        page.locator("#maintenance-tbody .maint-del-btn").click()
        page.wait_for_function("document.getElementById('maintenance-banner').classList.contains('hidden')")
        self.assertIn("No maintenance windows", page.locator("#maintenance-tbody").inner_text())

    def test_an_explicit_end_and_a_future_start_make_a_scheduled_window(self):
        page = self.open_page()
        self.open_maintenance(page)
        start = page.evaluate("utcToLocalInput(Math.floor(Date.now() / 1000) + 7200)")
        end = page.evaluate("utcToLocalInput(Math.floor(Date.now() / 1000) + 10800)")
        page.fill("#maint-start", start)
        page.select_option("#maint-duration", "custom")
        page.fill("#maint-end", end)
        page.fill("#maint-reason", "night work")
        page.once("dialog", lambda dialog: dialog.accept())  # all devices and rules: it asks (K117)
        page.click("#btn-maint-create")
        row = page.locator("#maintenance-tbody tr", has_text="night work")
        row.wait_for()
        self.assertIn("scheduled", row.inner_text().lower())
        self.assertTrue(page.locator("#maintenance-banner").evaluate("el => el.classList.contains('hidden')"))

    def test_a_refused_window_says_why(self):
        page = self.open_page()
        self.open_maintenance(page)
        page.select_option("#maint-duration", "custom")
        page.fill("#maint-end", "")
        page.click("#btn-maint-create")
        self.assertIn("when the window ends", page.locator("#maintenance-msg").inner_text())
        start = page.evaluate("utcToLocalInput(Math.floor(Date.now() / 1000))")
        page.fill("#maint-end", start)  # ends when it starts
        page.once("dialog", lambda dialog: dialog.accept())
        page.click("#btn-maint-create")
        page.wait_for_function("document.getElementById('maintenance-msg').textContent.indexOf('Could not create') === 0")

    def test_a_window_for_everything_asks_first_and_can_be_cancelled(self):
        # K117: an empty form means every device and every rule for one hour.
        page = self.open_page()
        self.open_maintenance(page)
        messages = []

        def answer(dialog):
            messages.append(dialog.message)
            dialog.dismiss()

        page.once("dialog", answer)
        page.click("#btn-maint-create")
        page.wait_for_timeout(500)
        self.assertEqual(len(messages), 1)
        self.assertIn("all devices", messages[0].lower())
        self.assertIn("all rules", messages[0].lower())
        self.assertIn("1 hour", messages[0])
        self.assertEqual(self.repo().list_maintenance_windows(int(time.time())), [])
        self.assertIn("cancelled", page.locator("#maintenance-msg").inner_text().lower())
        page.once("dialog", lambda dialog: dialog.accept())
        page.click("#btn-maint-create")
        page.wait_for_function("document.getElementById('maintenance-msg').textContent.indexOf('Maintenance window created') === 0")
        self.assertEqual(len(self.repo().list_maintenance_windows(int(time.time()))), 1)

    def test_a_window_for_one_device_or_one_rule_does_not_ask(self):
        page = self.open_page()
        self.open_maintenance(page)
        asked = []
        page.on("dialog", lambda dialog: (asked.append(dialog.message), dialog.accept()))
        page.select_option("#maint-rule", "interface_down")
        page.fill("#maint-reason", "one rule")
        page.click("#btn-maint-create")
        page.locator("#maintenance-tbody tr", has_text="one rule").wait_for()
        self.assertEqual(asked, [])

    def test_the_form_asks_to_sign_in_first(self):
        page = self.open_page()
        page.locator("button[data-tab='tab-settings']").click()
        page.click("[data-settings-section='maintenance']")
        page.wait_for_selector("#maint-device option[value='" + DNS + "']", state="attached")
        page.click("#btn-maint-create")
        self.assertIn("Sign in", page.locator("#maintenance-msg").inner_text())
        self.assertEqual(self.repo().list_maintenance_windows(int(time.time())), [])


class DependencyTest(AlertManagementBase):
    def test_choose_a_parent_in_the_inventory_and_keep_it_after_a_reload(self):
        page = self.open_page()
        self.sign_in_and_wait(page)
        page.locator("button[data-tab='tab-settings']").click()
        page.click("[data-settings-section='inventory']")
        select = page.locator(".inv-parent-select[data-id='" + DNS + "']")
        select.wait_for()
        options = select.locator("option").all_inner_texts()
        self.assertEqual(options[0], "None (independent)")
        self.assertNotIn("Pi-hole DNS", options)  # never itself
        select.select_option(ROUTER)
        page.locator(".inv-save-btn[data-id='" + DNS + "']").click()
        page.wait_for_function("document.getElementById('settings-inventory-msg').textContent.indexOf('updated') !== -1")  # K119
        page.reload()
        page.locator("button[data-tab='tab-settings']").click()
        page.click("[data-settings-section='inventory']")
        page.wait_for_function(
            "document.querySelector(\".inv-parent-select[data-id='" + DNS + "']\")?.value === '" + ROUTER + "'",
            timeout=20000,  # the inventory is fetched again after the reload; a busy machine needs longer
        )

    def test_a_device_that_sits_below_another_is_not_offered_as_its_parent(self):
        self.repo().set_depends_on(DNS, ROUTER)
        page = self.open_page()
        self.sign_in_and_wait(page)
        page.locator("button[data-tab='tab-settings']").click()
        page.click("[data-settings-section='inventory']")
        router_select = page.locator(".inv-parent-select[data-id='" + ROUTER + "']")
        router_select.wait_for()
        values = router_select.locator("option").evaluate_all("els => els.map(e => e.value)")
        self.assertNotIn(DNS, values)  # DNS is behind the router: offering it would close a loop

    def test_the_alert_card_says_which_parent_suppresses_it(self):
        self.repo().set_depends_on(DNS, ROUTER)
        self.add_alert(device=ROUTER, rule="stale_source", target="collector:snmp", message="router silent")
        self.add_alert(device=DNS, rule="stale_source", target="collector:dns", message="dns silent")
        page = self.open_page()
        badge = self.card(page, "collector:dns").locator(".alert-hold-badge")
        badge.wait_for()
        self.assertIn("suppressed: behind", badge.inner_text().lower())
        self.assertEqual(self.card(page, "collector:snmp").locator(".alert-hold-badge").count(), 0)


class LayoutTest(AlertManagementBase):
    """Nothing sticks out or breaks at the four phone-to-desktop widths, in both themes."""

    def test_alert_cards_banner_and_settings_sections_fit(self):
        self.add_alert(message="Interface is down and this message is long enough to need more than one line on a phone")
        self.add_alert(device=DNS, rule="stale_source", target="collector:dns", message="dns silent")
        self.repo().set_depends_on(DNS, ROUTER)
        now = int(time.time())
        self.repo().add_maintenance_window(ROUTER, None, now - 60, now + 3600, "router firmware upgrade with a long reason", "alice", now - 60)
        repo = self.repo()
        for alert in repo.list_alerts(status="firing"):
            if alert["target"] == "if:77":
                repo.set_ack(alert["id"], USERNAME, now, "a note that is a few words long")
        problems = []
        for scheme in ("dark", "light"):
            for width in VIEWPORTS:
                context = self.browser.new_context(color_scheme=scheme, viewport={"width": width, "height": 900})
                self.addCleanup(context.close)
                page = context.new_page()
                page.set_default_timeout(10000)
                page.goto(self.server.base_url)
                page.wait_for_selector(".alert-item-card")
                page.wait_for_selector(".alert-hold-badge")
                problems += [(scheme, width, "alerts", item) for item in self.no_overflow(page, "#alerts-section *")]
                page.evaluate("switchTab('tab-settings')")  # the tab strip is a menu on a phone
                for section in ("maintenance", "inventory"):
                    page.click("[data-settings-section='" + section + "']")
                    page.wait_for_timeout(250)
                    problems += [
                        (scheme, width, section, item)
                        for item in self.no_overflow(page, "#settings-panel :is(input, select, button, label, h3, p, .badge)")
                    ]
        self.assertEqual(problems, [])

    def test_touch_targets_of_the_new_controls_are_at_least_24px(self):
        self.add_alert()
        page = self.open_page()
        self.card(page).wait_for()
        small = page.evaluate(
            """() => [...document.querySelectorAll('.alert-ack-btn, .alert-ack-note, .alert-silence-btn')]
              .map(e => e.getBoundingClientRect().height).filter(h => h < 24)"""
        )
        self.assertEqual(small, [])


if __name__ == "__main__":
    unittest.main()
