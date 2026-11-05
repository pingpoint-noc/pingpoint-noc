"""Static checks on the web pieces of acknowledge, maintenance windows and dependencies (K63-K65)."""

from pathlib import Path
import re
import unittest

from alerts.notify import KNOWN_RULE_IDS

WEB = Path(__file__).resolve().parent.parent / "web"


def read(*parts):
    return (WEB.joinpath(*parts)).read_text(encoding="utf-8")


class TestAlertManagementAssets(unittest.TestCase):
    def test_the_rule_list_of_the_maintenance_form_is_the_servers(self):
        js = read("static", "js", "views", "settings.js")
        block = js.split("var MAINTENANCE_RULES = [")[1].split("];")[0]
        self.assertEqual(tuple(re.findall(r'"([a-z_]+)"', block)), KNOWN_RULE_IDS)

    def test_the_form_has_the_controls_the_script_reads(self):
        html = read("partials", "tab_settings.html")
        js = read("static", "js", "views", "settings.js")
        for element_id in (
            "maint-device", "maint-rule", "maint-start", "maint-duration", "maint-end", "maint-reason",
            "btn-maint-create", "maintenance-tbody", "maintenance-msg",
        ):
            self.assertIn('id="' + element_id + '"', html)
            self.assertIn('"' + element_id + '"', js)
        self.assertIn('id="maintenance-banner"', read("partials", "header_nav.html"))
        for label in ("All devices", "All rules", "30 minutes", "1 hour", "4 hours", "24 hours"):
            self.assertIn(label, html)

    def test_times_are_converted_with_the_configured_zone_not_the_browsers(self):
        js = read("static", "js", "formatters.js")
        self.assertIn('timeZone: LOCAL_TZ', js.split("function localToUtc")[0])
        body = js.split("function localToUtc")[1].split("function utcToLocalInput")[0]
        self.assertNotIn("getTimezoneOffset", body)
        self.assertNotIn("new Date(text", body)

    def test_the_inventory_header_matches_the_cells_and_offers_the_parent(self):
        html = read("partials", "tab_settings.html")
        head = html.split('id="settings-devices-tbody"')[0].split("<thead>")[-1]
        self.assertEqual(len(re.findall(r"<th>", head)), 7)
        self.assertIn("Behind (parent device)", head)

    def test_the_new_styles_use_tokens_and_no_loose_colours(self):
        css = read("static", "styles.css")
        block = css.split("Maintenance windows (K64) and dependencies between devices (K65)")[1]
        self.assertNotRegex(block, r"rgba?\(|#[0-9a-fA-F]{3,8}\b")
        self.assertNotIn("transition: all", block)
        self.assertNotIn("box-shadow", block)

    def test_alert_text_from_the_api_is_escaped_in_the_new_badges(self):
        js = read("static", "js", "views", "alerts.js")
        body = js.split("function holdBadgesHtml")[1].split("function renderMaintenanceBanner")[0]
        self.assertIn("escapeHtml(al.maintenance_reason)", body)
        self.assertIn("escapeHtml(al.suppressed_by_name || al.suppressed_by)", body)
        ack = js.split("function ackLineHtml")[1].split("function ackControlsHtml")[0]
        self.assertIn("escapeHtml(al.ack_by)", ack)
        self.assertIn("escapeHtml(al.ack_note)", ack)


if __name__ == "__main__":
    unittest.main()
