"""Every panel header has the same shape (K112, point 3 of the interface backlog).

Title and subtitle on the left; on the right, in this order and always inside `.panel-actions`:
filters and view controls, then actions (exports, downloads, links to the full view, refresh, save), then the
freshness badge. The check reads the partials, so it covers panels that are hidden until a target or data exists.
"""

from html.parser import HTMLParser
from pathlib import Path
import unittest

PARTIALS = Path(__file__).resolve().parent.parent / "web" / "partials"
VOID = {"br", "hr", "img", "input", "link", "meta", "col", "wbr", "source"}
ACTION_IDS = {"topology-refresh-btn", "settings-top-save-btn"}
ACTION_CLASSES = {"export-button-group", "jump-tab-btn"}


class HeaderParser(HTMLParser):
    """Collects, for every `.panel-header`, its direct children and the direct children of its `.panel-actions`."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.headers = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        node = {"tag": tag, "classes": classes, "id": attributes.get("id"), "kids": []}
        if self.stack:
            self.stack[-1]["kids"].append(node)
        if "panel-header" in classes:
            self.headers.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                return


def rank(node):
    """0 = filter or view control, 1 = action, 2 = freshness."""
    if "freshness-badge" in node["classes"]:
        return 2
    if node["classes"] & ACTION_CLASSES or node["id"] in ACTION_IDS:
        return 1
    if node["tag"] == "a" and "btn" in node["classes"]:
        return 1
    return 0


def name(node):
    return node["id"] or ".".join(sorted(node["classes"])) or node["tag"]


class PanelHeaderShapeTest(unittest.TestCase):
    def headers(self):
        found = []
        for path in sorted(PARTIALS.glob("*.html")):
            parser = HeaderParser()
            parser.feed(path.read_text(encoding="utf-8"))
            for header in parser.headers:
                found.append((path.name, header))
        return found

    def test_there_are_panel_headers_to_check(self):
        self.assertGreaterEqual(len(self.headers()), 19)

    def test_title_block_first_and_everything_else_inside_panel_actions(self):
        for file_name, header in self.headers():
            kids = header["kids"]
            where = file_name + " " + name(header["kids"][0] if kids else header)
            self.assertTrue(kids and "panel-title-wrap" in kids[0]["classes"], where + ": title block must come first")
            rest = kids[1:]
            self.assertLessEqual(len(rest), 1, where + ": loose elements next to the title block: "
                                 + ", ".join(name(n) for n in rest))
            for node in rest:
                self.assertIn("panel-actions", node["classes"], where + ": " + name(node) + " must be in .panel-actions")

    def test_filters_then_actions_then_freshness_last(self):
        for file_name, header in self.headers():
            actions = [n for n in header["kids"][1:] if "panel-actions" in n["classes"]]
            if not actions:
                continue
            ranks = [rank(n) for n in actions[0]["kids"]]
            where = file_name + " " + name(header["kids"][0]) + ": " + ", ".join(name(n) for n in actions[0]["kids"])
            self.assertEqual(ranks, sorted(ranks), where + " (order must be filters, actions, freshness)")
            self.assertLessEqual(ranks.count(2), 1, where)


if __name__ == "__main__":
    unittest.main()
