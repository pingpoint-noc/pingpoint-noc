"""Browser test: the first screen. The header does not move between views, and the first target has data. Optional (Playwright)."""

import unittest

from .support import BrowserTestCase


class FirstScreenTest(BrowserTestCase):
    WITH_USER = False

    def test_the_page_reserves_the_scrollbar_so_the_header_does_not_move_between_views(self):
        # Headless Chromium hides scrollbars, so the shift (10 px on a real desktop) cannot be measured here: check the cause.
        page = self.open_page()
        page.wait_for_selector(".noc-navbar-controls")
        self.assertEqual(page.evaluate("getComputedStyle(document.documentElement).scrollbarGutter"), "stable")

    def test_default_target_skips_devices_that_report_nothing_but_ping(self):
        page = self.open_page()
        page.wait_for_selector(".noc-navbar-controls")
        result = page.evaluate("""() => [
          firstDeviceWithData([{id: 'ping-only'}, {id: 'with-snmp', has_snmp: true}]),
          firstDeviceWithData([{id: 'a'}, {id: 'b'}]),
          firstDeviceWithData([{id: 'a', has_telemetry: true}, {id: 'b', has_snmp: true}]),
          firstDeviceWithData([])]""")
        self.assertEqual(result, ["with-snmp", "a", "a", None])

    def test_default_target_is_all_machines_when_there_is_more_than_one(self):
        page = self.open_page(remember_machine=False)
        page.wait_for_selector(".noc-navbar-controls")
        result = page.evaluate("""() => [
          pickDefaultDevice([{id: 'a'}, {id: 'b'}]),
          pickDefaultDevice([{id: 'only', has_snmp: true}]),
          pickDefaultDevice([])]""")
        self.assertEqual(result, ["*", "only", None])


if __name__ == "__main__":
    unittest.main()
