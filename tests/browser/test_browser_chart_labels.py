"""Browser test: the bandwidth chart's time axis and tooltip swatches. Optional (Playwright + Chromium).

Seen on a real lab (09-11): the X ticks repeated the date ("09/11/2026, 15:48:11 09/11/2026, ...") and
overlapped, and the tooltip swatches were white or grey, glued to the text, so Peak TX and Peak RX looked the same.
"""

import re
import unittest

from .support import BrowserTestCase

CHART = "window.CHART_INSTANCES['bw-canvas']"


class ChartLabelsTest(BrowserTestCase):
    def open_chart(self, width):
        page = self.open_page(path="/#/bandwidth?range=1h")
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_function("!!(" + CHART + " && " + CHART + ".scales && " + CHART + ".scales.x)")
        page.wait_for_timeout(300)
        return page

    def tick_labels(self, page):
        return page.evaluate("(" + CHART + ").scales.x.ticks.map(t => (" + CHART + ").scales.x.options.ticks.callback.call((" + CHART + ").scales.x, t.value))")

    def test_a_one_day_window_shows_only_the_time_on_the_axis(self):
        for width in (390, 1400):
            with self.subTest(width=width):
                labels = self.tick_labels(self.open_chart(width))
                self.assertGreaterEqual(len(labels), 1)
                for label in labels:
                    self.assertRegex(label, r"^\d{2}:\d{2}:\d{2}$")

    def test_ticks_fit_the_width_of_the_chart(self):
        for width in (320, 390, 768, 1400):
            with self.subTest(width=width):
                page = self.open_chart(width)
                chart_width = page.evaluate("document.getElementById('bw-canvas').clientWidth")
                labels = self.tick_labels(page)
                # ~90 px per "hh:mm:ss" tick: never more than the canvas can hold without overlapping.
                self.assertLessEqual(len(labels) * 90, max(chart_width, 180) + 90)

    def test_the_tooltip_keeps_the_full_date_and_time(self):
        page = self.open_chart(1400)
        title = page.evaluate("(" + CHART + ").options.plugins.tooltip.callbacks.title([{dataIndex: 0}])")
        self.assertTrue(re.match(r"^\d{2}/\d{2}/\d{4}, \d{2}:\d{2}:\d{2}$", title), title)

    def test_the_swatch_is_lifted_to_the_capitals_centre(self):
        # Measured at DPR 4: Chart.js left the swatch 1.1 px below the capital letters' centre. The lift is a wrapper on
        # the library's private draw method; if a Chart.js upgrade renames it, this test says so instead of the UI drifting.
        page = self.open_chart(1400)
        lifted = page.evaluate("Object.getPrototypeOf((" + CHART + ").tooltip)._drawColorBox.nocLifted === true")
        self.assertTrue(lifted)

    def test_each_swatch_is_solid_in_its_own_line_colour_and_has_a_gap(self):
        page = self.open_chart(1400)
        data = page.evaluate(
            "(function () { var c = " + CHART + "; var cb = c.options.plugins.tooltip.callbacks.labelColor;"
            " return { gap: c.options.plugins.tooltip.boxPadding,"
            " boxes: c.data.datasets.map(function (d) { return [d.label, d.borderColor, cb({dataset: d})]; }) }; })()"
        )
        self.assertGreaterEqual(data["gap"], 4)
        self.assertGreaterEqual(len(data["boxes"]), 4)  # TX, RX, Peak TX, Peak RX
        for label, line, box in data["boxes"]:
            self.assertEqual(box["backgroundColor"], line, label)
            self.assertEqual(box["borderColor"], line, label)
        colours = [box["backgroundColor"] for _, _, box in data["boxes"]]
        self.assertEqual(len(set(colours)), len(colours), "every series has its own swatch colour")


if __name__ == "__main__":
    unittest.main()
