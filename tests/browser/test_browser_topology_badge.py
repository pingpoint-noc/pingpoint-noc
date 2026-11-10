"""Browser test: the alert count badge sits flush with the corner of its node card (K126). Optional.

It used to end 2 px short of the right edge, right where the rounded corner starts, so it looked almost aligned but
not quite. Now its right edge is the card's right edge and it is centred on the top border, with the number centred.
"""

import unittest

from .support import BrowserTestCase

GEOMETRY = """() => [...document.querySelectorAll('.topo-node')]
  .filter(g => g.querySelector('.topo-node-alert-badge'))
  .map(g => {
    const card = g.querySelector('.topo-node-card').getBoundingClientRect();
    const pill = g.querySelector('.topo-node-alert-badge rect').getBoundingClientRect();
    const text = g.querySelector('.topo-node-alert-badge text').getBoundingClientRect();
    return { id: g.getAttribute('data-node-id'), cardRight: card.right, cardTop: card.top,
             pillRight: pill.right, pillMiddle: (pill.top + pill.bottom) / 2,
             pillCentre: (pill.left + pill.right) / 2, textCentre: (text.left + text.right) / 2,
             textMiddle: (text.top + text.bottom) / 2 };
  })"""


class TopologyBadgeTest(BrowserTestCase):
    WITH_USER = False

    def test_the_badge_is_flush_with_the_right_edge_and_centred_on_the_top_border(self):
        page = self.open_page(path="#/topology")
        page.wait_for_selector(".topo-node-alert-badge", state="attached")
        page.wait_for_timeout(1500)
        badges = page.evaluate(GEOMETRY)
        self.assertGreaterEqual(len(badges), 1)
        for b in badges:
            self.assertAlmostEqual(b["pillRight"], b["cardRight"], delta=0.6, msg=b["id"] + " right edge")
            self.assertAlmostEqual(b["pillMiddle"], b["cardTop"], delta=0.6, msg=b["id"] + " on the top border")
            self.assertAlmostEqual(b["textCentre"], b["pillCentre"], delta=0.6, msg=b["id"] + " number, horizontal")
            self.assertAlmostEqual(b["textMiddle"], b["pillMiddle"], delta=0.6, msg=b["id"] + " number, vertical")


if __name__ == "__main__":
    unittest.main()
