"""Browser test (U3): the machine picked last is still selected when the page is opened again without a link to it.

Covers a reopened address, a hash with a view but no device, and a stored id that no longer exists. Optional (Playwright)."""

import unittest

from .support import BrowserTestCase


class RememberDeviceTest(BrowserTestCase):
    WITH_USER = False

    def ready(self, page):
        page.wait_for_function("document.querySelectorAll('#device-select option').length > 1")

    def pick_other(self, page):
        ids = page.evaluate("[...document.querySelectorAll('#device-select option')].map(o => o.value)")
        other = [i for i in ids if i != page.input_value("#device-select")][-1]
        page.select_option("#device-select", other)
        page.wait_for_function("STATE.deviceId === %r" % other)
        return other

    def test_reopening_the_address_keeps_the_machine(self):
        page = self.open_page()
        self.ready(page)
        other = self.pick_other(page)
        page.goto(self.server.base_url + "/")
        self.ready(page)
        self.assertEqual(page.input_value("#device-select"), other)

    def test_a_view_link_without_a_device_keeps_the_machine(self):
        page = self.open_page()
        self.ready(page)
        other = self.pick_other(page)
        page.goto(self.server.base_url + "/#/topology")
        self.ready(page)
        page.wait_for_function("STATE.deviceId === %r" % other)
        self.assertEqual(page.input_value("#device-select"), other)

    def test_a_link_with_a_device_wins_over_the_remembered_one(self):
        page = self.open_page()
        self.ready(page)
        other = self.pick_other(page)
        ids = page.evaluate("[...document.querySelectorAll('#device-select option')].map(o => o.value)")
        wanted = [i for i in ids if i != other][0]
        page.goto(self.server.base_url + "/#/overview?device=" + wanted)
        self.ready(page)
        page.wait_for_function("STATE.deviceId === %r" % wanted)

    def test_a_remembered_machine_that_is_gone_falls_back_to_the_default(self):
        page = self.open_page()
        self.ready(page)
        default = page.input_value("#device-select")
        page.evaluate("localStorage.setItem('mini-noc-device', 'no-such-device')")
        page.goto(self.server.base_url + "/")
        self.ready(page)
        self.assertEqual(page.input_value("#device-select"), default)


if __name__ == "__main__":
    unittest.main()
