"""Browser test: panel headers read the same way in every view (K112, point 3). Optional (Playwright + Chromium).

Title and subtitle first; in the actions block the freshness badge comes after every filter and every action, both on a
desktop (same row or a later row) and on a phone (a later row). The order in the markup is checked by
tests/test_panel_headers.py; this checks what is drawn.
"""

import unittest

from .support import BrowserTestCase

TABS = ["tab-overview", "tab-topology", "tab-bandwidth", "tab-flows", "tab-syslog", "tab-timeline", "tab-health"]

READING_ORDER = """(tabId) => {
  const problems = [];
  let checked = 0;
  for (const header of document.querySelectorAll('#' + tabId + ' .panel-header')) {
    if (!header.offsetParent) { continue; }
    const title = header.querySelector('.panel-title-wrap');
    const actions = header.querySelector('.panel-actions');
    const fresh = actions && actions.querySelector(':scope > .freshness-badge');
    if (!title || !actions || !fresh) { continue; }
    checked += 1;
    const f = fresh.getBoundingClientRect();
    const t = title.getBoundingClientRect();
    const name = (title.querySelector('h2,h3,h4') || title).textContent.trim();
    if (t.top > f.top + 1 && t.bottom > f.bottom + 1) { problems.push(name + ': title below the freshness badge'); }
    for (const other of actions.children) {
      if (other === fresh || !other.offsetParent) { continue; }
      const o = other.getBoundingClientRect();
      const before = o.bottom <= f.top + 1 || o.right <= f.left + 1;
      if (!before) { problems.push(name + ': ' + (other.id || other.className) + ' is drawn after the freshness badge'); }
    }
  }
  return { checked: checked, problems: problems };
}"""


class PanelHeaderReadingOrderTest(BrowserTestCase):
    WITH_USER = False

    def check_every_tab(self, page):
        total = 0
        for tab in TABS:
            page.locator("button[data-tab='%s']" % tab).evaluate("el => el.click()")
            page.wait_for_timeout(400)
            result = page.evaluate(READING_ORDER, tab)
            self.assertEqual(result["problems"], [], tab)
            total += result["checked"]
        self.assertGreaterEqual(total, 10)

    def test_desktop_freshness_is_last(self):
        self.check_every_tab(self.open_page())

    def test_phone_freshness_is_last(self):
        context = self.browser.new_context(viewport={"width": 390, "height": 844})
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(self.server.base_url)
        self.check_every_tab(page)


if __name__ == "__main__":
    unittest.main()
