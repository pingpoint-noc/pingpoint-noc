"""One name per view and per concept in the web UI (K114, point 4 of the interface backlog).

The tab name is the only name of its view: the command palette, the buttons that jump to it and the lead of its panel
title all use it. "Syslog Stream" is reserved for the Syslog tab; summaries elsewhere say "Syslog".
"""

import html
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
TAB_NAMES = {
    "tab-overview": "Overview",
    "tab-topology": "Topology",
    "tab-bandwidth": "Bandwidth & Interfaces",
    "tab-flows": "IPFIX & NAT",
    "tab-syslog": "Syslog Stream",
    "tab-timeline": "Incident Timeline",
    "tab-health": "Health & Inventory",
    "tab-settings": "Settings",
}


def read(*parts):
    return WEB.joinpath(*parts).read_text(encoding="utf-8")


def plain(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()


class ButtonParser(HTMLParser):
    """(data-tab or data-target, visible text) of every button in a partial."""

    def __init__(self, attribute, css_class):
        super().__init__()
        self.attribute = attribute
        self.css_class = css_class
        self.found = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "button" and self.css_class in (attributes.get("class") or "").split() and attributes.get(self.attribute):
            self.current = [attributes[self.attribute], ""]

    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == "button" and self.current is not None:
            self.found.append((self.current[0], plain(self.current[1])))
            self.current = None


class UiNamesTest(unittest.TestCase):
    def test_tab_bar_uses_the_agreed_names(self):
        parser = ButtonParser("data-tab", "nav-tab-btn")
        parser.feed(read("partials", "header_nav.html"))
        self.assertEqual(dict(parser.found), TAB_NAMES)

    def test_command_palette_uses_the_tab_names(self):
        titles = dict(re.findall(r'id: "(tab-[a-z]+)", title: "([^"]+)"', read("static", "js", "components", "palette.js")))
        self.assertEqual(titles, TAB_NAMES)

    def test_every_jump_button_carries_the_name_of_its_destination(self):
        found = []
        for path in sorted((WEB / "partials").glob("*.html")):
            parser = ButtonParser("data-target", "jump-tab-btn")
            parser.feed(path.read_text(encoding="utf-8"))
            found += [(path.name, target, text) for target, text in parser.found]
        self.assertGreaterEqual(len(found), 6)
        for file_name, target, text in found:
            self.assertEqual(text, TAB_NAMES[target] + " →", file_name + " " + target)

    def test_old_names_are_gone(self):
        sources = {}
        for path in list((WEB / "partials").glob("*.html")) + list((WEB / "static" / "js").rglob("*.js")):
            sources[path.name] = path.read_text(encoding="utf-8")
        for old in ("Syslog Console", "Deep Dive", "All Flows", "Inspect Host Flows", "View Host Logs",
                    "View Host Syslog Events", "IPFIX Flows & NAT"):
            for name, text in sources.items():
                self.assertNotIn(old, html.unescape(text), old + " in " + name)

    def test_single_panel_views_start_their_title_with_the_tab_name(self):
        flows = read("static", "js", "views", "flows.js")
        syslog = read("static", "js", "views", "syslog.js")
        for text in re.findall(r'flowsTitle\.textContent = "([^"]+)"', flows):
            self.assertTrue(text.startswith("IPFIX & NAT"), text)
        for text in re.findall(r'syslogTitle\.textContent = "([^"]+)"', syslog):
            self.assertTrue(text.startswith("Syslog Stream"), text)
        self.assertRegex(plain(read("partials", "tab_flows.html")), r"^.*IPFIX & NAT: ")
        self.assertIn("<h2 id=\"syslog-panel-title\">Syslog Stream: ", read("partials", "tab_syslog.html"))
        self.assertIn("Incident Timeline", read("partials", "tab_timeline.html"))

    def test_syslog_stream_names_only_the_syslog_tab(self):
        overview = read("static", "js", "views", "overview.js")
        self.assertNotIn("Syslog Stream", overview)
        titles = re.findall(r'<h[34] id="overview-[a-z]+-title">([^<]+)<', read("partials", "tab_overview.html"))
        self.assertEqual(len(titles), 3)
        for title in titles:
            self.assertNotIn("Stream", title)

    def test_overview_side_panels_start_with_the_same_title_the_script_sets(self):
        page = read("partials", "tab_overview.html")
        script = read("static", "js", "views", "overview.js")
        for element_id, variable in (("overview-talkers-title", "ovTalkersTitle"), ("overview-syslog-title", "ovSyslogTitle")):
            in_html = re.search(r'id="%s">([^<]+)<' % element_id, page).group(1)
            in_script = re.search(r'%s\.textContent = "([^"]+)"' % variable, script).group(1)
            self.assertEqual(html.unescape(in_html), in_script, element_id)


if __name__ == "__main__":
    unittest.main()
