"""Browser test: the alert-sound toggle (button, M key, persistence, AudioContext) and the CSV/JSON exports.

Optional (Playwright + Chromium). The sound tests spy on window.AudioContext / window.Audio, so they check what the
page really asks the browser to play. The export tests click the real buttons and read the downloaded file.
"""

import csv
import io
import json
import time
import unittest

from storage import create_repository

from .support import BrowserTestCase

SOUND_KEY = "mini-noc-sound"
ROUTER = "edge-router-1"

# Counts every AudioContext / Audio the page creates, before any page script runs.
AUDIO_SPY = """
(() => {
  window.__audio = { contexts: 0, elements: 0 };
  const Orig = window.AudioContext || window.webkitAudioContext;
  if (Orig) {
    window.AudioContext = class extends Orig {
      constructor(...args) { super(...args); window.__audio.contexts += 1; }
    };
    window.webkitAudioContext = window.AudioContext;
  }
  const OrigAudio = window.Audio;
  window.Audio = function (...args) { window.__audio.elements += 1; return new OrigAudio(...args); };
})();
"""


def audio_calls(page):
    return page.evaluate("window.__audio.contexts + window.__audio.elements")


def open_tools_menu(page):
    page.evaluate("document.getElementById('tools-menu').open = true")


def sound_state_text(page):
    """The 'on'/'off' label of the sound item: it is drawn by CSS (::after of .tool-state)."""
    text = page.evaluate(
        "getComputedStyle(document.querySelector('#sound-btn .tool-state'), '::after').content"
    )
    return text.strip("\"'")


class SpyPageMixin:
    def open_spied_page(self, stored_sound=None):
        """A fresh context with the audio spy installed; `stored_sound` pre-sets the saved choice before the first load."""
        context = self.browser.new_context(color_scheme="dark", viewport={"width": 1400, "height": 900})
        self.addCleanup(context.close)
        context.add_init_script(AUDIO_SPY)
        if stored_sound is not None:
            # Only on the very first document, so a later reload keeps whatever the page itself saved.
            context.add_init_script(
                "if (!sessionStorage.getItem('__seeded')) { sessionStorage.setItem('__seeded', '1');"
                " localStorage.setItem('%s', '%s'); }" % (SOUND_KEY, "true" if stored_sound else "false")
            )
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(self.server.base_url + "/")
        page.wait_for_function("STATE.lastFiringCount !== null")
        return page


class SoundTest(SpyPageMixin, BrowserTestCase):
    SERVER_PER_TEST = True

    def add_firing_alert(self, target="if:77"):
        now = int(time.time())
        repo = create_repository("sqlite", self.server.db_path)
        return repo.create_alert(ROUTER, "interface_down", target, "critical", "firing", now, now, now, "Interface is down")

    def trigger_alert_refresh(self, page, expected_count):
        page.click("#refresh-btn")
        page.wait_for_function("STATE.lastFiringCount === %d" % expected_count)

    def test_sound_is_off_by_default(self):
        page = self.open_spied_page()
        open_tools_menu(page)
        self.assertEqual(page.get_attribute("#sound-btn", "aria-pressed"), "false")
        self.assertEqual(sound_state_text(page), "off")

    def test_button_toggles_state_attribute_and_label(self):
        page = self.open_spied_page()
        open_tools_menu(page)
        page.click("#sound-btn")
        self.assertEqual(page.get_attribute("#sound-btn", "aria-pressed"), "true")
        self.assertEqual(sound_state_text(page), "on")
        page.click("#sound-btn")
        self.assertEqual(page.get_attribute("#sound-btn", "aria-pressed"), "false")
        self.assertEqual(sound_state_text(page), "off")

    def test_m_key_toggles_the_sound_like_the_button(self):
        page = self.open_spied_page()
        page.keyboard.press("m")
        self.assertEqual(page.get_attribute("#sound-btn", "aria-pressed"), "true")
        page.keyboard.press("M")
        self.assertEqual(page.get_attribute("#sound-btn", "aria-pressed"), "false")

    def test_m_key_is_ignored_while_typing_in_a_field(self):
        page = self.open_spied_page()
        page.keyboard.press("5")  # Syslog tab, it has a text filter
        page.focus("#log-search-input")
        page.keyboard.type("mm")
        self.assertEqual(page.input_value("#log-search-input"), "mm")
        self.assertEqual(page.get_attribute("#sound-btn", "aria-pressed"), "false")

    def test_choice_is_saved_in_local_storage_and_restored_on_reload(self):
        page = self.open_spied_page()
        page.keyboard.press("m")
        self.assertEqual(page.evaluate("localStorage.getItem('%s')" % SOUND_KEY), "true")
        page.reload()
        page.wait_for_function("STATE.lastFiringCount !== null")
        open_tools_menu(page)
        self.assertEqual(page.get_attribute("#sound-btn", "aria-pressed"), "true")
        self.assertEqual(sound_state_text(page), "on")
        page.keyboard.press("m")
        self.assertEqual(page.evaluate("localStorage.getItem('%s')" % SOUND_KEY), "false")
        page.reload()
        page.wait_for_function("STATE.lastFiringCount !== null")
        self.assertEqual(page.get_attribute("#sound-btn", "aria-pressed"), "false")

    def test_turning_the_sound_on_plays_a_confirmation_beep(self):
        page = self.open_spied_page()
        self.assertEqual(audio_calls(page), 0)
        page.keyboard.press("m")
        self.assertGreaterEqual(audio_calls(page), 1)

    def test_new_alert_with_sound_off_creates_no_audio(self):
        page = self.open_spied_page(stored_sound=False)
        before = page.evaluate("STATE.lastFiringCount")
        self.add_firing_alert()
        self.trigger_alert_refresh(page, before + 1)
        self.assertEqual(audio_calls(page), 0)

    def test_new_alert_with_sound_on_tries_to_play(self):
        page = self.open_spied_page(stored_sound=True)
        self.assertEqual(page.get_attribute("#sound-btn", "aria-pressed"), "true")
        self.assertEqual(audio_calls(page), 0, "restoring the saved choice must not beep by itself")
        before = page.evaluate("STATE.lastFiringCount")
        self.add_firing_alert()
        self.trigger_alert_refresh(page, before + 1)
        self.assertGreaterEqual(audio_calls(page), 1)

    def test_a_refresh_without_new_alerts_stays_silent_even_with_sound_on(self):
        page = self.open_spied_page(stored_sound=True)
        before = page.evaluate("STATE.lastFiringCount")
        page.click("#refresh-btn")
        page.wait_for_timeout(500)
        self.assertEqual(page.evaluate("STATE.lastFiringCount"), before)
        self.assertEqual(audio_calls(page), 0)

    def test_resolving_an_alert_with_sound_on_stays_silent(self):
        page = self.open_spied_page(stored_sound=True)
        base = page.evaluate("STATE.lastFiringCount")
        self.add_firing_alert()
        self.trigger_alert_refresh(page, base + 1)
        beeps = audio_calls(page)
        self.assertGreaterEqual(beeps, 1)
        repo = create_repository("sqlite", self.server.db_path)
        for alert in repo.list_alerts(device_id=ROUTER, limit=50):
            if alert.get("status") == "firing" and alert.get("target") == "if:77":
                now = int(time.time())
                repo.update_alert(alert["id"], "critical", "resolved", alert["fired_utc"], now, now, "Interface is up")
        page.click("#refresh-btn")
        page.wait_for_function("STATE.lastFiringCount === %d" % base)
        self.assertEqual(audio_calls(page), beeps)


def wait_for_href(page, link_id):
    page.wait_for_function(
        "document.getElementById('%s') && document.getElementById('%s').getAttribute('href') !== '#'" % (link_id, link_id)
    )


class ExportTest(BrowserTestCase):
    def open_tab(self, page, tab_id):
        page.locator("button[data-tab='%s']" % tab_id).click()
        page.wait_for_load_state("networkidle")

    def collect_console_errors(self, page):
        errors = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: errors.append(str(err)))
        return errors

    def download(self, page, selector):
        with page.expect_download() as info:
            page.click(selector)
        return info.value

    def read(self, download):
        with open(download.path(), encoding="utf-8") as handle:
            return handle.read()

    def parse_csv(self, text):
        return list(csv.reader(io.StringIO(text)))

    def check_csv(self, download, name_part, header_part, utc_columns=()):
        name = download.suggested_filename
        self.assertTrue(name.endswith(".csv"), name)
        self.assertIn(name_part, name)
        rows = self.parse_csv(self.read(download))
        self.assertGreaterEqual(len(rows), 2, "CSV without data rows: " + str(rows))
        header = rows[0]
        self.assertIn(header_part, header)
        for column in utc_columns:
            self.assertIn(column, header)
            self.assertTrue(column.endswith("_utc"), column)
        self.assertTrue(any(col.endswith("_utc") for col in header), "no UTC timestamp column: " + str(header))
        return header, rows[1:]

    def check_json(self, download, name_part, rows_key="rows"):
        name = download.suggested_filename
        self.assertTrue(name.endswith(".json"), name)
        self.assertIn(name_part, name)
        body = json.loads(self.read(download))
        self.assertIn(rows_key, body)
        self.assertGreaterEqual(len(body[rows_key]), 1)
        return body

    # ---- (a) alert history ----------------------------------------------------------------------------------
    def test_alert_history_csv_has_header_and_utc_timestamps(self):
        page = self.open_page()
        errors = self.collect_console_errors(page)
        download = self.download(page, "#export-alert-history")
        self.assertTrue(download.suggested_filename.startswith("alerts_history_"))
        self.assertTrue(download.suggested_filename.endswith(".csv"))
        rows = self.parse_csv(self.read(download))
        header = rows[0]
        for column in ("id", "device_id", "rule_id", "status", "first_triggered_utc", "fired_utc", "resolved_utc"):
            self.assertIn(column, header)
        self.assertEqual(errors, [])

    def test_alert_history_csv_rows_have_the_same_width_as_the_header(self):
        page = self.open_page()
        rows = self.parse_csv(self.read(self.download(page, "#export-alert-history")))
        for row in rows[1:]:
            self.assertEqual(len(row), len(rows[0]), row)

    def test_alert_history_json_is_valid_and_lists_alerts(self):
        page = self.open_page()
        download = self.download(page, "#export-alert-history-json")
        self.assertTrue(download.suggested_filename.startswith("alerts_history_"))
        self.assertTrue(download.suggested_filename.endswith(".json"))
        body = json.loads(self.read(download))
        self.assertIsInstance(body["alerts"], list)

    # ---- (b) bandwidth --------------------------------------------------------------------------------------
    def open_bandwidth(self, page):
        self.open_tab(page, "tab-bandwidth")
        wait_for_href(page, "export-bw-csv")

    def test_bandwidth_csv_download_has_rates_and_utc_time(self):
        page = self.open_page()
        errors = self.collect_console_errors(page)
        self.open_bandwidth(page)
        download = self.download(page, "#export-bw-csv")
        header, rows = self.check_csv(download, "bandwidth", "rx_bps")
        self.assertIn("tx_bps", header)
        self.assertEqual(header[0], "ts_utc")
        self.assertRegex(rows[0][0], r"^\d")
        self.assertEqual(errors, [])

    def test_bandwidth_json_download_is_valid(self):
        page = self.open_page()
        self.open_bandwidth(page)
        body = self.check_json(self.download(page, "#export-bw-json"), "bandwidth")
        self.assertEqual(body["dataset"], "bandwidth")
        self.assertEqual(body["row_count"], len(body["rows"]))
        self.assertIn("ts_utc", body["columns"])

    # ---- (c) IPFIX flows ------------------------------------------------------------------------------------
    def open_flows(self, page):
        self.open_tab(page, "tab-flows")
        wait_for_href(page, "export-flows-csv")

    def test_flows_csv_download_has_header_and_a_seed_row(self):
        page = self.open_page()
        errors = self.collect_console_errors(page)
        self.open_flows(page)
        download = self.download(page, "#export-flows-csv")
        header, rows = self.check_csv(download, "flows", "src_ip")
        self.assertIn("ts_start_utc", header)
        self.assertIn("bytes", header)
        self.assertEqual(errors, [])

    def test_flows_json_download_is_valid(self):
        page = self.open_page()
        self.open_flows(page)
        body = self.check_json(self.download(page, "#export-flows-json"), "flows")
        self.assertEqual(body["dataset"], "flows")
        self.assertIn("src_ip", body["rows"][0])

    # ---- (d) syslog -----------------------------------------------------------------------------------------
    def open_syslog(self, page):
        self.open_tab(page, "tab-syslog")
        wait_for_href(page, "export-logs-csv")

    def test_syslog_csv_download_has_header_and_a_seed_row(self):
        page = self.open_page()
        errors = self.collect_console_errors(page)
        self.open_syslog(page)
        download = self.download(page, "#export-logs-csv")
        header, rows = self.check_csv(download, "logs", "message")
        self.assertEqual(header[0], "ts_utc")
        self.assertEqual(errors, [])

    def test_syslog_json_download_is_valid(self):
        page = self.open_page()
        self.open_syslog(page)
        body = self.check_json(self.download(page, "#export-logs-json"), "logs")
        self.assertEqual(body["dataset"], "logs")
        self.assertIn("message", body["rows"][0])

    def test_syslog_export_follows_the_text_filter(self):
        page = self.open_page()
        self.open_syslog(page)
        page.fill("#log-search-input", "zzz-no-such-message-zzz")
        page.click("#log-filter-btn")
        page.wait_for_function("document.getElementById('export-logs-csv').href.indexOf('zzz-no-such') !== -1")
        rows = self.parse_csv(self.read(self.download(page, "#export-logs-csv")))
        self.assertEqual(len(rows), 1, "only the header is expected: " + str(rows))
        self.assertIn("message", rows[0])

    def test_empty_syslog_json_export_is_valid_and_does_not_break_the_page(self):
        page = self.open_page()
        errors = self.collect_console_errors(page)
        self.open_syslog(page)
        page.fill("#log-search-input", "zzz-no-such-message-zzz")
        page.click("#log-filter-btn")
        page.wait_for_function("document.getElementById('export-logs-json').href.indexOf('zzz-no-such') !== -1")
        download = self.download(page, "#export-logs-json")
        body = json.loads(self.read(download))
        self.assertEqual(body["rows"], [])
        self.assertEqual(body["row_count"], 0)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
