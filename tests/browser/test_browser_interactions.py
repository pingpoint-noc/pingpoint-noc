"""Browser test: undo in the topology, the right-click menu, the optional green banner and hover descriptions."""

from tests.browser.support import BrowserTestCase


class InteractionsTest(BrowserTestCase):
    def test_ctrl_z_undoes_a_card_move_and_ctrl_shift_z_redoes_it(self):
        page = self.open_page()
        page.locator("button[data-tab='tab-topology']").click()
        page.locator(".topo-node").first.wait_for()
        page.wait_for_timeout(300)
        node_id = page.locator(".topo-node").first.get_attribute("data-node-id")
        node = page.locator('.topo-node[data-node-id="%s"]' % node_id)
        before = node.bounding_box()
        node.focus()
        page.keyboard.press("Shift+ArrowRight")
        page.wait_for_function("document.getElementById('topo-undo').disabled === false")
        page.wait_for_timeout(150)
        moved = node.bounding_box()
        self.assertNotAlmostEqual(moved["x"], before["x"], delta=1.0)
        page.locator("#topology-viewport").focus()
        page.keyboard.press("Control+z")
        page.wait_for_timeout(150)
        restored = node.bounding_box()
        self.assertAlmostEqual(restored["x"], before["x"], delta=1.5)
        self.assertFalse(page.locator("#topo-redo").is_disabled())
        page.keyboard.press("Control+Shift+z")
        page.wait_for_timeout(150)
        again = node.bounding_box()
        self.assertAlmostEqual(again["x"], moved["x"], delta=1.5)

    def test_undo_button_and_a_text_field_keep_their_own_undo(self):
        page = self.open_page()
        page.locator("button[data-tab='tab-topology']").click()
        page.locator(".topo-node").first.wait_for()
        self.assertTrue(page.locator("#topo-undo").is_disabled())
        search = page.locator("#topo-search, #topology-search").first
        if search.count():
            search.fill("abc")
            page.keyboard.press("Control+z")
            self.assertTrue(page.locator("#topo-undo").is_disabled())

    def test_right_click_on_an_alert_opens_a_menu_with_its_actions(self):
        page = self.open_page()
        card = page.locator(".alert-item-card").first
        card.wait_for()
        card.locator(".alert-item-message").click(button="right")
        menu = page.locator(".context-menu")
        menu.wait_for()
        labels = menu.locator(".context-menu-item").all_inner_texts()
        self.assertIn("Open timeline", labels)
        self.assertIn("Copy the message", labels)
        page.keyboard.press("Escape")
        page.wait_for_function("document.querySelector('.context-menu') === null")

    def test_right_click_on_a_device_pill_offers_the_device(self):
        page = self.open_page()
        pill = page.locator(".avail-pill").first
        pill.wait_for()
        pill.click(button="right")
        menu = page.locator(".context-menu")
        menu.wait_for()
        self.assertIn("Show this device", menu.locator(".context-menu-item").all_inner_texts())

    def test_right_click_on_plain_text_keeps_the_browser_menu(self):
        page = self.open_page()
        page.locator("#main-content").wait_for()
        prevented = page.evaluate(
            """() => { const e = new MouseEvent('contextmenu', {bubbles: true, cancelable: true});
                       document.querySelector('.noc-navbar').dispatchEvent(e); return e.defaultPrevented; }"""
        )
        self.assertFalse(prevented)

    def test_main_buttons_and_cards_have_hover_descriptions(self):
        page = self.open_page()
        page.locator("#kpi-global-nodes").wait_for()
        for selector in ("#kpi-global-nodes .kpi-label", ".nav-tab-btn[data-tab='tab-topology']", "#topo-zoom-fit"):
            self.assertTrue(page.locator(selector).first.get_attribute("title"), selector)

    def test_the_green_banner_can_be_turned_off_in_preferences(self):
        page = self.open_page()
        page.locator("#alerts-section").wait_for()
        # the demo has alerts firing: clear the cache of choice and check the preference is stored and read back
        page.evaluate("localStorage.setItem('mini-noc-pref-ok-banner', 'false')")
        page.reload()
        page.locator("#main-content").wait_for()
        self.assertFalse(page.evaluate("STATE.showOkBanner"))
        page.locator("button[data-tab='tab-settings']").click()
        page.locator("#settings-nav-bar [data-settings-section='account']").click()
        self.assertFalse(page.locator("#pref-ok-banner").is_checked())
