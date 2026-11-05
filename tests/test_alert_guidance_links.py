"""Every alert rule with a "Check first" text also says where to look in the dashboard (K115, point 5 of the interface backlog).

The links are fixed per rule, like the text: a view, the id of the panel that answers the question and, for syslog, a
filter. The page turns them into buttons that select the alert's device and scroll to that panel.
"""

from pathlib import Path
import re
import unittest

from api.services import ALERT_GUIDANCE, with_guidance

PARTIALS = Path(__file__).resolve().parent.parent / "web" / "partials"
VIEWS = {"overview", "topology", "bandwidth", "flows", "syslog", "timeline", "health", "settings"}
# Its fix is on the DNS server itself, not in a view of this dashboard.
NO_VIEW = {"dns_blocklist_dropped"}


def known_ids():
    ids = set()
    for path in PARTIALS.glob("*.html"):
        ids.update(re.findall(r'\bid="([^"{]+)"', path.read_text(encoding="utf-8")))
    return ids


class AlertGuidanceLinksTest(unittest.TestCase):
    def test_every_rule_with_guidance_has_links_to_real_panels(self):
        ids = known_ids()
        for rule, guidance in ALERT_GUIDANCE.items():
            links = guidance.get("links")
            self.assertIsInstance(links, list, rule)
            if rule in NO_VIEW:
                self.assertEqual(links, [], rule)
                continue
            self.assertGreaterEqual(len(links), 1, rule)
            self.assertLessEqual(len(links), 2, rule)
            seen = set()
            for link in links:
                self.assertIn(link["view"], VIEWS, rule)
                self.assertIn(link["anchor"], ids, rule + ": panel id not in the page")
                self.assertNotIn(link["view"], seen, rule + ": one link per view")
                seen.add(link["view"])
                extra = set(link) - {"view", "anchor", "q"}
                self.assertEqual(extra, set(), rule)
                if "q" in link:
                    self.assertEqual(link["view"], "syslog", rule)
                    self.assertTrue(0 < len(link["q"]) <= 80, rule)

    def test_links_ride_along_with_the_alert_rows(self):
        alert = {"rule_id": "icmp_loss_high", "device_id": "demo-nas", "target": "icmp", "status": "firing", "severity": "warning"}
        guidance = with_guidance(alert)["guidance"]
        self.assertEqual(guidance["links"][0]["view"], "health")
        self.assertIn("why", guidance)
        self.assertIn("check", guidance)

    def test_check_text_uses_the_tab_names(self):
        # K114: the text points to views by the same names the tab bar uses.
        for rule, guidance in ALERT_GUIDANCE.items():
            self.assertNotIn("IPFIX Flows", guidance["check"], rule)
            self.assertNotIn("Overview & Proxmox", guidance["check"], rule)


if __name__ == "__main__":
    unittest.main()
