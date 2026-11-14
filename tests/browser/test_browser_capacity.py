"""Browser test: p95 chip and line, Top Interfaces p95 column, forecast and the Capacity panel.

The demo database holds 20 polls, too few for a p95 (needs 20 valid intervals) and for any forecast,
so the class injects synthetic history straight into SQLite: 4 days of 15 s samples on a 100 Mbit/s
interface whose daily p95 grows by 10 Mbit/s per day, and 72 hourly capacity samples.
"""

import sqlite3
import time
import unittest

from .support import BrowserTestCase

DEVICE = "edge-router-1"
FAST_IF = 99
HOST = "mini-noc-host"
VIEWPORTS = (320, 390, 768, 1440)


def inject_history(db_path):
    now = int(time.time())
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO interfaces (device_id, if_index, if_descr, admin_status, oper_status, speed_bps, updated_utc)"
            " VALUES (?, ?, 'ether-fast', 'up', 'up', 100000000, ?)",
            (DEVICE, FAST_IF, now),
        )
        rows = []
        counter_in = 0
        counter_out = 0
        total = 4 * 5760
        for i in range(total, -1, -1):
            ts = now - i * 15
            day = i // 5760  # 0 = last 24 h
            rate_bps = 50_000_000 - 10_000_000 * day
            counter_in += int(rate_bps * 15 / 8)
            counter_out += int(rate_bps * 15 / 8 / 4)
            rows.append((DEVICE, FAST_IF, ts, 1000 + (total - i) * 1500, counter_in, counter_out))
        conn.executemany(
            "INSERT INTO interface_samples (device_id, if_index, ts_utc, sys_uptime_ticks, in_octets, out_octets)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        metrics = []
        for h in range(72, -1, -1):
            step = 72 - h
            ts = now - h * 3600
            metrics.append((HOST, ts, "capacity_db_bytes", "", 100_000_000.0 + step * 1_000_000))
            metrics.append((HOST, ts, "capacity_wal_bytes", "", 0.0))
            metrics.append((HOST, ts, "capacity_disk_total_bytes", "", 10_000_000_000.0))
            metrics.append((HOST, ts, "capacity_disk_free_bytes", "", 5_000_000_000.0 - step * 2_000_000))
        conn.executemany(
            "INSERT INTO device_metrics (device_id, ts_utc, metric_name, label, value) VALUES (?, ?, ?, ?, ?)", metrics
        )
        conn.execute(
            "UPDATE collector_status SET last_success_utc = ? WHERE collector_name = 'retention'", (now,)
        )
        conn.commit()
    finally:
        conn.close()


def dataset_labels(page):
    return page.evaluate(
        "(CHART_INSTANCES['bw-canvas'] ? CHART_INSTANCES['bw-canvas'].data.datasets.map(function (d) { return d.label; }) : [])"
    )


class CapacityTest(BrowserTestCase):
    WITH_USER = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        inject_history(cls.server.db_path)

    def open_bandwidth(self, if_index, color_scheme="dark", width=1400):
        page = self.open_page(color_scheme=color_scheme)
        page.set_viewport_size({"width": width, "height": 900})
        page.keyboard.press("3")
        page.wait_for_selector("#bw-if-select option[value='%d']" % if_index, state="attached")
        page.select_option("#bw-if-select", str(if_index))
        # The box says which interface it describes, so the wait cannot be satisfied by the previous one.
        page.wait_for_function(
            "document.getElementById('bw-capacity').getAttribute('data-if') === '%d'" % if_index
        )
        return page

    def test_p95_chip_toggles_two_reference_lines(self):
        page = self.open_bandwidth(FAST_IF)
        page.wait_for_function("CHART_INSTANCES['bw-canvas'] !== undefined")
        self.assertNotIn("p95 RX", dataset_labels(page))
        page.check("#chk-show-p95")
        labels = dataset_labels(page)
        self.assertIn("p95 RX", labels)
        self.assertIn("p95 TX", labels)
        flat = page.evaluate(
            "(function () { var d = CHART_INSTANCES['bw-canvas'].data.datasets.filter(function (x) { return x.label === 'p95 RX'; })[0];"
            " return d.data[0] === d.data[d.data.length - 1]; })()"
        )
        self.assertTrue(flat)
        page.uncheck("#chk-show-p95")
        self.assertNotIn("p95 RX", dataset_labels(page))

    def test_known_speed_shows_occupancy_and_a_forecast_with_its_range(self):
        page = self.open_bandwidth(FAST_IF)
        text = page.text_content("#bw-capacity")
        self.assertIn("p95 (15m)", text)
        self.assertIn("p95 (7 days)", text)
        self.assertRegex(text, r"Occupancy at p95: RX [0-9.]+% . TX [0-9.]+% of 100\.00 Mbit/s")
        self.assertRegex(text, r"80% of capacity in about [0-9.]+ days")
        self.assertRegex(text, r"95% in about [0-9.]+ days")
        self.assertIn("samples up to", text)

    def test_lab_interface_says_link_speed_unknown_and_never_a_percentage(self):
        page = self.open_bandwidth(3)
        text = page.text_content("#bw-capacity")
        self.assertIn("link speed unknown", text)
        self.assertNotIn("Occupancy at p95", text)
        self.assertIn("Forecast: Link speed unknown", text)

    def test_all_interfaces_asks_for_one_interface(self):
        page = self.open_page()
        page.keyboard.press("3")
        page.wait_for_selector("#bw-if-select option", state="attached")
        page.select_option("#bw-if-select", "0")
        page.wait_for_function("document.getElementById('bw-capacity').textContent.indexOf('select one interface') !== -1")

    def test_top_interfaces_has_a_p95_column_for_the_selected_range(self):
        page = self.open_page()
        page.wait_for_selector("#overview-top-if-tbody tr.top-if-row", state="attached")  # folded on a phone since K116
        self.assertEqual(page.text_content("#top-if-p95-head"), "p95 (15m)")
        page.click(".range-pill-btn[data-range='1h']")
        page.wait_for_function("document.getElementById('top-if-p95-head').textContent === 'p95 (1h)'")
        cells = page.eval_on_selector_all("#overview-top-if-tbody td.top-if-p95", "els => els.map(e => e.textContent)")
        self.assertTrue(len(cells) >= 1)
        fast = [c for c in cells if "%" in c]
        self.assertTrue(fast, cells)  # the 100 Mbit/s interface shows its percentage

    def test_health_capacity_panel_has_growth_days_and_freshness(self):
        page = self.open_page()
        page.keyboard.press("7")
        page.wait_for_function("document.getElementById('capacity-cards').textContent.indexOf('Days to the size cap') !== -1")
        text = page.text_content("#capacity-cards")
        self.assertRegex(text, r"\+[0-9.]+ MB/day")
        self.assertRegex(text, r"[0-9.]+ days")
        self.assertIn("Days until the disk is full", text)
        page.click("#health-group-trends > summary")  # Capacity & Trends starts folded since K113
        self.assertIn("SOURCE: OK", page.inner_text("#capacity-freshness").upper())
        self.assertNotEqual(page.inner_text("#capacity-status"), "NO FORECAST")

    def test_no_sideways_scroll_and_big_enough_targets_in_both_themes(self):
        for scheme in ("dark", "light"):
            for width in VIEWPORTS:
                page = self.open_bandwidth(FAST_IF, color_scheme=scheme, width=width)
                page.check("#chk-show-p95")
                self.assertLessEqual(
                    page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0,
                    "bandwidth %s %d" % (scheme, width),
                )
                box = page.locator("label.toggle-chip:has(#chk-show-p95)").bounding_box()
                self.assertGreaterEqual(box["height"], 24)
                self.assertGreaterEqual(box["width"], 24)
                inside = page.evaluate(
                    "(function () { var r = document.getElementById('bw-capacity').getBoundingClientRect();"
                    " return r.left >= 0 && r.right <= document.documentElement.clientWidth; })()"
                )
                self.assertTrue(inside, "bw-capacity %s %d" % (scheme, width))
                page.evaluate("document.activeElement.blur()")  # shortcuts are ignored while a field has focus
                page.keyboard.press("7")
                page.wait_for_function("document.getElementById('capacity-cards').textContent.indexOf('Days to the size cap') !== -1")
                self.assertLessEqual(
                    page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0,
                    "health %s %d" % (scheme, width),
                )
                page.keyboard.press("1")
                page.wait_for_selector("#overview-top-if-tbody tr.top-if-row", state="attached")  # folded on a phone since K116
                self.assertLessEqual(
                    page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0,
                    "overview %s %d" % (scheme, width),
                )


if __name__ == "__main__":
    unittest.main()
