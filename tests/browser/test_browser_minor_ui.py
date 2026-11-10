"""Browser test: the small items of the interface backlog (K119, point 9). Optional (Playwright + Chromium).

Topology search with no match, inventory messages in their own section, the Monitored Nodes indicator while a refresh is held,
the page at 200 % and 300 % browser zoom, and what a screen reader is told (the accessibility tree).
"""

import json
import unittest

from .support import BrowserTestCase


class NoSnmpBadgeTest(BrowserTestCase):
    WITH_USER = False

    def test_a_host_without_snmp_does_not_claim_its_interface_source_is_ok(self):
        page = self.open_page()
        page.wait_for_function("document.querySelectorAll('#device-select option').length > 1")
        page.select_option("#device-select", "demo-laptop")
        page.keyboard.press("3")
        page.wait_for_function("document.getElementById('device-freshness').textContent.indexOf('n/a') >= 0")
        self.assertIn("no SNMP", page.inner_text("#device-freshness"))
        page.select_option("#device-select", "demo-switch")
        page.wait_for_function("document.getElementById('device-freshness').textContent.indexOf('n/a') < 0")
        # A device with SNMP keeps the real status of its interface data (the demo switch is seeded as down)
        self.assertIn("Source: ", page.inner_text("#device-freshness"))


class TopologyEmptySearchTest(BrowserTestCase):
    WITH_USER = False

    def open_topology(self):
        page = self.open_page()
        page.keyboard.press("2")
        page.wait_for_selector(".topo-node", state="attached")
        return page

    def test_the_search_placeholder_is_not_cut(self):
        page = self.open_topology()
        spare = page.evaluate("""() => {
            const el = document.getElementById('topo-search-input');
            const cs = getComputedStyle(el);
            const ctx = document.createElement('canvas').getContext('2d');
            ctx.font = cs.font;
            const text = ctx.measureText(el.placeholder).width;
            const room = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
            return room - text;
        }""")
        # 24 px of margin: another font on the visitor's machine must not cut the end of the text
        self.assertGreaterEqual(spare, 24, "the placeholder has no room to spare in the field")

    def test_a_search_with_no_match_says_so_and_can_be_cleared(self):
        page = self.open_topology()
        self.assertFalse(page.is_visible("#topo-no-match"))
        page.fill("#topo-search-input", "zzzz-nothing")
        page.wait_for_selector("#topo-no-match", state="visible")
        self.assertIn("zzzz-nothing", page.inner_text("#topo-no-match"))
        page.click("#topo-no-match button")
        self.assertEqual(page.input_value("#topo-search-input"), "")
        self.assertFalse(page.is_visible("#topo-no-match"))
        self.assertEqual(page.locator(".topo-node.node-dimmed").count(), 0)

    def test_a_filter_with_no_match_says_so_too(self):
        page = self.open_topology()
        page.fill("#topo-search-input", "192.168.88")
        page.wait_for_timeout(200)
        self.assertFalse(page.is_visible("#topo-no-match"))
        page.fill("#topo-search-input", "pihole")
        page.click("[data-topo-filter='proxmox']")  # the DNS server is not a hypervisor
        page.wait_for_selector("#topo-no-match", state="visible")
        self.assertIn("filter", page.inner_text("#topo-no-match").lower())
        page.click("#topo-no-match button")
        self.assertEqual(page.get_attribute("[data-topo-filter='all']", "aria-pressed"), "true")
        self.assertFalse(page.is_visible("#topo-no-match"))


class InventoryMessagesTest(BrowserTestCase):
    SERVER_PER_TEST = True

    def open_inventory(self):
        page = self.open_page()
        self.sign_in(page)
        page.wait_for_selector("#audit-box:not(.hidden)")
        page.click("[data-settings-section='inventory']")
        page.wait_for_selector(".inv-save-btn")
        return page

    def test_saving_a_target_reports_in_the_inventory_section(self):
        page = self.open_inventory()
        page.locator(".inv-name-input").first.fill("Renamed by the test")
        page.locator(".inv-save-btn").first.click()
        page.wait_for_function("document.getElementById('settings-inventory-msg').textContent.indexOf('updated') !== -1")
        self.assertEqual(page.inner_text("#settings-msg").strip(), "")

    def test_removing_a_target_reports_in_the_inventory_section(self):
        page = self.open_inventory()
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator(".inv-del-btn").first.click()
        page.wait_for_function("document.getElementById('settings-inventory-msg').textContent.indexOf('removed') !== -1")
        self.assertEqual(page.inner_text("#settings-msg").strip(), "")


class HeldRefreshTest(BrowserTestCase):
    WITH_USER = False

    def test_monitored_nodes_keeps_up_while_a_typed_note_holds_the_panels(self):
        page = self.open_page()
        page.wait_for_function("document.getElementById('kpi-global-nodes-val').textContent.indexOf('/') !== -1")
        before = page.inner_text("#kpi-global-nodes-val")

        def all_down(route):
            response = route.fetch()
            data = response.json()
            for device in data["devices"]:
                device["reachable"] = False
            route.fulfill(response=response, body=json.dumps(data), headers={"content-type": "application/json"})

        page.route("**/api/overview*", all_down)
        card = page.locator("#active-alerts-list .alert-item-card").first
        card.locator(".alert-ack-note").fill("typing a note")
        page.evaluate("STATE.countdownRemaining = 1")
        page.wait_for_function("document.getElementById('refresh-countdown').textContent === 'hold'")
        page.wait_for_function("document.getElementById('kpi-global-nodes-val').textContent.indexOf('0 /') === 0")
        self.assertNotEqual(page.inner_text("#kpi-global-nodes-val"), before)
        self.assertEqual(card.locator(".alert-ack-note").input_value(), "typing a note")


class BrowserZoomTest(BrowserTestCase):
    WITH_USER = False
    TABS = ("tab-overview", "tab-topology", "tab-bandwidth", "tab-flows", "tab-syslog", "tab-timeline", "tab-health", "tab-settings")

    def test_no_sideways_scroll_at_200_and_300_percent_zoom(self):
        # A browser zoom of N % on a 1440 x 900 window is a viewport of 1440/N x 900/N CSS pixels at N/100 device pixels.
        for percent, size in ((200, (720, 450)), (300, (480, 300))):
            context = self.browser.new_context(
                viewport={"width": size[0], "height": size[1]}, device_scale_factor=percent / 100)
            self.addCleanup(context.close)
            page = context.new_page()
            page.set_default_timeout(10000)
            page.goto(self.server.base_url)
            page.wait_for_selector("#active-alerts-list .alert-item-card")
            for tab in self.TABS:
                page.locator("button[data-tab='%s']" % tab).evaluate("el => el.click()")
                page.wait_for_timeout(300)
                overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                self.assertLessEqual(overflow, 0, "%d %% zoom, %s" % (percent, tab))
            # the header must not eat a short window: it scrolls away (K111) instead of staying fixed
            self.assertNotEqual(page.evaluate("getComputedStyle(document.querySelector('header.noc-navbar')).position"), "fixed")


class AccessibilityTreeTest(BrowserTestCase):
    WITH_USER = False

    def test_the_page_tells_a_screen_reader_its_landmarks_and_controls(self):
        page = self.open_page()
        page.wait_for_selector("#active-alerts-list .alert-item-card")
        tree = page.locator("body").aria_snapshot()
        for expected in ('heading "PingPoint NOC operations dashboard" [level=1]',
                         'navigation "NOC Dashboard Views"',
                         'main',
                         'link "Skip to content"',
                         'button "Refresh"',
                         'combobox "Monitored device"',
                         'group "Time window selection"',
                         'region "Active alerts"'):
            self.assertIn(expected, tree)

    def test_the_status_of_a_device_is_text_not_only_a_colour(self):
        page = self.open_page()
        page.wait_for_selector("#welcome-devices .avail-pill")
        names = page.eval_on_selector_all(
            "#welcome-devices .avail-pill", "els => els.map(e => e.textContent.replace(/\\s+/g, ' ').trim())")
        self.assertTrue(names)
        for name in names:
            self.assertRegex(name, r", (ok|stale|down|indeterminate)$")

    def test_every_button_and_link_has_a_name_in_every_view(self):
        page = self.open_page()
        for tab in ("tab-overview", "tab-topology", "tab-bandwidth", "tab-flows", "tab-syslog", "tab-timeline",
                    "tab-health", "tab-settings"):
            page.locator("button[data-tab='%s']" % tab).evaluate("el => el.click()")
            page.wait_for_timeout(300)
            unnamed = page.evaluate(
                """(tab) => [...document.querySelectorAll('#' + tab + ' button, #' + tab + ' a[href], #' + tab + ' input, #' + tab + ' select')]
                  .filter(el => el.offsetParent !== null && el.type !== 'hidden')
                  .filter(el => !(el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.title
                                  || (el.textContent || '').trim() || (el.labels && el.labels.length) || el.placeholder
                                  || el.closest('label')))
                  .map(el => el.id || el.className)""", tab)
            self.assertEqual(unnamed, [], tab)


if __name__ == "__main__":
    unittest.main()
