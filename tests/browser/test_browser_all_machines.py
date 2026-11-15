"""Browser test (U1): the "All machines" entry of the Target list.

Fleet views stay as they are, the views that show one machine ask for one, the choice survives a reload and a link
(`device=all`), and picking a machine from the card goes back to normal. Optional (Playwright)."""

import unittest

from .support import BrowserTestCase


class AllMachinesTest(BrowserTestCase):
    WITH_USER = False

    def ready(self, path="/"):
        page = self.open_page(path=path)
        page.wait_for_function("document.querySelectorAll('#device-select option').length > 2 && STATE.cacheDeviceDetail !== undefined")
        return page

    def pick_all(self, page):
        page.select_option("#device-select", "*")
        page.wait_for_function("STATE.allMachines === true")

    def test_the_list_starts_with_all_machines_and_a_machine_is_still_the_default(self):
        page = self.ready()
        self.assertEqual(page.evaluate("document.querySelector('#device-select option').value"), "*")
        self.assertFalse(page.evaluate("STATE.allMachines"))
        self.assertNotEqual(page.input_value("#device-select"), "*")

    def test_per_machine_views_ask_for_a_machine(self):
        page = self.ready()
        self.pick_all(page)
        for tab in ("bandwidth", "flows", "syslog", "timeline"):
            page.click("button[data-tab=tab-%s]" % tab)
            self.assertTrue(page.is_visible("#tab-%s .pick-machine" % tab), tab)
            self.assertFalse(page.is_visible("#tab-%s .pick-machine ~ section" % tab), tab)
            self.assertGreater(page.locator("#tab-%s .pick-machine-btn" % tab).count(), 1, tab)

    def test_fleet_views_and_the_overview_keep_working(self):
        page = self.ready()
        self.pick_all(page)
        self.assertTrue(page.is_visible("#tab-overview .noc-global-ribbon"))
        self.assertFalse(page.is_visible("#tab-overview .overview-split-layout"))
        for tab in ("topology", "health"):
            page.click("button[data-tab=tab-%s]" % tab)
            self.assertFalse(page.is_visible("#tab-%s .pick-machine" % tab) if page.locator("#tab-%s .pick-machine" % tab).count() else False)

    def test_picking_from_the_card_selects_that_machine(self):
        page = self.ready()
        self.pick_all(page)
        page.click("button[data-tab=tab-syslog]")
        button = page.locator("#tab-syslog .pick-machine-btn").nth(1)
        wanted = button.get_attribute("data-id")
        button.click()
        page.wait_for_function("STATE.deviceId === %r" % wanted)
        self.assertFalse(page.evaluate("STATE.allMachines"))
        self.assertFalse(page.is_visible("#tab-syslog .pick-machine"))

    def test_the_choice_survives_a_reload_and_is_in_the_link(self):
        page = self.ready()
        self.pick_all(page)
        page.wait_for_function("location.hash.indexOf('device=all') !== -1")
        page.reload()
        page.wait_for_function("document.querySelectorAll('#device-select option').length > 2")
        page.wait_for_function("STATE.allMachines === true")
        self.assertEqual(page.input_value("#device-select"), "*")

    def test_a_link_with_device_all_opens_on_all_machines(self):
        page = self.open_page(path="/#/overview?device=all")
        page.wait_for_function("document.querySelectorAll('#device-select option').length > 2")
        page.wait_for_function("STATE.allMachines === true")
        self.assertEqual(page.input_value("#device-select"), "*")

    def test_topology_pulses_the_selected_machine_once(self):
        """U2: the card of the selected machine is marked for about a second, then the mark goes."""
        page = self.ready()
        device = page.evaluate("STATE.deviceId")
        # Record the mark instead of racing a one-second window on a busy machine
        page.evaluate("""() => { window.__pulsed = false; new MutationObserver(() => {
            if (document.querySelector('.topo-node.pulse')) window.__pulsed = true; })
            .observe(document.body, {subtree: true, attributes: true, attributeFilter: ['class']}); }""")
        page.click("button[data-tab=tab-topology]")
        page.wait_for_function("window.__pulsed === true")
        page.wait_for_selector(".topo-node[data-node-id=%r]:not(.pulse)" % device, state="attached")

    def test_the_target_list_lets_go_of_the_focus_after_a_pointer_pick(self):
        page = self.ready()
        other = page.evaluate("[...document.querySelectorAll('#device-select option')].map(o => o.value).filter(v => v !== '*' && v !== STATE.deviceId)[0]")
        page.evaluate("document.getElementById('device-select').focus()")
        page.dispatch_event("#device-select", "pointerdown")
        page.select_option("#device-select", other)
        page.wait_for_function("STATE.deviceId === %r" % other)
        self.assertFalse(page.evaluate("document.activeElement.id === 'device-select'"))

    def test_the_target_list_keeps_the_focus_after_a_keyboard_pick(self):
        page = self.ready()
        other = page.evaluate("[...document.querySelectorAll('#device-select option')].map(o => o.value).filter(v => v !== '*' && v !== STATE.deviceId)[0]")
        page.evaluate("document.getElementById('device-select').focus()")
        page.dispatch_event("#device-select", "keydown")
        page.select_option("#device-select", other)
        page.wait_for_function("STATE.deviceId === %r" % other)
        self.assertTrue(page.evaluate("document.activeElement.id === 'device-select'"))

    def test_topology_does_not_pulse_with_all_machines(self):
        page = self.ready()
        self.pick_all(page)
        page.click("button[data-tab=tab-topology]")
        page.wait_for_selector(".topo-node", state="attached")
        page.wait_for_timeout(300)
        self.assertEqual(page.locator(".topo-node.pulse").count(), 0)


if __name__ == "__main__":
    unittest.main()
