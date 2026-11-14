"""Browser test: a rate shows its share of the link when the speed is known, and nothing extra when it is not. Optional (Playwright)."""

import unittest

from .support import BrowserTestCase


class LinkShareTest(BrowserTestCase):
    def fmt(self, page, bps, speed):
        return page.evaluate("([b, s]) => formatBpsOfLink(b, s)", [bps, speed])

    def test_the_share_is_shown_only_when_the_speed_is_known(self):
        page = self.open_page()
        page.wait_for_function("typeof formatBpsOfLink === 'function'")
        self.assertEqual(self.fmt(page, 42_000_000, 1_000_000_000), "42.00 Mbit/s (4.2%)")
        self.assertEqual(self.fmt(page, 500_000_000, 1_000_000_000), "500.00 Mbit/s (50%)")
        self.assertEqual(self.fmt(page, 10_000, 1_000_000_000), "10.0 kbit/s (<0.1%)")
        self.assertEqual(self.fmt(page, 42_000_000, 0), "42.00 Mbit/s")
        self.assertEqual(self.fmt(page, 42_000_000, None), "42.00 Mbit/s")
        self.assertEqual(self.fmt(page, None, 1_000_000_000), "gap / reset")


if __name__ == "__main__":
    unittest.main()
