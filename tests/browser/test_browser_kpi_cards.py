"""Browser test: the indicator cards of the selected machine keep one width whatever the machine, nothing is cut, and the service pills of
Device Health keep a gap when they wrap. Optional (Playwright).

Seen on the real NOC: a DNS machine (four cards) squeezed its last two cards to 58 px and cut their text, and picking another machine
(three cards) made every card wider again, so everything moved."""

import unittest

from .support import BrowserTestCase

CARDS = """() => {
  const group = document.querySelector('.kpi-group-target');
  const cards = [...group.querySelectorAll('.kpi-card')].filter(c => c.getBoundingClientRect().width > 0);
  const cut = [];
  for (const e of group.querySelectorAll('.kpi-label, .kpi-sub, .kpi-caption, .kpi-value')) {
    if (e.getBoundingClientRect().width > 0 && e.scrollWidth > e.clientWidth + 1) cut.push(e.textContent.trim());
  }
  const ribbon = group.querySelector('.kpi-ribbon');
  return {widths: cards.map(c => Math.round(c.getBoundingClientRect().width)), cut: cut, sideways: ribbon.scrollWidth - ribbon.clientWidth,
          group: Math.round(group.getBoundingClientRect().width)};
}"""


class KpiCardsTest(BrowserTestCase):
    WITH_USER = False

    def target(self, page, name):
        value = page.evaluate(
            "(n) => [...document.querySelectorAll('#device-select option')].find(o => o.textContent.includes(n)).value", name)
        page.select_option("#device-select", value)
        page.wait_for_timeout(900)
        return page.evaluate(CARDS)

    def test_a_card_has_the_same_width_for_a_machine_with_three_cards_and_one_with_four(self):
        for width in (1100, 1280, 1440, 1700):
            page = self.open_page()
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_function("document.querySelectorAll('#device-select option').length > 2")
            three = self.target(page, "edge-router")
            four = self.target(page, "Pi-hole")
            self.assertEqual(len(three["widths"]), 3, width)
            self.assertEqual(len(four["widths"]), 4, width)
            self.assertEqual(set(three["widths"]) | set(four["widths"]), {three["widths"][0]}, "%d px: %s vs %s" % (width, three, four))
            self.assertEqual(three["group"], four["group"], width)

    def test_no_text_is_cut_and_the_ribbon_does_not_overflow(self):
        for width in (1100, 1280, 1440, 1700):
            page = self.open_page()
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_function("document.querySelectorAll('#device-select option').length > 2")
            for name in ("edge-router", "Pi-hole", "noc-host"):
                data = self.target(page, name)
                self.assertEqual(data["cut"], [], "%s at %d px" % (name, width))
                self.assertLessEqual(data["sideways"], 0, "%s at %d px" % (name, width))

    def test_the_service_pills_have_a_gap_when_they_wrap(self):
        page = self.open_page()
        page.set_viewport_size({"width": 800, "height": 900})

        def patch(route):
            response = route.fetch()
            body = response.json()
            for device in body["devices"]:
                device["services"] = [{"service_name": n, "status": "active", "description": ""}
                                      for n in ("cron", "docker", "rsyslog", "ssh", "sshd")]
            route.fulfill(response=response, json=body)

        page.route("**/api/host-health*", patch)
        page.wait_for_function("typeof switchTab === 'function' && typeof loadHostHealth === 'function'")
        page.evaluate("() => { switchTab('tab-health'); document.querySelectorAll('details.health-group').forEach(d => d.open = true); return loadHostHealth(); }")
        page.wait_for_function("document.querySelectorAll('#host-health-tbody .svc-pills .badge').length >= 5")
        page.evaluate("() => document.querySelectorAll('#host-health-tbody .svc-pills').forEach(e => e.style.maxWidth = '130px')")  # force a wrap
        gaps = page.evaluate("""() => {
          const pills = [...document.querySelectorAll('#host-health-tbody .svc-pills')[0].querySelectorAll('.badge')].map(b => b.getBoundingClientRect());
          const lines = [...new Set(pills.map(r => Math.round(r.top)))].sort((a, b) => a - b);
          const out = [];
          for (let i = 1; i < lines.length; i++) {
            const above = Math.max(...pills.filter(r => Math.round(r.top) === lines[i - 1]).map(r => r.bottom));
            out.push(lines[i] - above);
          }
          return {lines: lines.length, gaps: out};
        }""")
        self.assertGreaterEqual(gaps["lines"], 2, gaps)
        self.assertTrue(all(g >= 3 for g in gaps["gaps"]), gaps)


if __name__ == "__main__":
    unittest.main()
