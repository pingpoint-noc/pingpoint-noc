"""Browser test: saving settings and the change log (K27). Optional, needs Playwright + Chromium."""

import unittest

from .support import BrowserTestCase, USERNAME

THRESHOLD_FIELD = "#set-thresh-failed-logins"
THRESHOLD_KEY = "alert_failed_logins"
NTFY_FIELD = "#set-ntfy-url"
NTFY_KEY = "ntfy_url"
NTFY_VALUE = "http://ntfy.invalid/browser-test-topic"


class SettingsWithUserTest(BrowserTestCase):
    SERVER_PER_TEST = True

    def audit_rows(self, page):
        rows = []
        cells = page.locator("#audit-tbody tr")
        for index in range(cells.count()):
            rows.append(cells.nth(index).inner_text())
        return rows

    def wait_for_message(self, page, prefix):
        page.wait_for_function(
            "document.getElementById('settings-msg').textContent.indexOf(" + repr(prefix) + ") === 0"
        )

    def materialize_defaults(self, page):
        """The first save on a fresh database stores every form default, so it lists every key.

        That is by design (a key absent from the database counts as changed), so tests that look at
        one key first save the untouched form once to get a baseline.
        """
        page.click("#settings-save")
        self.wait_for_message(page, "Saved.")
        page.wait_for_function("document.querySelectorAll('#audit-tbody tr').length > 0")
        page.wait_for_load_state("networkidle")

    def test_first_save_on_a_fresh_database_lists_the_defaults_it_stores(self):
        page = self.open_page()
        self.sign_in(page)
        page.wait_for_function("document.getElementById('settings-state').textContent === 'Editable'")
        page.wait_for_selector("#audit-box:not(.hidden)")
        self.assertEqual(self.audit_rows(page), [])
        self.materialize_defaults(page)
        rows = self.audit_rows(page)
        self.assertEqual(len(rows), 1)
        self.assertIn("settings.update", rows[0])
        self.assertIn(THRESHOLD_KEY, rows[0])

    def test_admin_saves_a_threshold_and_the_change_log_lists_only_its_key(self):
        page = self.open_page()
        self.sign_in(page)
        page.wait_for_function("document.getElementById('session-label').textContent !== ''")
        # The form is editable.
        page.wait_for_function("document.getElementById('settings-state').textContent === 'Editable'")
        self.assertFalse(page.locator("#settings-readonly").is_visible())
        page.wait_for_selector("#audit-box:not(.hidden)")
        self.materialize_defaults(page)
        before = len(self.audit_rows(page))

        # Settings shows one section at a time (K51): the thresholds are in "rules", the change log in "account".
        page.click("[data-settings-section='rules']")
        old_value = page.input_value(THRESHOLD_FIELD)
        new_value = "37"
        self.assertNotEqual(old_value, new_value)
        page.fill(THRESHOLD_FIELD, new_value)
        page.click("#settings-save")
        self.wait_for_message(page, "Saved.")
        page.click("[data-settings-section='account']")

        page.wait_for_function(
            "document.querySelectorAll('#audit-tbody tr').length > " + str(before)
        )
        rows = self.audit_rows(page)
        newest = rows[0]
        self.assertIn(USERNAME, newest)
        self.assertIn("settings.update", newest)
        self.assertIn(THRESHOLD_KEY, newest)
        # Only the key that changed is listed, and never a value.
        self.assertNotIn(NTFY_KEY, newest)
        self.assertNotIn("min_severity", newest)
        # Only the detail column is checked: the time column can hold any digits (it once held ":37" and failed 1 run in 60).
        detail = newest.split("\t")[3]
        self.assertNotIn(new_value, detail.replace(THRESHOLD_KEY, ""))
        # The new value is what the server now returns after a reload.
        page.reload()
        page.locator("button[data-tab='tab-settings']").click()
        page.wait_for_function(
            "document.getElementById('set-thresh-failed-logins').value === '" + new_value + "'"
        )

    def test_changing_the_ntfy_destination_logs_the_key_but_never_the_url(self):
        page = self.open_page()
        self.sign_in(page)
        page.wait_for_function("document.getElementById('settings-state').textContent === 'Editable'")
        page.wait_for_selector("#audit-box:not(.hidden)")
        self.materialize_defaults(page)
        before = len(self.audit_rows(page))
        page.click("[data-settings-section='channels']")
        page.fill(NTFY_FIELD, NTFY_VALUE)
        page.click("#settings-save")
        self.wait_for_message(page, "Saved.")
        page.click("[data-settings-section='account']")
        page.wait_for_function("document.querySelectorAll('#audit-tbody tr').length > " + str(before))
        table_text = page.inner_text("#audit-tbody")
        newest = self.audit_rows(page)[0]
        self.assertTrue(newest.endswith("settings.update	" + NTFY_KEY), newest)
        self.assertNotIn("browser-test-topic", table_text)
        self.assertNotIn("ntfy.invalid", table_text)
        self.assertEqual(len(self.audit_rows(page)), before + 1)

    def test_saving_the_same_values_again_adds_no_log_row(self):
        page = self.open_page()
        self.sign_in(page)
        page.wait_for_function("document.getElementById('settings-state').textContent === 'Editable'")
        page.wait_for_selector("#audit-box:not(.hidden)")
        self.materialize_defaults(page)
        before = len(self.audit_rows(page))
        self.assertEqual(before, 1)
        # The page reloads the change log after every save; wait for that very response.
        with page.expect_response(lambda response: "/api/settings/audit" in response.url):
            page.click("#settings-save")
        self.assertEqual(len(self.audit_rows(page)), before)

    def test_without_a_session_saving_is_refused_and_the_log_stays_hidden(self):
        page = self.open_page()
        page.locator("button[data-tab='tab-settings']").click()
        page.wait_for_function("document.getElementById('settings-state').textContent === 'Editable'")
        # The server has a user, so the form is enabled; the refusal comes from the API (401).
        page.click("#settings-save")
        self.wait_for_message(page, "Not saved:")
        self.assertFalse(page.locator("#audit-box").is_visible())
        self.assertIsNone(page.evaluate("sessionStorage.getItem('mini-noc-session')"))


class SettingsSectionsTest(BrowserTestCase):
    """K51: Settings shows one section at a time and the destination filters are ticked chips."""

    SERVER_PER_TEST = True

    def visible_groups(self, page):
        return page.evaluate(
            "[...new Set([...document.querySelectorAll('#tab-settings [data-section-group]')]"
            ".filter(e => e.offsetParent !== null).map(e => e.getAttribute('data-section-group')))].sort()"
        )

    def test_signed_out_it_opens_on_the_alert_rules_and_offers_to_sign_in(self):
        # K117: what people come to Settings for is the rules; the sign-in box is one click away from a hint.
        page = self.open_page()
        page.locator("button[data-tab='tab-settings']").click()
        page.wait_for_selector("#tab-settings:not(.settings-pending)")
        self.assertEqual(self.visible_groups(page), ["always", "rules"])
        self.assertTrue(page.locator("#settings-save").is_visible())
        self.assertFalse(page.locator("#login-username").is_visible())
        hint = page.locator("#settings-signin-hint")
        self.assertTrue(hint.is_visible())
        self.assertIn("not signed in", hint.inner_text().lower())
        hint.locator("button").click()
        self.assertEqual(self.visible_groups(page), ["account", "always"])
        self.assertTrue(page.locator("#login-username").is_visible())
        self.assertFalse(hint.is_visible())

    def test_signed_in_there_is_no_sign_in_hint(self):
        page = self.open_page()
        self.sign_in(page)
        page.wait_for_selector("#audit-box:not(.hidden)")
        page.click("[data-settings-section='rules']")
        self.assertFalse(page.locator("#settings-signin-hint").is_visible())

    def test_signed_in_a_chosen_section_is_shown_alone_and_remembered(self):
        page = self.open_page()
        self.sign_in(page)
        # "Editable" shows before the sign-in finishes (the server has a user); the change log only after it.
        page.wait_for_selector("#audit-box:not(.hidden)")
        page.click("[data-settings-section='rules']")
        self.assertEqual(self.visible_groups(page), ["always", "rules"])
        self.assertEqual(page.get_attribute("[data-settings-section='rules']", "aria-pressed"), "true")
        self.assertFalse(page.locator("#login-form").is_visible())
        # "All sections" is still there for anyone who wants the long page.
        page.click("[data-settings-section='all']")
        self.assertEqual(self.visible_groups(page), ["account", "always", "channels", "inventory", "maintenance", "rules"])
        page.click("[data-settings-section='channels']")
        page.reload()
        page.locator("button[data-tab='tab-settings']").click()
        page.wait_for_selector("#tab-settings:not(.settings-pending)")
        self.assertEqual(self.visible_groups(page), ["always", "channels"])

    def test_a_ticked_rule_is_saved_counted_and_reopens_its_filters(self):
        page = self.open_page()
        self.sign_in(page)
        # "Editable" shows before the sign-in finishes (the server has a user); the change log only after it.
        page.wait_for_selector("#audit-box:not(.hidden)")
        page.click("[data-settings-section='channels']")
        self.assertFalse(page.evaluate("document.getElementById('dest-filters-ntfy').open"))
        self.assertEqual(page.inner_text("#dest-filters-state-ntfy"), "every alert")
        page.click("#dest-filters-ntfy > summary")
        page.locator("#set-ntfy-rules .rule-chip").first.click()
        self.assertTrue(page.inner_text("#set-ntfy-rules-count").startswith("1 of"))
        self.assertEqual(page.inner_text("#dest-filters-state-ntfy"), "1 rule")
        page.click("#settings-save")
        self.wait_for_message(page, "Saved.")
        page.reload()
        page.locator("button[data-tab='tab-settings']").click()
        page.wait_for_function("document.querySelector('#set-ntfy-rules input:checked') !== null")
        self.assertTrue(page.evaluate("document.getElementById('dest-filters-ntfy').open"))

    def wait_for_message(self, page, prefix):
        page.wait_for_function(
            "document.getElementById('settings-msg').textContent.indexOf(" + repr(prefix) + ") === 0"
        )


class SettingsReadOnlyServerTest(BrowserTestCase):
    """A server with no user and no admin token cannot be changed from the page at all."""

    WITH_USER = False

    def test_form_is_read_only_and_explains_why(self):
        page = self.open_page()
        page.locator("button[data-tab='tab-settings']").click()
        # K32 renamed the badge: it says what to do, and the note below explains the first-admin setup.
        page.wait_for_function("document.getElementById('settings-state').textContent === 'Sign in to Edit'")
        self.assertTrue(page.locator("#settings-readonly").is_visible())
        self.assertIn("manage_users", page.inner_text("#settings-readonly"))
        self.assertIn("Quick Admin Setup", page.inner_text("#settings-readonly"))
        self.assertTrue(page.locator("#settings-save").is_disabled())
        self.assertTrue(page.locator(NTFY_FIELD).is_disabled())
        self.assertTrue(page.locator(THRESHOLD_FIELD).is_disabled())
        self.assertFalse(page.locator("#audit-box").is_visible())


if __name__ == "__main__":
    unittest.main()
