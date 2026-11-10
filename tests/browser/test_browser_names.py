"""Browser test: the jump button of Overview follows the name of the Bandwidth tab for every target (K114). Optional."""

import unittest

from .support import BrowserTestCase


class JumpLabelsFollowTheTabTest(BrowserTestCase):
    WITH_USER = False

    def test_the_bandwidth_jump_button_names_the_current_tab_for_every_target(self):
        page = self.open_page()
        page.wait_for_selector("#device-select option", state="attached")
        ids = page.evaluate("[...document.querySelectorAll('#device-select option')].map(o => o.value)")
        self.assertGreaterEqual(len(ids), 3)
        seen = set()
        for device_id in ids:
            page.select_option("#device-select", device_id)
            page.wait_for_timeout(800)
            tab = page.inner_text("#nav-tab-bandwidth-label").strip()
            button = page.evaluate("document.getElementById('overview-bw-jump').textContent.replace(/\\s+/g, ' ').trim()")
            self.assertEqual(button, tab + " →", device_id)
            seen.add(tab)
        self.assertGreaterEqual(len(seen), 2)  # router and host/DNS targets really differ


if __name__ == "__main__":
    unittest.main()
