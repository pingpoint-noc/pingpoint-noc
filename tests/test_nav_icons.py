"""Every icon the interface asks for exists in the sprite: a missing symbol paints nothing and leaves a gap that looks like off-centre text."""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class NavIconsTest(unittest.TestCase):
    def test_every_icon_used_in_the_scripts_and_the_markup_is_defined(self):
        sprite = (ROOT / "web" / "partials" / "header_nav.html").read_text(encoding="utf-8")
        defined = set(re.findall(r'<symbol id="(i-[a-z-]+)"', sprite))
        used = set()
        for path in list((ROOT / "web" / "static" / "js").rglob("*.js")) + list((ROOT / "web" / "partials").glob("*.html")) + [ROOT / "web" / "index.html"]:
            text = path.read_text(encoding="utf-8")
            used |= set(re.findall(r'setNavIcon\([^,]+,\s*"(i-[a-z-]+)"', text))
            used |= set(re.findall(r'<use href="#(i-[a-z-]+)"', text))
        self.assertTrue(used, "no icon references found: the test is blind")
        self.assertEqual(sorted(used - defined), [])


class BundleSyntaxTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_the_bundle_is_valid_javascript(self):
        """One stray character in a view (a newline inside a string) breaks every page; the bundle is checked as the browser reads it."""
        done = subprocess.run(["node", "--check", str(ROOT / "web" / "static" / "app.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr[:500])


if __name__ == "__main__":
    unittest.main()
