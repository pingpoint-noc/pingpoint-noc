"""Browser test: the header badge does not say "All Systems Operational" while a device is down or stale. Optional (Playwright)."""

import unittest

from .support import BrowserTestCase

BADGE = "#global-status-badge"
TEXT = "#global-status-text"


class AttentionBadgeTest(BrowserTestCase):
    WITH_USER = False

    def set_green(self, page):
        page.wait_for_selector(BADGE)
        page.evaluate("""() => {
          const b = document.getElementById('global-status-badge');
          b.removeAttribute('data-attention');
          b.className = 'global-status ok';
          document.getElementById('global-status-text').textContent = 'All Systems Operational';
        }""")

    def test_a_green_badge_turns_amber_when_a_device_needs_attention_and_back(self):
        page = self.open_page()
        self.set_green(page)
        page.evaluate("() => { STATE.devicesAttention = 2; syncAttentionBadge(); }")
        self.assertEqual(page.inner_text(TEXT), "2 devices need attention")
        self.assertIn("warn", page.get_attribute(BADGE, "class"))
        page.evaluate("() => { STATE.devicesAttention = 1; syncAttentionBadge(); }")
        self.assertEqual(page.inner_text(TEXT), "1 device needs attention")
        page.evaluate("() => { STATE.devicesAttention = 0; syncAttentionBadge(); }")
        self.assertEqual(page.inner_text(TEXT), "All Systems Operational")
        self.assertIn("ok", page.get_attribute(BADGE, "class"))

    def test_a_firing_badge_is_never_touched(self):
        page = self.open_page()
        page.wait_for_selector(BADGE)
        page.evaluate("""() => {
          const b = document.getElementById('global-status-badge');
          b.removeAttribute('data-attention');
          b.className = 'global-status firing';
          document.getElementById('global-status-text').textContent = '1 critical';
          STATE.devicesAttention = 3; syncAttentionBadge();
        }""")
        self.assertEqual(page.inner_text(TEXT), "1 critical")
        self.assertIn("firing", page.get_attribute(BADGE, "class"))


if __name__ == "__main__":
    unittest.main()
