"""Browser test: the topology map follows the window (fits again after a resize unless the operator moved it)."""

from .support import BrowserTestCase


class TopologyResizeTest(BrowserTestCase):
    def open_topology(self):
        page = self.open_page()
        page.set_viewport_size({"width": 1400, "height": 900})
        page.click("button[data-tab=tab-topology]")
        page.wait_for_selector("#topology-viewport .topo-node")
        page.wait_for_timeout(400)
        return page

    def scale(self, page):
        return page.evaluate("TOPOLOGY_STATE.scale")

    def all_inside(self, page):
        return page.evaluate("""() => {
            const v = document.getElementById('topology-viewport').getBoundingClientRect();
            return [...document.querySelectorAll('#topology-viewport .topo-node')].every(n => {
                const r = n.getBoundingClientRect();
                return r.left >= v.left - 1 && r.right <= v.right + 1 && r.top >= v.top - 1 && r.bottom <= v.bottom + 1;
            });
        }""")

    def test_every_card_stays_inside_the_panel_after_resizing(self):
        page = self.open_topology()
        self.assertTrue(self.all_inside(page))
        for width, height in ((700, 800), (1800, 1000), (500, 700), (1100, 650)):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(500)
            self.assertTrue(self.all_inside(page), "cards leave the panel at %dx%d" % (width, height))

    def test_a_view_moved_by_hand_is_left_alone_until_fit_is_pressed(self):
        page = self.open_topology()
        page.keyboard.press("Tab")
        page.evaluate("topologyZoomCentered(1.5)")
        zoomed = self.scale(page)
        page.set_viewport_size({"width": 1000, "height": 800})
        page.wait_for_timeout(500)
        self.assertEqual(self.scale(page), zoomed)
        page.evaluate("fitTopologyView()")
        self.assertNotEqual(self.scale(page), zoomed)
        self.assertFalse(page.evaluate("TOPOLOGY_STATE.userView"))
