"""Browser test: the Environment block in Settings shows the scenario variables and the lines to copy. Optional (Playwright)."""

import unittest

from .support import BrowserTestCase

SUGGESTIONS = {
    "suggestions": {"MININOC_INTERNAL_NETS": "192.0.2.0/24", "MININOC_NOC_IP": "192.0.2.40", "MININOC_TZ": "Europe/Paris"},
    "configured": {"MININOC_ROUTER_IP": False, "MININOC_DEVICE_ID": False, "MININOC_DEVICE_NAME": False, "MININOC_INTERNAL_NETS": False,
                   "MININOC_ROUTER_OWN_IPS": False, "MININOC_NOC_IP": False, "MININOC_TZ": True},
    "effective": {"MININOC_ROUTER_IP": "", "MININOC_DEVICE_ID": "", "MININOC_DEVICE_NAME": "", "MININOC_INTERNAL_NETS": "10.0.0.0/8",
                  "MININOC_ROUTER_OWN_IPS": "", "MININOC_NOC_IP": "", "MININOC_TZ": "Asia/Tokyo"},
    "descriptions": {"MININOC_INTERNAL_NETS": "internal networks"},
    "interfaces": [], "ignored_interfaces": [],
}


class SetupEnvironmentTest(BrowserTestCase):
    def open_block(self):
        page = self.open_page()
        page.wait_for_function("typeof switchTab === 'function'")
        page.route("**/api/setup/suggestions", lambda route: route.fulfill(json=SUGGESTIONS))
        page.evaluate("() => { switchTab('tab-settings'); }")
        page.click("[data-settings-section='inventory']")
        page.wait_for_function("document.querySelectorAll('#setup-env-tbody tr').length === 7")
        return page

    def test_only_settings_that_still_use_a_default_and_have_a_suggestion_are_offered_as_lines(self):
        page = self.open_block()
        lines = page.input_value("#setup-env-lines").splitlines()
        # internal networks and the NOC address have a suggestion and no value in the env file; the time zone is already configured
        self.assertEqual(sorted(lines), ["MININOC_INTERNAL_NETS=192.0.2.0/24", "MININOC_NOC_IP=192.0.2.40"])

    def test_the_table_says_where_each_value_comes_from(self):
        page = self.open_block()
        text = page.inner_text("#setup-env-tbody")
        self.assertIn("built-in default", text)
        self.assertIn("mini-noc.env", text)
        self.assertIn("Europe/Paris", text)
        self.assertTrue(page.get_attribute("#setup-env-lines", "readonly") is not None)


if __name__ == "__main__":
    unittest.main()
