"""Browser test (U5): picking another machine fetches what depends on it and leaves the fleet-wide panels alone.

Measured before the change: 20 calls, 11 of them fleet endpoints, one after another. Optional (Playwright)."""

import unittest
from urllib.parse import urlparse

from .support import BrowserTestCase

FLEET = ("/api/alerts/noise", "/api/availability-report", "/api/capacity", "/api/coverage", "/api/health", "/api/host-health",
         "/api/icmp-health", "/api/nodes", "/api/resources/fleet")


class SwitchRequestsTest(BrowserTestCase):
    WITH_USER = False

    def switch(self, tab):
        page = self.open_page(path="/#/" + tab)
        page.wait_for_function("document.querySelectorAll('#device-select option').length > 1 && STATE.cacheDeviceDetail !== undefined")
        page.wait_for_timeout(1500)
        current = page.input_value("#device-select")
        ids = page.evaluate("[...document.querySelectorAll('#device-select option')].map(o => o.value).filter(v => v !== '*')")
        other = [i for i in ids if i != current][0]
        paths = []
        page.on("request", lambda r: paths.append(urlparse(r.url).path) if urlparse(r.url).path.startswith("/api/") else None)
        page.select_option("#device-select", other)
        page.wait_for_function("STATE.cacheDeviceDetail && STATE.cacheDeviceDetail.id === %r" % other)
        page.wait_for_timeout(1500)
        return other, paths

    def test_fleet_panels_are_not_refetched_and_device_panels_are(self):
        for tab in ("overview", "health", "topology"):
            other, paths = self.switch(tab)
            for fleet in FLEET:
                self.assertNotIn(fleet, paths, "%s was refetched on %s" % (fleet, tab))
            self.assertIn("/api/devices/" + other, paths, tab)
            self.assertTrue(any(p.startswith("/api/devices/" + other + "/logs") for p in paths), tab)

    def test_the_view_still_shows_the_new_machine_after_a_switch(self):
        page = self.open_page()
        page.wait_for_function("document.querySelectorAll('#device-select option').length > 1 && STATE.cacheDeviceDetail !== undefined")
        ids = page.evaluate("[...document.querySelectorAll('#device-select option')].map(o => o.value).filter(v => v !== '*')")
        for device in ids[:4]:
            page.select_option("#device-select", device)
            page.wait_for_function("STATE.cacheDeviceDetail && STATE.cacheDeviceDetail.id === %r" % device)
            self.assertEqual(page.evaluate("STATE.deviceId"), device)


if __name__ == "__main__":
    unittest.main()
