"""Browser test: sign-in from the Settings tab (K27). Optional, needs Playwright + Chromium."""

import json
import unittest

from .support import BrowserTestCase, PASSWORD, USERNAME


class LoginFlowTest(BrowserTestCase):
    def test_wrong_password_shows_a_generic_message_and_no_session(self):
        page = self.open_page()
        self.sign_in(page, "not the password")
        message = page.locator("#login-msg")
        message.wait_for(state="visible")
        page.wait_for_function("document.getElementById('login-msg').textContent.indexOf('Not signed in') === 0")
        text = message.inner_text()
        self.assertIn("wrong username or password", text)
        # The same text for an unknown user: the page must not reveal which one is wrong.
        self.assertNotIn(USERNAME, text)
        self.assertIsNone(page.evaluate("sessionStorage.getItem('mini-noc-session')"))
        self.assertTrue(page.locator("#login-form").is_visible())
        # The password field is cleared after every attempt.
        self.assertEqual(page.input_value("#login-password"), "")

    def test_right_password_signs_in_and_keeps_the_token_out_of_the_url(self):
        page = self.open_page()
        self.sign_in(page)
        page.wait_for_function("document.getElementById('session-label').textContent !== ''")
        self.assertEqual(page.inner_text("#session-label"), "Signed in as " + USERNAME + " (admin)")
        self.assertFalse(page.locator("#login-form").is_visible())
        raw = page.evaluate("sessionStorage.getItem('mini-noc-session')")
        self.assertIsNotNone(raw)
        session = json.loads(raw)
        token = session["token"]
        self.assertEqual(session["username"], USERNAME)
        # Never in the URL, and never in localStorage (which outlives the tab).
        self.assertNotIn(token, page.url)
        self.assertNotIn("token", page.url)
        # (the page keeps harmless preferences there, e.g. sound, but never the session).
        self.assertNotIn(token, page.evaluate("JSON.stringify(Object.assign({}, localStorage))"))
        self.assertNotIn(PASSWORD, raw)

    def test_session_survives_a_reload_in_the_same_tab_only(self):
        page = self.open_page()
        self.sign_in(page)
        page.wait_for_function("document.getElementById('session-label').textContent !== ''")
        page.reload()
        page.locator("button[data-tab='tab-settings']").click()
        page.wait_for_function("document.getElementById('session-label').textContent !== ''")
        self.assertEqual(page.inner_text("#session-label"), "Signed in as " + USERNAME + " (admin)")
        self.assertFalse(page.locator("#login-form").is_visible())
        # A second tab of the same browser context has its own sessionStorage: no session there.
        other = page.context.new_page()
        other.goto(self.server.base_url)
        other.locator("button[data-tab='tab-settings']").click()
        # Settings stays hidden (settings-pending) until it has chosen its section: wait, do not sample once.
        other.locator("#login-form").wait_for(state="visible", timeout=10000)
        self.assertIsNone(other.evaluate("sessionStorage.getItem('mini-noc-session')"))

    def test_sign_out_drops_the_session(self):
        page = self.open_page()
        self.sign_in(page)
        page.wait_for_function("document.getElementById('session-label').textContent !== ''")
        page.click("#logout-btn")
        page.wait_for_function("document.getElementById('login-form').classList.contains('hidden') === false")
        self.assertIsNone(page.evaluate("sessionStorage.getItem('mini-noc-session')"))
        self.assertEqual(page.inner_text("#login-msg"), "Signed out.")


if __name__ == "__main__":
    unittest.main()
