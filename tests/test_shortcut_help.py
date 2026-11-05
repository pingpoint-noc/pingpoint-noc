"""The keyboard help lists every key the page really handles (K118, point 8 of the interface backlog)."""

from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

WEB = Path(__file__).resolve().parent.parent / "web"
JS = WEB / "static" / "js"


class HelpParser(HTMLParser):
    """kbd texts, group headings and the text of every row of the help."""

    def __init__(self):
        super().__init__()
        self.inside = False
        self.in_kbd = False
        self.in_heading = False
        self.in_row = 0
        self.kbd = []
        self.headings = []
        self.rows = []
        self.note = ""
        self.in_note = False

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        if tag == "aside" and dict(attrs).get("id") == "shortcut-help":
            self.inside = True
        if not self.inside:
            return
        if tag == "kbd":
            self.in_kbd = True
            self.kbd.append("")
        elif tag == "h3":
            self.in_heading = True
            self.headings.append("")
        elif tag == "div" and self.in_row == 0 and "shortcut-row" in classes:
            self.in_row = 1
            self.rows.append("")
        elif tag == "p" and "shortcut-help-note" in classes:
            self.in_note = True

    def handle_endtag(self, tag):
        if tag == "kbd":
            self.in_kbd = False
        elif tag == "h3":
            self.in_heading = False
        elif tag == "p":
            self.in_note = False
        elif tag == "aside":
            self.inside = False
        elif tag == "div" and self.in_row:
            self.in_row = 0

    def handle_data(self, data):
        if not self.inside:
            return
        if self.in_kbd:
            self.kbd[-1] += data
        if self.in_heading:
            self.headings[-1] += data
        if self.in_row:
            self.rows[-1] += data
        if self.in_note:
            self.note += data


def parse_help():
    parser = HelpParser()
    parser.feed((WEB / "partials" / "header_nav.html").read_text(encoding="utf-8"))
    return parser


class ShortcutHelpTest(unittest.TestCase):
    def test_every_single_key_of_the_page_is_listed(self):
        source = (JS / "components" / "controls.js").read_text(encoding="utf-8")
        handler = source.split("// Single-key shortcuts can be turned off")[1]
        keys = {k.lower() for k in re.findall(r'key === "([A-Za-z?/])"', handler)}
        self.assertTrue({"v", "h", "r", "p", "t", "m", "?", "/"} <= keys, keys)
        listed = {k.strip().lower() for k in parse_help().kbd}
        for key in keys:
            self.assertIn(key, listed, "key " + key + " works but the help does not list it")

    def test_the_map_and_palette_keys_are_listed(self):
        topology = (JS / "views" / "topology.js").read_text(encoding="utf-8")
        for code, shown in (("ArrowLeft", "←"), ("ArrowRight", "→"), ("ArrowUp", "↑"), ("ArrowDown", "↓")):
            self.assertIn('"%s"' % code, topology)
            self.assertIn(shown, [k.strip() for k in parse_help().kbd], code)
        for code in ('"+"', '"-"', '"0"'):
            self.assertIn("e.key === " + code, topology)
        listed = [k.strip() for k in parse_help().kbd]
        for shown in ("+", "-", "0", "Enter"):
            self.assertIn(shown, listed, shown)

    def test_help_is_grouped_and_escape_says_everything_it_closes(self):
        parser = parse_help()
        self.assertEqual([h.strip() for h in parser.headings], ["Views", "Anywhere", "Command palette", "Topology map"])
        escape = [row for row in parser.rows if "Esc" in row]
        self.assertEqual(len(escape), 1)
        for closed in ("help", "palette", "view menu", "drawer", "TV"):
            self.assertIn(closed, escape[0], closed)

    def test_the_note_says_when_single_keys_do_nothing(self):
        note = parse_help().note.lower()
        self.assertIn("typing", note)
        self.assertIn("settings", note)


if __name__ == "__main__":
    unittest.main()
