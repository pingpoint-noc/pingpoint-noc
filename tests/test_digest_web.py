"""Static checks of the daily summary block in Settings (the browser test covers behaviour and layout)."""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
PARTIAL = (ROOT / "web" / "partials" / "tab_settings.html").read_text(encoding="utf-8")
SETTINGS_JS = (ROOT / "web" / "static" / "js" / "views" / "settings.js").read_text(encoding="utf-8")
BUNDLE = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
STYLES = (ROOT / "web" / "static" / "styles.css").read_text(encoding="utf-8")


class DigestWebTest(unittest.TestCase):
    def test_every_control_has_an_id_and_a_label(self):
        for control in ("set-digest-enabled", "set-digest-time"):
            self.assertIn('id="%s"' % control, PARTIAL)
            self.assertIn('for="%s"' % control, PARTIAL)
        for name in ("ntfy", "discord", "slack", "telegram", "webhook", "email"):
            self.assertIn('data-digest-dest="%s"' % name, PARTIAL)
        self.assertIn('id="digest-test"', PARTIAL)
        self.assertIn("Send a test summary now", PARTIAL)

    def test_the_time_field_is_a_time_input_and_states_the_zone(self):
        self.assertRegex(PARTIAL, r'<input id="set-digest-time"[^>]*type="time"')
        self.assertIn("Send at ({{ tz }})", PARTIAL)

    def test_the_block_has_no_absolute_url(self):
        block = PARTIAL[PARTIAL.index('id="dest-digest"'):PARTIAL.index('class="destinations-grid')]
        self.assertIsNone(re.search(r"https?://", block))

    def test_the_form_sends_the_new_keys_and_the_bundle_has_them(self):
        for text in (SETTINGS_JS, BUNDLE):
            self.assertIn("digest_enabled", text)
            self.assertIn("digest_time", text)
            self.assertIn('"_digest"', text)
            self.assertIn("/api/settings/test-digest", text)

    def test_the_new_css_uses_tokens_only(self):
        lines = [line for line in STYLES.splitlines() if "is-unconfigured" in line or "#dest-digest" in line]
        self.assertGreaterEqual(len(lines), 2)
        for line in lines:
            self.assertNotRegex(line, r"rgba?\(|#[0-9a-fA-F]{3,8}\b")


if __name__ == "__main__":
    unittest.main()
