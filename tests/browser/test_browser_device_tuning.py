"""Browser test: the per-device alert tuning card in Settings (own latency limit and interface ignore pattern)."""

from .support import BrowserTestCase


class DeviceTuningTest(BrowserTestCase):
    def open_inventory(self):
        page = self.open_page()
        self.sign_in(page)
        page.wait_for_function("typeof authSession !== 'undefined' && authSession !== null")
        page.wait_for_function("document.getElementById('settings-state').textContent === 'Editable'")
        page.click("[data-settings-section='inventory']")
        page.wait_for_function("document.querySelectorAll('#tune-device option').length > 0")
        return page

    def test_a_device_limit_is_saved_reloaded_and_cleared(self):
        page = self.open_inventory()
        device = page.input_value("#tune-device")
        page.fill("#tune-icmp-latency", "420")
        page.fill("#tune-ignored-ifaces", "^wg-")
        page.click("#btn-tune-save")
        page.wait_for_function("document.getElementById('tune-msg').textContent.startsWith('Saved')")
        stored = page.evaluate("(id) => fetch('/api/devices/' + id + '/settings').then(r => r.json())", device)
        self.assertEqual(stored["overrides"], {"icmp_latency_ms": "420", "ignored_interfaces": "^wg-"})
        page.fill("#tune-icmp-latency", "")
        page.fill("#tune-ignored-ifaces", "")
        page.click("#btn-tune-save")
        page.wait_for_function("document.getElementById('tune-msg').textContent.startsWith('Saved')")
        stored = page.evaluate("(id) => fetch('/api/devices/' + id + '/settings').then(r => r.json())", device)
        self.assertEqual(stored["overrides"], {})

    def test_a_bad_pattern_is_refused_with_a_message(self):
        page = self.open_inventory()
        page.fill("#tune-ignored-ifaces", "(unclosed")
        page.click("#btn-tune-save")
        page.wait_for_function("document.getElementById('tune-msg').textContent.startsWith('Tuning not saved')")
        self.assertIn("regular expression", page.inner_text("#tune-msg"))
