"""Browser test: a device that gives no SNMP counters and no flows says so instead of drawing a flat 0 bit/s chart."""

from .support import BrowserTestCase


class NoTrafficDeviceTest(BrowserTestCase):
    WITH_USER = False

    def test_timeline_of_a_ping_only_device_has_a_note_instead_of_a_chart(self):
        page = self.open_page()
        page.wait_for_function("STATE.cacheDevices && STATE.cacheDevices.length > 0")
        # A device nobody collects traffic for: no SNMP, no IPFIX, no samples
        page.route("**/api/devices/*/timeline*", lambda route: route.fulfill(
            content_type="application/json",
            body='{"device_id":"x","generated_utc":1,"as_of_utc":null,"at_utc":null,"window_seconds":900,"start_ts_utc":0,'
                 '"end_ts_utc":900,"if_index":null,"primary_source":"snmp","telemetry_mode":"snmp",'
                 '"sources":{"snmp":{"status":"na","last_success_utc":null},"syslog":{"status":"na","last_success_utc":null},'
                 '"ipfix":{"status":"na","last_success_utc":null},"dns":{"status":"na","last_success_utc":null}},'
                 '"bandwidth":{"if_index":null,"sample_count":0,"points":[]},'
                 '"logs":{"count":0,"events":[]},"talkers":{"total_flows":0,"top":[]},"hosts":[]}'))
        page.evaluate("switchTab('tab-timeline')")
        page.wait_for_function("!document.getElementById('tl-no-traffic').classList.contains('hidden')")
        self.assertTrue(page.locator("#tl-chart-box").evaluate("e => e.classList.contains('hidden')"))
