"""Browser test: selectDevice() (used by the "Runs on Proxmox" link and the topology drawer) really changes the target."""

from .support import BrowserTestCase


class SelectDeviceTest(BrowserTestCase):
    def test_selecting_a_known_device_changes_the_target_and_shows_the_overview(self):
        page = self.ready()
        ids = page.evaluate("[...document.querySelectorAll('#device-select option')].map(o => o.value)")
        other = [i for i in ids if i != page.input_value("#device-select")][0]
        page.click("button[data-tab=tab-topology]")
        self.assertTrue(page.evaluate("selectDevice(%r)" % other))
        self.assertEqual(page.input_value("#device-select"), other)
        self.assertEqual(page.evaluate("STATE.deviceId"), other)
        self.assertTrue(page.is_visible("#tab-overview"))

    def test_an_unknown_device_says_so_and_changes_nothing(self):
        page = self.ready()
        before = page.input_value("#device-select")
        self.assertFalse(page.evaluate("selectDevice('no-such-device')"))
        self.assertEqual(page.input_value("#device-select"), before)
        page.wait_for_selector("#toast-container .toast:has-text('Device not found')")

    def ready(self):
        page = self.open_page()
        page.wait_for_function("document.querySelectorAll('#device-select option').length > 1")
        return page
