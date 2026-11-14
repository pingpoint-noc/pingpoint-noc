"""Browser test: the legend under the throughput chart leaves room before what sits below it. Optional (Playwright)."""

import unittest

from .support import BrowserTestCase


class LegendSpacingTest(BrowserTestCase):
    def test_the_legend_strip_has_a_gap_to_the_next_block(self):
        page = self.open_page()
        page.wait_for_selector(".chart-legend-strip", state="attached")
        gap = page.evaluate(
            """() => {
              const strip = document.querySelector('#overview-router-bw-wrap .chart-legend-strip');
              const next = strip.nextElementSibling;
              return parseFloat(getComputedStyle(strip).marginBottom) + (next ? parseFloat(getComputedStyle(next).marginTop) : 0);
            }"""
        )
        self.assertGreaterEqual(gap, 14)


if __name__ == "__main__":
    unittest.main()
