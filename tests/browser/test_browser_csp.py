"""F6-03: the Content-Security-Policy the server sends does not break any view (no CSP violation in the console)."""

import unittest

from .support import BrowserTestCase

TABS = ("tab-overview", "tab-topology", "tab-bandwidth", "tab-flows", "tab-syslog", "tab-timeline", "tab-health", "tab-settings")


class ContentSecurityPolicyTest(BrowserTestCase):
    WITH_USER = False
    BYPASS_CSP = False

    def test_the_policy_is_sent_with_the_page(self):
        context = self.browser.new_context()
        self.addCleanup(context.close)
        response = context.new_page().goto(self.server.base_url + "/")
        policy = response.headers.get("content-security-policy", "")
        self.assertIn("default-src 'self'", policy)
        self.assertIn("script-src 'self'", policy)

    def test_no_view_triggers_a_csp_violation(self):
        context = self.browser.new_context(viewport={"width": 1400, "height": 900})
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        problems = []
        page.on("console", lambda msg: problems.append(msg.text) if "Content Security Policy" in msg.text else None)
        page.on("pageerror", lambda err: problems.append(str(err)) if "Content Security Policy" in str(err) else None)
        page.goto(self.server.base_url + "/")
        # Not wait_for_function(str): Playwright evaluates it with eval(), which this very policy forbids.
        page.wait_for_selector("#device-select option", state="attached")
        for tab in TABS:
            page.click('.nav-tab-btn[data-tab="' + tab + '"]')
            page.wait_for_timeout(700)
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
