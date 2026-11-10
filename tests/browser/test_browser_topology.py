"""Browser test: the Topology view (cards, filters, drag, selection, zoom/pan, keyboard, touch, responsive layout, icons)."""

import unittest

from .support import BrowserTestCase

OFFSETS_KEY = "mininoc.topology.offsets"


def open_topology(page):
    """Open the tab, wait for the cards and fit the map to the panel (the first auto-fit is deferred)."""
    if page.locator("#nav-tab-topology").is_visible():
        page.click("#nav-tab-topology")
    else:  # phone: the tabs are collapsed behind a menu button
        page.evaluate("switchTab('tab-topology')")
    page.wait_for_selector(".topo-node")
    page.wait_for_timeout(150)
    # the panel starts below the fold: bring it up so that mouse coordinates are inside the window
    page.evaluate("document.getElementById('topology-viewport').scrollIntoView({block: 'start'})")
    page.click("#topo-zoom-fit")
    page.evaluate("document.getElementById('topology-viewport').scrollIntoView({block: 'start'})")
    page.wait_for_timeout(50)


def state(page, expr):
    return page.evaluate("() => " + expr.replace("STATE", "TOPOLOGY_STATE"))


def card_box(page, node_id):
    return page.locator('.topo-node[data-node-id="' + node_id + '"] .topo-node-card').bounding_box()


def first_host_id(page):
    """A card that is neither a router nor a subnet, drawn fully inside the viewport."""
    return page.evaluate("""() => Object.values(TOPOLOGY_STATE.nodePositions)
        .filter(p => p.node.type !== 'router' && p.node.type !== 'subnet')[0].node.id""")


def viewport_box(page):
    return page.locator("#topology-viewport").bounding_box()


def touch(page, kind, pointer_id, x, y):
    """Dispatch a touch PointerEvent. Down goes to the panel (empty space); move/up to window, as the page listens there."""
    target = "document.getElementById('topology-viewport')" if kind == "pointerdown" else "window"
    page.evaluate(
        """([kind, id, x, y]) => {
            const t = %s;
            t.dispatchEvent(new PointerEvent(kind, {pointerId: id, pointerType: 'touch', isPrimary: id === 1,
                clientX: x, clientY: y, bubbles: true, cancelable: true, button: 0}));
        }""" % target,
        [kind, pointer_id, x, y],
    )


class TopologyTest(BrowserTestCase):
    # ---- 1. cards -------------------------------------------------------------------------------------------
    def test_seed_data_draws_router_subnets_and_hosts(self):
        page = self.open_page()
        open_topology(page)
        types = page.evaluate("""() => Object.values(TOPOLOGY_STATE.nodePositions).map(p => p.node.type)""")
        self.assertIn("router", types)
        self.assertIn("subnet", types)
        self.assertTrue(any(t not in ("router", "subnet") for t in types), "no host cards: " + str(types))
        self.assertEqual(page.locator(".topo-node").count(), len(types))
        self.assertTrue(page.locator('.topo-node[data-node-id^="subnet:"]').count() >= 1)

    OVERFLOWING_TEXT = """(subnets) => {
        const out = [];
        document.querySelectorAll('.topo-node').forEach(g => {
            if (g.dataset.nodeId.startsWith('subnet:') !== subnets) return;
            const c = g.querySelector('.topo-node-card').getBoundingClientRect();
            g.querySelectorAll('.topo-node-title, .topo-node-sub').forEach(t => {
                const r = t.getBoundingClientRect();
                if (r.left < c.left - 0.5 || r.right > c.right + 0.5 || r.top < c.top - 0.5 || r.bottom > c.bottom + 0.5)
                    out.push([g.dataset.nodeId, t.textContent, 'right ' + (r.right - c.right).toFixed(1),
                              'bottom ' + (r.bottom - c.bottom).toFixed(1), 'top ' + (r.top - c.top).toFixed(1)]);
            });
        });
        return out;
    }"""

    def test_no_router_or_host_card_text_sticks_out_of_its_rectangle(self):
        page = self.open_page()
        open_topology(page)
        self.assertEqual(page.evaluate(self.OVERFLOWING_TEXT, False), [])

    def test_no_subnet_card_text_sticks_out_of_its_rectangle(self):
        # The subnet card is as tall as the others (56), so its second line (the range) stays inside it.
        page = self.open_page()
        open_topology(page)
        self.assertEqual(page.evaluate(self.OVERFLOWING_TEXT, True), [])

    # ---- 2. filters -----------------------------------------------------------------------------------------
    def dimmed_subnets(self, page):
        return page.evaluate("""() => [...document.querySelectorAll('.topo-node[data-node-id^="subnet:"]')]
            .filter(e => e.classList.contains('node-dimmed')).length""")

    def test_subnets_are_never_dimmed_by_the_type_filters(self):
        page = self.open_page()
        open_topology(page)
        for label in ("All", "Routers", "Linux / DNS"):
            page.click('.btn-pill:text-is("%s")' % label)
            self.assertEqual(self.dimmed_subnets(page), 0, "subnet dimmed with filter " + label)

    def test_searching_a_host_dims_the_rest_but_keeps_the_subnets(self):
        page = self.open_page()
        open_topology(page)
        target = page.evaluate("""() => TOPOLOGY_STATE.data.nodes.filter(n => n.type !== 'router')[0]""")
        page.fill("#topo-search-input", target["label"] if target.get("label") else target["id"])
        dimmed = page.evaluate("""() => [...document.querySelectorAll('.topo-node.node-dimmed')].map(e => e.dataset.nodeId)""")
        self.assertTrue(len(dimmed) >= 1, "the search dimmed nothing")
        self.assertNotIn(target["id"], dimmed)
        self.assertEqual(self.dimmed_subnets(page), 0)

    # ---- 3. dragging a card ---------------------------------------------------------------------------------
    def test_dragging_a_card_moves_it_without_selecting_and_persists_after_reload(self):
        page = self.open_page()
        open_topology(page)
        nid = first_host_id(page)
        before = card_box(page, nid)
        sx, sy = before["x"] + before["width"] / 2, before["y"] + before["height"] / 2
        page.mouse.move(sx, sy)
        page.mouse.down()
        page.mouse.move(sx + 30, sy + 20, steps=5)
        page.mouse.move(sx + 60, sy + 40, steps=5)
        page.mouse.up()
        page.wait_for_timeout(250)
        after = card_box(page, nid)
        self.assertAlmostEqual(after["x"] - before["x"], 60, delta=2)
        self.assertAlmostEqual(after["y"] - before["y"], 40, delta=2)
        self.assertTrue(page.evaluate("document.getElementById('topology-drawer').classList.contains('hidden')"),
                        "dragging selected the card")
        saved = page.evaluate("JSON.parse(localStorage.getItem('%s'))" % OFFSETS_KEY)
        self.assertIn(nid, saved)
        # reload: the position is kept (compare relative to the router, which does not move)
        page.reload()
        open_topology(page)
        self.assertIn(nid, page.evaluate("JSON.parse(localStorage.getItem('%s'))" % OFFSETS_KEY))
        offset = state(page, "STATE.nodeOffsets[%s]" % repr(nid).replace("'", '"'))
        self.assertGreater(abs(offset["x"]) + abs(offset["y"]), 10)
        # the redrawn card carries that offset: its distance to the router differs from the untouched layout
        reset_layout = page.evaluate("""(nid) => { const p = TOPOLOGY_STATE.nodePositions[nid], o = TOPOLOGY_STATE.nodeOffsets[nid];
            return [p.x - o.x, p.y - o.y]; }""", nid)
        pos = state(page, "STATE.nodePositions[%s]" % repr(nid).replace("'", '"'))
        self.assertAlmostEqual(pos["x"] - reset_layout[0], offset["x"], delta=0.01)

    def test_reset_forgets_dragged_cards(self):
        page = self.open_page()
        open_topology(page)
        nid = first_host_id(page)
        b = card_box(page, nid)
        page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
        page.mouse.down()
        page.mouse.move(b["x"] + 80, b["y"] + 60, steps=6)
        page.mouse.up()
        page.wait_for_timeout(200)
        self.assertIn(nid, page.evaluate("JSON.parse(localStorage.getItem('%s'))" % OFFSETS_KEY))
        page.click("#topo-zoom-reset")
        self.assertEqual(page.evaluate("JSON.parse(localStorage.getItem('%s'))" % OFFSETS_KEY), {})
        self.assertEqual(state(page, "Object.keys(STATE.nodeOffsets).length"), 0)

    # ---- 4. selection ---------------------------------------------------------------------------------------
    def test_click_opens_the_drawer_and_escape_or_empty_space_close_it(self):
        page = self.open_page()
        open_topology(page)
        nid = first_host_id(page)
        label = state(page, "STATE.nodePositions[%s].node.label" % repr(nid).replace("'", '"'))
        page.locator('.topo-node[data-node-id="%s"] .topo-node-card' % nid).click()
        self.assertFalse(page.evaluate("document.getElementById('topology-drawer').classList.contains('hidden')"))
        self.assertEqual(page.inner_text("#drawer-node-name").strip(), label)
        page.keyboard.press("Escape")
        self.assertTrue(page.evaluate("document.getElementById('topology-drawer').classList.contains('hidden')"))
        page.locator('.topo-node[data-node-id="%s"] .topo-node-card' % nid).click()
        vb = viewport_box(page)
        page.mouse.click(vb["x"] + 6, vb["y"] + vb["height"] - 6)
        self.assertTrue(page.evaluate("document.getElementById('topology-drawer').classList.contains('hidden')"))

    # ---- 5. pan and zoom ------------------------------------------------------------------------------------
    def test_dragging_the_background_pans_the_map(self):
        page = self.open_page()
        open_topology(page)
        px, py = state(page, "STATE.panX"), state(page, "STATE.panY")
        vb = viewport_box(page)
        x, y = vb["x"] + 6, vb["y"] + vb["height"] - 6
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 40, y - 25, steps=4)
        page.mouse.up()
        self.assertAlmostEqual(state(page, "STATE.panX") - px, 40, delta=1)
        self.assertAlmostEqual(state(page, "STATE.panY") - py, -25, delta=1)
        self.assertIn("translate(%s" % state(page, "STATE.panX"),
                      page.get_attribute("#topology-root-group", "transform"))

    def test_wheel_zooms_around_the_cursor(self):
        page = self.open_page()
        open_topology(page)
        vb = viewport_box(page)
        cx, cy = vb["x"] + 300, vb["y"] + 150
        world = page.evaluate("""([cx, cy]) => { const r = document.getElementById('topology-viewport').getBoundingClientRect();
            const s = TOPOLOGY_STATE; return [(cx - r.left - s.panX) / s.scale, (cy - r.top - s.panY) / s.scale]; }""", [cx, cy])
        s0 = state(page, "STATE.scale")
        page.mouse.move(cx, cy)
        page.mouse.wheel(0, -100)
        page.wait_for_timeout(50)
        self.assertGreater(state(page, "STATE.scale"), s0)
        after = page.evaluate("""([cx, cy]) => { const r = document.getElementById('topology-viewport').getBoundingClientRect();
            const s = TOPOLOGY_STATE; return [(cx - r.left - s.panX) / s.scale, (cy - r.top - s.panY) / s.scale]; }""", [cx, cy])
        self.assertAlmostEqual(after[0], world[0], delta=0.5)
        self.assertAlmostEqual(after[1], world[1], delta=0.5)
        s1 = state(page, "STATE.scale")
        page.mouse.wheel(0, 100)
        page.wait_for_timeout(50)
        self.assertLess(state(page, "STATE.scale"), s1)

    def test_zoom_buttons_and_fit_change_the_scale(self):
        page = self.open_page()
        open_topology(page)
        s0 = state(page, "STATE.scale")
        page.click("#topo-zoom-in")
        s1 = state(page, "STATE.scale")
        self.assertAlmostEqual(s1 / s0, 1.2, delta=0.01)
        page.click("#topo-zoom-out")
        page.click("#topo-zoom-out")
        self.assertLess(state(page, "STATE.scale"), s0)
        page.click("#topo-zoom-fit")
        self.assertAlmostEqual(state(page, "STATE.scale"), s0, delta=0.001)

    def test_scale_is_clamped_between_015_and_25(self):
        page = self.open_page()
        open_topology(page)
        for _ in range(40):
            page.evaluate("topologyZoomCentered(1 / 1.2)")
        self.assertAlmostEqual(state(page, "STATE.scale"), 0.15, delta=1e-9)
        for _ in range(60):
            page.evaluate("topologyZoomCentered(1.2)")
        self.assertAlmostEqual(state(page, "STATE.scale"), 2.5, delta=1e-9)
        # through the real controls too
        vb = viewport_box(page)
        page.mouse.move(vb["x"] + 200, vb["y"] + 100)
        for _ in range(5):
            page.mouse.wheel(0, -200)
        for _ in range(5):
            page.click("#topo-zoom-in")
        self.assertLessEqual(state(page, "STATE.scale"), 2.5)

    # ---- 6. keyboard ----------------------------------------------------------------------------------------
    def test_keyboard_on_the_map_pans_zooms_and_fits(self):
        page = self.open_page()
        open_topology(page)
        page.focus("#topology-viewport")
        px, py = state(page, "STATE.panX"), state(page, "STATE.panY")
        page.keyboard.press("ArrowLeft")
        self.assertAlmostEqual(state(page, "STATE.panX") - px, 48, delta=0.01)
        page.keyboard.press("ArrowDown")
        self.assertAlmostEqual(state(page, "STATE.panY") - py, -48, delta=0.01)
        s0 = state(page, "STATE.scale")
        page.keyboard.press("+")
        self.assertGreater(state(page, "STATE.scale"), s0)
        page.keyboard.press("-")
        page.keyboard.press("-")
        self.assertLess(state(page, "STATE.scale"), s0)
        page.keyboard.press("0")
        self.assertAlmostEqual(state(page, "STATE.scale"), s0, delta=0.001)

    def test_focused_card_moves_with_shift_arrows_and_enter_selects_it(self):
        page = self.open_page()
        open_topology(page)
        nid = first_host_id(page)
        card = page.locator('.topo-node[data-node-id="%s"]' % nid)
        card.focus()
        page.keyboard.press("Shift+ArrowRight")
        page.wait_for_timeout(120)
        off = state(page, "STATE.nodeOffsets[%s]" % repr(nid).replace("'", '"'))
        self.assertEqual((off["x"], off["y"]), (20, 0))
        # focus returns to the (redrawn) card, so a second press keeps working
        page.keyboard.press("Shift+ArrowDown")
        page.wait_for_timeout(120)
        off = state(page, "STATE.nodeOffsets[%s]" % repr(nid).replace("'", '"'))
        self.assertEqual((off["x"], off["y"]), (20, 20))
        self.assertTrue(page.evaluate("document.getElementById('topology-drawer').classList.contains('hidden')"))
        page.keyboard.press("Enter")
        self.assertFalse(page.evaluate("document.getElementById('topology-drawer').classList.contains('hidden')"))
        self.assertEqual(state(page, "STATE.selectedNodeId"), nid)

    # ---- 7. touch -------------------------------------------------------------------------------------------
    def test_two_finger_pinch_scales_in_proportion_to_the_distance(self):
        page = self.open_page()
        open_topology(page)
        vb = viewport_box(page)
        x, y = vb["x"] + 6, vb["y"] + vb["height"] - 6
        s0 = state(page, "STATE.scale")
        touch(page, "pointerdown", 1, x, y)
        touch(page, "pointerdown", 2, x + 100, y)
        touch(page, "pointermove", 2, x + 150, y)
        self.assertAlmostEqual(state(page, "STATE.scale") / s0, 1.5, delta=0.01)
        touch(page, "pointermove", 2, x + 60, y)
        self.assertAlmostEqual(state(page, "STATE.scale") / s0, 0.6, delta=0.01)
        touch(page, "pointerup", 2, x + 60, y)
        touch(page, "pointerup", 1, x, y)
        self.assertFalse(state(page, "STATE.isDragging"))

    def test_one_finger_drag_pans(self):
        page = self.open_page()
        open_topology(page)
        vb = viewport_box(page)
        x, y = vb["x"] + 6, vb["y"] + vb["height"] - 6
        px, py, s0 = state(page, "STATE.panX"), state(page, "STATE.panY"), state(page, "STATE.scale")
        touch(page, "pointerdown", 1, x, y)
        touch(page, "pointermove", 1, x + 30, y - 20)
        touch(page, "pointerup", 1, x + 30, y - 20)
        self.assertAlmostEqual(state(page, "STATE.panX") - px, 30, delta=0.5)
        self.assertAlmostEqual(state(page, "STATE.panY") - py, -20, delta=0.5)
        self.assertEqual(state(page, "STATE.scale"), s0)

    # ---- 8. responsive --------------------------------------------------------------------------------------
    def open_sized(self, width, height):
        context = self.browser.new_context(color_scheme="dark", viewport={"width": width, "height": height})
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(self.server.base_url)
        open_topology(page)
        return page

    HOST_POSITIONS = """() => Object.values(TOPOLOGY_STATE.nodePositions)
        .filter(p => ['router', 'subnet', 'guest'].indexOf(p.node.type) === -1).map(p => [Math.round(p.x), Math.round(p.y)])"""

    def test_phone_has_no_horizontal_overflow_and_hosts_in_at_most_two_per_row(self):
        page = self.open_sized(390, 844)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
        pills = page.locator(".btn-pill")
        self.assertTrue(pills.count() >= 4)
        for i in range(pills.count()):
            self.assertTrue(pills.nth(i).is_visible())
        hosts = page.evaluate(self.HOST_POSITIONS)
        self.assertTrue(len(hosts) >= 1)
        # at most two cards per row (a lone last card is centred, so it may sit between the two columns)
        per_row = {}
        for x, y in hosts:
            per_row[y] = per_row.get(y, 0) + 1
        self.assertLessEqual(max(per_row.values()), 2, per_row)
        self.assertGreater(len(per_row), 1, "on a phone the hosts should wrap into several rows")

    def test_wide_screen_puts_all_hosts_in_one_row(self):
        page = self.open_sized(1440, 900)
        hosts = page.evaluate(self.HOST_POSITIONS)
        self.assertTrue(len(hosts) >= 1)
        self.assertEqual(len({h[1] for h in hosts}), 1)

    # ---- 9. icons -------------------------------------------------------------------------------------------
    def test_icon_only_zoom_buttons_have_a_centred_svg(self):
        page = self.open_page()
        open_topology(page)
        for sel in ("#topo-zoom-in", "#topo-zoom-out"):
            res = page.evaluate("""(sel) => { const b = document.querySelector(sel), s = b.querySelector('svg');
                const br = b.getBoundingClientRect(), sr = s.getBoundingClientRect();
                return { text: b.textContent.trim(), dx: (sr.left + sr.width / 2) - (br.left + br.width / 2),
                         dy: (sr.top + sr.height / 2) - (br.top + br.height / 2) }; }""", sel)
            self.assertEqual(res["text"], "", sel + " has text, not only an icon")
            self.assertLess(abs(res["dx"]), 0.6, sel + " icon off-centre horizontally")
            self.assertLess(abs(res["dy"]), 0.6, sel + " icon off-centre vertically")

    ICON_VS_CAPS = """(sel) => {
        const el = document.querySelector(sel), svg = el.querySelector('svg');
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        let node = null, n;
        while ((n = walker.nextNode())) { if (n.textContent.trim()) node = n; }
        const range = document.createRange(); range.selectNodeContents(node);
        const tr = range.getBoundingClientRect();
        const cs = getComputedStyle(node.parentElement);
        const ctx = document.createElement('canvas').getContext('2d');
        ctx.font = cs.fontStyle + ' ' + cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
        const m = ctx.measureText('H');
        const A = m.fontBoundingBoxAscent, D = m.fontBoundingBoxDescent;
        const top = tr.top + (tr.height - (A + D)) / 2;
        const capCenter = top + A - m.actualBoundingBoxAscent / 2;
        const sr = svg.getBoundingClientRect();
        return { d: (sr.top + sr.height / 2) - capCenter };
    }"""

    def test_refresh_and_tab_icons_sit_on_the_centre_of_the_capital_letters(self):
        page = self.open_page()
        open_topology(page)
        selectors = ["#refresh-btn"] + [
            ".nav-tab-btn[data-tab='%s']" % t
            for t in page.evaluate("[...document.querySelectorAll('.nav-tab-btn')].map(b => b.dataset.tab)")
        ]
        self.assertTrue(len(selectors) >= 5)
        for sel in selectors:
            d = page.evaluate(self.ICON_VS_CAPS, sel)["d"]
            self.assertLessEqual(abs(d), 1.2, "%s icon is %.2f px from the cap centre" % (sel, d))


if __name__ == "__main__":
    unittest.main()
