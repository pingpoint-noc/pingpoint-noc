"""Static checks on the web UI: theme, favicon, controls and no hard-coded canvas colors."""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from fastapi.testclient import TestClient

from api.app import create_app
from storage import create_repository


class TestWebAssets(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "t.db"
        repo = create_repository("sqlite", db_path)
        repo.init_storage()
        self.client = TestClient(create_app(db_path))

    def test_select_picker_icon_does_not_shrink(self):
        # In an inline-flex select the arrow shrank to 0 px next to a long label and showed up as a stray "/".
        css = (Path(__file__).resolve().parent.parent / "web" / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"select::picker-icon\s*\{\s*flex:\s*none;")

    def test_phone_select_width_follows_content(self):
        css = (Path(__file__).resolve().parent.parent / "web" / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("field-sizing: content", css)
        self.assertNotIn("min-width: 9rem", css)

    def test_no_coloured_glow_shadows(self):
        css = (Path(__file__).resolve().parent.parent / "web" / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertNotIn("accent-solid-glow", css)
        self.assertNotRegex(css, r"box-shadow:\s*0 0 1[06]px")

    def test_app_js_bundle_matches_modules(self):
        # app.js is generated from web/static/js/: editing a module without rebuilding would leave the UI stale.
        from tools.build_bundle import bundle_app_js

        target = Path(__file__).resolve().parent.parent / "web" / "static" / "app.js"
        original = target.read_bytes()
        try:
            bundle_app_js()
            rebuilt = target.read_bytes()
        finally:
            target.write_bytes(original)
        self.assertEqual(original.replace(b"\r\n", b"\n"), rebuilt.replace(b"\r\n", b"\n"))

    def test_top_interfaces_block_can_really_be_hidden(self):
        # The class .hidden only works where a rule exists for that element. Without it the router's
        # interface ranking stayed visible under a DNS or host target (seen on the replica, 08-11).
        css = (Path(__file__).resolve().parent.parent / "web" / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"#overview-top-if-wrap\.hidden\s*\{[^}]*display:\s*none")

    def test_empty_alert_list_adds_no_gap_to_the_banner(self):
        # With 0 firing alerts the list is empty but its margin-top made the banner content sit 6 px too high.
        css = (Path(__file__).resolve().parent.parent / "web" / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.active-alerts-list:empty\s*\{[^}]*margin-top:\s*0")

    def test_header_controls_do_not_float_right_on_their_own_row(self):
        # At 1440 px the brand (381) + controls (1036) did not fit, so the controls fell to a second row and
        # floated right with a hole on their left. Below 1500 px the badge goes away so both fit on one row,
        # and below 1420 px the controls take a full row, left-aligned, with the clock at the far end.
        css = (Path(__file__).resolve().parent.parent / "web" / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"@media \(max-width: 1500px\)\s*\{[^}]*\.brand-badge\s*\{[^}]*display:\s*none")
        self.assertRegex(css, r"@media \(max-width: 1420px\)\s*\{[^}]*\.noc-navbar-controls\s*\{[^}]*flex:\s*1 1 100%")
        self.assertRegex(css, r"@media \(max-width: 1420px\)[^@]*\.clock-display\s*\{[^}]*margin-left:\s*auto")

    def test_overview_grid_column_can_shrink_on_phones(self):
        # A plain 1fr column keeps the content's minimum width: at 390 px three panels reached 406 px and the
        # page scrolled sideways. minmax(0, 1fr) lets the column shrink to the screen.
        css = (Path(__file__).resolve().parent.parent / "web" / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.overview-split-layout\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)")

    def test_hidden_class_always_hides(self):
        # The JS toggles the class "hidden" on dozens of panels, but only a few had their own rule. On a Linux
        # host the router's interface panels stayed visible above the host panel (and the other way round).
        css = (Path(__file__).resolve().parent.parent / "web" / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"(?m)^\.hidden\s*\{\s*display:\s*none\s*!important;?\s*\}")

    def test_host_quick_action_buttons_wrap_on_phones(self):
        # Two buttons with long labels (one 442 px wide) made the whole page 846 px wide at 390 px on host and
        # DNS targets. The row must wrap and each button may break its label over several lines.
        css = (Path(__file__).resolve().parent.parent / "web" / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.host-quick-actions\s*\{[^}]*flex-wrap:\s*wrap")
        self.assertRegex(css, r"\.host-quick-actions\s+\.btn\s*\{[^}]*white-space:\s*normal")

    def test_overview_labels_are_short_and_truthful(self):
        # "Reachable (Telemetry)" pushed the IP out of its card ("192.168.88.15…") and the DNS peers list was
        # titled "Internal hosts" although it also lists external addresses (1.1.1.1, 8.8.8.8).
        js = (Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "views" / "overview.js").read_text(encoding="utf-8")
        self.assertNotIn("Reachable (Telemetry)", js)
        self.assertNotIn("Internal hosts communicating", js)

    def test_font_sizes_come_from_one_scale(self):
        # 29 different sizes (0.72, 0.74, 0.75, 0.76 rem...) made the text look uneven. Every size must now be
        # a --fs-* token, and the scale itself must stay small.
        css = (Path(__file__).resolve().parent.parent / "web" / "static" / "styles.css").read_text(encoding="utf-8")
        tokens = re.findall(r"--fs-[a-z0-9]+\s*:\s*([0-9.]+rem)\s*;", css)
        self.assertGreaterEqual(len(tokens), 8)
        self.assertLessEqual(len(set(tokens)), 12)
        loose = []
        for value in re.findall(r"font-size:\s*([^;]+);", css):
            value = value.strip()
            if value in ("inherit", "100%"):
                continue
            if not value.startswith("var(--fs-"):
                loose.append(value)
        self.assertEqual(loose, [])

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_health_marks_an_old_collector_error_as_recovered(self):
        """A stale last_error must not look like a current failure (seen with real data on 08-11)."""
        js = self.client.get("/static/app.js").text
        self.assertIn("Recovered", js)
        self.assertIn("consecutive_errors", js)

    def test_favicon_is_served_as_svg_and_linked(self):
        response = self.client.get("/static/favicon.svg")
        self.assertEqual(response.status_code, 200)
        self.assertIn("image/svg+xml", response.headers["content-type"])
        self.assertIn("<svg", response.text)
        self.assertIn('rel="icon"', self.client.get("/").text)

    def test_theme_is_set_before_first_paint_and_has_a_toggle(self):
        html = self.client.get("/").text
        head = html.split("</head>")[0]
        self.assertIn("/static/theme-init.js", head)
        self.assertIn('id="theme-toggle"', html)
        init = self.client.get("/static/theme-init.js").text
        self.assertIn("prefers-color-scheme", init)
        self.assertIn("mini-noc-theme", init)

    def test_light_theme_overrides_every_surface_token(self):
        css = self.client.get("/static/styles.css").text
        dark = css.split(':root[data-theme="light"]')[0]
        light = css.split(':root[data-theme="light"]')[1].split("}")[0]
        tokens = re.findall(r"^\s*(--(?:bg|border|text|color|chart)-[a-z-]+):", dark, re.M)
        self.assertGreater(len(tokens), 20)
        # glow, soft-danger and on-accent tokens are intentionally shared or redefined elsewhere
        missing = []
        for token in tokens:
            if token + ":" not in light:
                missing.append(token)
        self.assertEqual(missing, [])

    def test_canvas_code_reads_theme_colors_instead_of_hex_literals(self):
        js = self.client.get("/static/app.js").text
        self.assertIn("themeColor(", js)
        canvas_colors = re.findall(r'ctx\.(?:fillStyle|strokeStyle) = "#[0-9a-fA-F]{3,8}"', js)
        # the red event markers are a fixed semantic color on both themes
        for line in canvas_colors:
            self.assertIn("#ef4444", line)

    def test_controls_and_shortcut_help_exist(self):
        html = self.client.get("/").text
        for element_id in ("pause-btn", "sound-btn", "help-btn", "shortcut-help", "export-alert-history"):
            self.assertIn('id="%s"' % element_id, html)
        js = self.client.get("/static/app.js").text
        self.assertIn("setPaused", js)
        self.assertIn("notifyFiringChange", js)
        self.assertIn("exportAlertHistoryCsv", js)

    def test_settings_tab_and_interface_row_selection_exist(self):
        html = self.client.get("/").text
        for marker in ('data-tab="tab-settings"', 'id="settings-form"', 'id="set-ntfy-url"', 'id="set-admin-token"'):
            self.assertIn(marker, html)
        js = self.client.get("/static/app.js").text
        self.assertIn("selectInterface(", js)
        self.assertIn("tr.if-row", js)
        # the admin token lives in sessionStorage only
        self.assertNotIn("localStorage.setItem(\"mini-noc-admin-token\"", js)

    def test_settings_has_one_section_per_destination_with_test_and_remove(self):
        html = self.client.get("/").text
        for name in ("ntfy", "discord", "slack", "telegram", "webhook", "email"):
            self.assertIn('id="dest-%s"' % name, html)
            self.assertIn('id="dest-status-%s"' % name, html)
            self.assertIn('data-test="%s"' % name, html)
            self.assertIn('data-clear="%s"' % name, html)
        # secrets are typed in password fields, never plain text
        for field in ("set-discord-url", "set-slack-url", "set-telegram-token", "set-webhook-url", "set-smtp-password"):
            self.assertRegex(html, r'id="%s"[^>]*type="password"' % field)

    def test_brand_links_home_and_there_is_a_skip_link(self):
        html = self.client.get("/").text
        self.assertRegex(html, r'<a[^>]*id="brand-home"[^>]*href="/"')
        self.assertIn('class="skip-link"', html)
        self.assertIn('href="#main-content"', html)
        self.assertRegex(html, r'<main[^>]*id="main-content"')
        js = self.client.get("/static/app.js").text
        self.assertIn("function goHome(", js)

    def test_welcome_strip_has_live_status_tasks_and_device_availability(self):
        html = self.client.get("/").text
        for marker in ('id="welcome"', 'id="welcome-title"', 'id="welcome-status"', 'id="welcome-devices"', 'id="welcome-toggle"'):
            self.assertIn(marker, html)
        self.assertGreaterEqual(html.count('data-goto="tab-'), 5)
        js = self.client.get("/static/app.js").text
        for name in ("function initWelcome(", "function updateWelcomeStatus(", "function renderAvailability("):
            self.assertIn(name, js)

    def test_navbar_never_overflows_and_motion_can_be_reduced(self):
        css = self.client.get("/static/styles.css").text
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn(".skip-link", css)
        # the seven tabs always get their own full-width row, and when they do not fit they wrap onto more rows:
        # no sideways scrolling inside the tab bar
        self.assertRegex(css, r"\.noc-nav-tabs\s*\{\s*order: 3;\s*flex: 1 1 100%;[^}]*flex-wrap: wrap")
        nav_rules = re.findall(r"\.noc-nav-tabs\s*\{[^}]*\}", css)
        for rule in nav_rules:
            self.assertNotIn("overflow-x", rule)

    def test_narrow_screens_get_a_menu_button_with_the_current_view(self):
        html = self.client.get("/").text
        css = self.client.get("/static/styles.css").text
        js = self.client.get("/static/app.js").text
        # the button comes right before the tab list and controls it (disclosure pattern, keyboard and screen readers)
        button = re.search(r'<button[^>]*id="nav-menu-btn"[^>]*>', html)
        self.assertIsNotNone(button)
        self.assertIn('aria-expanded="false"', button.group(0))
        self.assertIn('aria-controls="nav-tabs"', button.group(0))
        nav = re.search(r'<nav[^>]*id="nav-tabs"[^>]*>', html)
        self.assertIsNotNone(nav)
        self.assertLess(button.start(), nav.start())
        self.assertIn('id="nav-menu-label"', html)
        # hidden on wide screens; on narrow ones it shows and the tab list becomes a collapsed column
        self.assertRegex(css, r"\.nav-menu-btn\s*\{[^}]*display: none")
        media = re.search(r"@media \(max-width: 1100px\)\s*\{(?P<body>(?:[^{}]*\{[^{}]*\})+)\s*\}", css)
        self.assertIsNotNone(media)
        body = media.group("body")
        self.assertRegex(body, r"\.nav-menu-btn\s*\{[^}]*display: flex")
        self.assertRegex(body, r"\.noc-nav-tabs\s*\{[^}]*display: none")
        self.assertRegex(body, r"\.noc-nav-tabs\.is-open\s*\{[^}]*display: flex")
        for name in ("function initNavMenu(", "function syncNavMenu(", "aria-expanded", '"Escape"'):
            self.assertIn(name, js)

    def test_syslog_feed_row_keeps_the_badge_apart_from_the_text(self):
        css = self.client.get("/static/styles.css").text
        js = self.client.get("/static/app.js").text
        # badge and message are two flex items with a real gap (a plain space was 4 px), and a long message wraps
        # beside the badge instead of going back under it
        rule = re.search(r"\.mini-list-main\s*\{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        self.assertIn("display: flex", rule.group(1))
        self.assertRegex(rule.group(1), r"gap: 0\.[5-9]\d*rem|gap: 1rem")
        title = re.search(r"\.mini-list-main \.item-title\s*\{([^}]*)\}", css)
        self.assertIsNotNone(title)
        self.assertIn("min-width: 0", title.group(1))
        # cut by the available width, not at a fixed number of characters
        self.assertIn("text-overflow: ellipsis", title.group(1))
        self.assertNotIn("shortenLogMessage", js)
        self.assertIn("class='mini-list-main'", js)

    def test_single_column_grid_and_command_snippets_cannot_widen_the_page(self):
        css = self.client.get("/static/styles.css").text
        # a plain "1fr" column never shrinks below its content, so a long command pushed the page wider than a phone
        for grid, breakpoint in ((".two-col-grid", "860"), (".three-col-grid", "1024")):
            phone_rule = re.search(
                r"@media \(max-width: " + breakpoint + r"px\)\s*\{\s*" + re.escape(grid) + r"\s*\{([^}]*)\}", css
            )
            self.assertIsNotNone(phone_rule, grid)
            self.assertIn("minmax(0, 1fr)", phone_rule.group(1), grid)
        snippet = re.search(r"\.code-snippet\s*\{([^}]*)\}", css)
        self.assertIsNotNone(snippet)
        self.assertIn("overflow-wrap: anywhere", snippet.group(1))
        self.assertIn("min-width: 0", snippet.group(1))

    @unittest.skipIf(shutil.which("node") is None, "node not installed")
    def test_javascript_files_have_valid_syntax(self):
        static_dir = Path(__file__).parent.parent / "web" / "static"
        js_files = ["app.js", "theme-init.js"]
        js_subdir = static_dir / "js"
        if js_subdir.exists():
            for p in js_subdir.glob("**/*.js"):
                rel = p.relative_to(static_dir).as_posix()
                js_files.append(rel)
        for name in js_files:
            result = subprocess.run(
                ["node", "--check", str(static_dir / name)], capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, name + ": " + result.stderr)

    @unittest.skipIf(shutil.which("node") is None, "node not installed")
    def test_default_chart_interface_skips_the_loopback(self):
        js = self.client.get("/static/app.js").text
        match = re.search(r"function isLoopbackInterface\(name\) \{.*?\n\}", js, re.DOTALL)
        self.assertIsNotNone(match, "isLoopbackInterface() not found in app.js")
        script = match.group(0) + (
            "\nconsole.log(JSON.stringify(['lo', 'LO', 'loopback0', 'ether1', 'lan88', '', null]"
            ".map(isLoopbackInterface)));"
        )
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[true,true,true,false,false,false,false]")
        # the default pick uses it: among the interfaces that are up and are not the loopback, the one with the most traffic
        func = re.search(r"function pickDefaultInterface\(interfaces\) \{.*?\r?\n\}\r?\n", js, re.S)
        self.assertIsNotNone(func)
        script = match.group(0) + "\n" + func.group(0) + """
const ifs = [
  {name: 'lo', oper_status: 'up', total_bps: 9e9},
  {name: 'ether1', oper_status: 'up', total_bps: 1000},
  {name: 'ether2', oper_status: 'up', total_bps: 52000},
  {name: 'ether3', oper_status: 'down', total_bps: 9e8},
  {name: 'ether4', oper_status: 'not_seen', total_bps: 7e8},
];
const idle = [{name: 'lo', oper_status: 'up', total_bps: 0}, {name: 'a', oper_status: 'up', total_bps: 0}, {name: 'b', oper_status: 'up', total_bps: 0}];
const noRate = [{name: 'x', oper_status: 'up', rx_bps: 5, tx_bps: 5}, {name: 'y', oper_status: 'up', rx_bps: 30, tx_bps: 1}];
console.log(JSON.stringify([
  pickDefaultInterface(ifs).name, pickDefaultInterface(idle).name, pickDefaultInterface(noRate).name,
  pickDefaultInterface([{name: 'lo', oper_status: 'up'}]), pickDefaultInterface([]), pickDefaultInterface(null),
]));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '["ether2","a","y",null,null,null]')

    def test_every_css_variable_in_use_is_defined(self):
        # A var(--x) with no definition is invalid at computed time: in `fill` or `background` it silently becomes black or
        # transparent (topology cards went black under the pointer because of --bg-card-hover).
        css = self.client.get("/static/styles.css").text
        defined = set(re.findall(r"(--[a-zA-Z0-9_-]+)\s*:", css))
        used = set(re.findall(r"var\(\s*(--[a-zA-Z0-9_-]+)", css))
        self.assertEqual(sorted(used - defined), [])

    def test_topology_cards_have_a_visible_highlight_in_both_themes(self):
        css = self.client.get("/static/styles.css").text
        self.assertRegex(css, r"\.topo-node:hover \.topo-node-card,\s*\.topo-node:focus \.topo-node-card \{[^}]*fill: var\(--bg-hover\)")
        self.assertRegex(css, r"\.topo-node\.selected \.topo-node-card \{[^}]*fill: var\(--accent-soft\)")

    def test_overview_grid_keeps_panels_aligned(self):
        css = self.client.get("/static/styles.css").text
        # the two side panels become cells of the grid, so the row heights match and the gaps are the grid gap
        self.assertRegex(css, r"\.overview-side-stack\s*\{\s*display: contents;")
        self.assertRegex(css, r"\.overview-split-layout \.panel\s*\{\s*margin-bottom: 0;")
        self.assertRegex(css, r"\.overview-side-stack > \.panel:last-child\s*\{\s*grid-column: 1 / -1;")

    def test_shortcut_help_can_be_closed_and_single_keys_turned_off(self):
        js = self.client.get("/static/app.js").text
        self.assertIn("initShortcutHelp", js)
        self.assertIn("shortcut-help-close", js)
        self.assertIn("STATE.shortcutsOn", js)
        self.assertIn("mini-noc-pref-shortcuts", js)
        page = self.client.get("/").text
        self.assertIn('id="shortcut-help-close"', page)
        self.assertIn('id="pref-shortcuts"', page)

    def test_banner_does_not_claim_healthy_when_the_engine_stalled(self):
        js = self.client.get("/static/app.js").text
        self.assertIn("data.engine.status !== \"ok\"", js)
        self.assertIn("alert engine is not evaluating", js)
        self.assertIn("Alert engine stalled", js)
        css = self.client.get("/static/styles.css").text
        self.assertIn(".alerts-banner.stale", css)
        self.assertIn(".global-status.stale", css)

    def test_phone_header_has_room_above_its_line_and_does_not_stay_sticky(self):
        css = self._css()
        self.assertRegex(css, r"@media \(max-width: 1100px\) \{\s*\.noc-navbar \{ padding-bottom: 0\.9rem; \}")
        self.assertRegex(css, r"@media \(max-width: 700px\) \{\s*\.noc-navbar \{ position: static;")

    def test_wide_tables_stack_on_phones(self):
        css = self._css()
        self.assertIn(".table-stack tbody tr", css)
        self.assertIn(".table-stack-dense", css)
        js = self.client.get("/static/app.js").text
        self.assertIn("labelStackedRows", js)
        for cls in ("table-collectors", "table-alert-noise", "table-nodes", "table-interfaces", "table-top-hosts"):
            self.assertRegex(self.client.get("/").text, r'class="data-table table-stack[^"]*' + cls)

    def test_empty_chart_message_is_drawn_at_the_canvas_css_size(self):
        js = self.client.get("/static/app.js").text
        self.assertIn("function drawEmptyChartMessage(canvas)", js)
        self.assertIn("EMPTY_CHART_OBSERVERS[canvasId] = new ResizeObserver", js)

    def test_phone_alert_strip_keeps_firing_alerts_in_view(self):
        page = self.client.get("/").text
        self.assertIn('id="alert-strip"', page)
        js = self.client.get("/static/app.js").text
        self.assertIn("initAlertStrip", js)
        self.assertIn("severityLabel", js)
        css = self.client.get("/static/styles.css").text
        self.assertRegex(css, r"\.alert-strip \{\s*display: block;\s*position: sticky;\s*top: 0;")

    def test_recent_syslog_feed_shows_the_date_for_older_events(self):
        js = self.client.get("/static/app.js").text
        self.assertIn("function formatLocalFeed(utcSeconds)", js)
        self.assertIn("escapeHtml(formatLocalFeed(evObj.ts_utc))", js)

    def test_status_dots_stay_round_and_aligned_and_phones_get_a_device_summary(self):
        css = self._css()
        self.assertRegex(css, r"\.alert-icon-wrap \{\s*flex: none;")
        self.assertRegex(css, r"\.avail-dot \{ flex: none;")
        self.assertIn(".alerts-headline { align-items: flex-start; }", css)
        self.assertIn("margin-top: calc((1.5rem - 0.6rem) / 2)", css)
        self.assertIn(".alerts-headline .freshness-meta { display: block; margin-left: 0; }", css)
        self.assertRegex(css, r"\.avail-pill\.avail-ok \{ display: none; \}")
        self.assertIn('id="welcome-devices-summary"', self.client.get("/").text)
        js = self.client.get("/static/app.js").text
        self.assertIn(" devices up", js)

    def test_no_full_pill_shapes_only_rounded_rectangles_and_dots(self):
        css = self._css()
        self.assertNotIn("border-radius: 9999px", css)
        self.assertRegex(css, r"\.badge \{[^}]*border-radius: var\(--radius-sm\)")
        self.assertRegex(css, r"\.avail-pill \{[^}]*border-radius: var\(--radius-md\)")

    def test_alert_guidance_folds_with_a_long_list_and_remembers_the_choice(self):
        js = self.client.get("/static/app.js").text
        self.assertIn("<details class='alert-item-guidance'", js)
        self.assertIn("max-width: 700px", js)
        self.assertIn("data.active.length <= 2", js)
        self.assertIn("STATE.alertGuidanceOpen", js)
        css = self.client.get("/static/styles.css").text
        # the toggle must stay reachable on desktop: a hidden summary would make a folded card impossible to unfold
        self.assertNotRegex(css, r"\.alert-item-guidance > summary \{ display: none; \}")

    def test_active_alerts_show_guidance_and_open_the_timeline_at_their_moment(self):
        js = self.client.get("/static/app.js").text
        self.assertIn("alert-item-guidance", js)
        self.assertIn("Why it matters:", js)
        self.assertIn("Check first:", js)
        # the button centres the existing timeline on the moment the alert fired
        self.assertRegex(js, r"STATE\.timelineAtUtc = firedUtc;\s*switchTab\(\"tab-timeline\"\);\s*\}\);\s*loadTimeline\(\);")
        # the card button and the Ctrl+K palette share one function
        self.assertIn("openAlertTimelineFor(e.currentTarget.getAttribute(\"data-device\")", js)
        css = self.client.get("/static/styles.css").text
        self.assertIn(".alert-item-guidance", css)
        # the time and the button may wrap and never exceed the card, or on a phone the button pokes out of its border
        self.assertRegex(css, r"\.alert-item-actions\s*\{[^}]*flex-wrap: wrap;[^}]*max-width: 100%")

    def test_alert_dot_is_drawn_with_css_so_it_is_centred(self):
        html = self.client.get("/").text
        self.assertIn('<span class="alert-icon" aria-hidden="true"></span>', html)
        self.assertNotIn("●", html)
        css = self.client.get("/static/styles.css").text
        self.assertRegex(css, r"\.alert-icon\s*\{[^}]*border-radius: 50%;[^}]*background: currentColor")

    def test_brand_is_centred_not_baseline_aligned(self):
        css = self.client.get("/static/styles.css").text
        self.assertRegex(css, r"\.brand-text\s*\{\s*display: flex;\s*align-items: center;")

    def test_flows_card_keeps_a_short_value_so_it_never_wraps(self):
        html = self.client.get("/").text
        self.assertIn('id="kpi-flows-sub"', html)
        js = self.client.get("/static/app.js").text
        self.assertNotIn('" flows (" + formatBytes', js)
        self.assertIn('getElementById("kpi-flows-sub")', js)

    def test_auto_refresh_holds_while_the_user_selects_text_or_types(self):
        # Panels are rebuilt on each refresh, which drops a text selection or a typed value.
        js = self.client.get("/static/app.js").text
        self.assertIn("function userIsReading()", js)
        self.assertIn("userIsReading() && STATE.heldCycles < MAX_HELD_CYCLES", js)

    def test_tables_are_patched_in_place_instead_of_cleared_and_rebuilt(self):
        js = self.client.get("/static/app.js").text
        self.assertIn("function commitRows(container, holder)", js)
        # the panels that hold rows with focus, selection or listeners no longer clear themselves
        for panel in ("logs-tbody", "top-hosts-tbody", "active-alerts-list", "welcome-devices", "interfaces-tbody"):
            self.assertIn('getElementById("%s")' % panel, js)
        self.assertNotRegex(js, r"(?:logsTbody|hostsTbody|listEl|strip|ifTbody|tbody)\.innerHTML = \"\";")
        # buttons wired after the render must not get a second listener when the row is kept
        self.assertGreaterEqual(js.count(".listenerWired"), 6)

    def test_countdown_has_a_fixed_width_so_the_buttons_beside_it_do_not_move(self):
        css = self.client.get("/static/styles.css").text
        rule = re.search(r"\.countdown-indicator\s*\{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        self.assertRegex(rule.group(1), r"(?<!min-)width: [\d.]+rem")

    def test_charts_expose_their_values_to_screen_readers(self):
        html = self.client.get("/").text
        for canvas_id in ("overview-chart", "bw-canvas", "tl-canvas"):
            self.assertRegex(html, r'<canvas id="%s"[^>]*role="img"[^>]*aria-label=' % canvas_id)
        js = self.client.get("/static/app.js").text
        self.assertIn("describeRateChart(canvas, points || [])", js)

    def test_no_external_resources_in_html(self):
        html = self.client.get("/").text
        self.assertEqual(re.findall(r'(?:src|href)="https?://', html), [])

    def test_roadmap_features_exist(self):
        html = self.client.get("/").text
        for element_id in (
            "cmd-palette-btn",
            "cmd-palette-modal",
            "tv-mode-btn",
            "tv-indicator",
            "overview-top-if-wrap",
            "chk-compare-24h",
        ):
            self.assertIn('id="%s"' % element_id, html)
        js = self.client.get("/static/app.js").text
        self.assertIn("openCommandPalette", js)
        self.assertIn("toggleTvMode", js)
        self.assertIn("compare_points", js)
        css = self.client.get("/static/styles.css").text
        self.assertIn(".cmd-palette-modal", css)
        self.assertIn("body.tv-mode", css)

    def test_kpi_sparklines_exist_in_html_css_and_js(self):
        html = self.client.get("/").text
        for spark_id in (
            "spark-alerts",
            "spark-flows",
            "spark-traffic",
            "spark-host-traffic",
            "spark-host-syslog",
        ):
            self.assertIn('id="%s"' % spark_id, html)
            self.assertIn('class="kpi-sparkline-wrap"', html)

        css = self.client.get("/static/styles.css").text
        self.assertIn(".kpi-header-row", css)
        self.assertIn(".kpi-sparkline-wrap", css)
        self.assertIn(".sparkline-svg", css)

        js = self.client.get("/static/app.js").text
        self.assertIn("function renderSparkline(", js)
        self.assertIn('renderSparkline("spark-alerts"', js)
        self.assertIn('renderSparkline("spark-traffic"', js)
        self.assertIn('renderSparkline("spark-flows"', js)
        self.assertIn('renderSparkline("spark-host-syslog"', js)

    def test_alert_rules_settings_controls_exist_in_html_css_and_js(self):
        html = self.client.get("/").text
        for element_id in (
            "dest-rules-operational",
            "btn-reset-rules",
            "set-thresh-bw-bps",
            "set-thresh-bw-hold",
            "set-thresh-failed-logins",
            "set-thresh-port-scan-ports",
            "toggle-rule-high-bandwidth",
            "toggle-rule-interface-down",
            "toggle-rule-flow-port-scan",
        ):
            self.assertIn('id="%s"' % element_id, html)

        css = self.client.get("/static/styles.css").text
        self.assertIn(".settings-subheading", css)
        self.assertIn(".toggles-grid", css)
        self.assertIn(".thresholds-grid", css)

        js = self.client.get("/static/app.js").text
        self.assertIn("RULE_THRESHOLD_FIELDS", js)
        self.assertIn("RULE_TOGGLE_FIELDS", js)
        self.assertIn("btn-reset-rules", js)
        self.assertIn("clear_rules", js)

    def test_modular_device_tabs_and_host_telemetry_panel_exist(self):
        html = self.client.get("/").text
        for element_id in (
            "nav-tab-bandwidth-label",
            "nav-tab-bandwidth-icon",
            "bandwidth-host-panel",
            "host-panel-title",
            "host-panel-subtitle",
            "host-telemetry-banner",
            "host-summary-cards",
            "host-dns-section",
            "host-dns-latency",
            "host-dns-queries",
            "host-dns-blocked",
            "host-dns-gravity",
            "host-bw-flow-vol",
            "host-bw-top-peer",
            "btn-switch-router-bw",
            "flows-panel-title",
            "flows-panel-subtitle",
            "syslog-panel-title",
            "syslog-panel-subtitle",
        ):
            self.assertIn('id="%s"' % element_id, html)

        js = self.client.get("/static/app.js").text
        self.assertIn("renderHostBandwidthView", js)
        self.assertIn("nav-tab-bandwidth-label", js)
        self.assertIn("btn-switch-router-bw", js)

    def test_dynamic_target_charts_exports_and_organized_settings_exist(self):
        html = self.client.get("/").text
        for element_id in (
            "host-telemetry-canvas",
            "host-chart-tooltip",
            "export-host-csv",
            "export-host-json",
            "export-tl-csv",
            "export-tl-json",
            "export-nodes-csv",
            "export-nodes-json",
            "settings-nav-bar",
            "settings-inventory-section",
            "settings-devices-tbody",
            "pref-refresh-interval",
            "pref-default-range",
            "bootstrap-admin-box",
        ):
            self.assertIn('id="%s"' % element_id, html)

        js = self.client.get("/static/app.js").text
        self.assertIn("renderHostTelemetryChart", js)
        self.assertIn("switchSettingsSection", js)
        self.assertIn("saveDeviceMetadata", js)
        self.assertIn("bootstrapFirstAdmin", js)

    def test_overview_interface_picker_and_functions_exist(self):
        html = self.client.get("/").text
        self.assertIn('id="overview-if-picker-wrap"', html)
        self.assertIn('id="overview-if-select"', html)

        js = self.client.get("/static/app.js").text
        self.assertIn("selectOverviewInterface", js)
        self.assertIn("loadOverviewBandwidthChart", js)

    def test_every_render_and_load_function_called_in_the_bundle_is_defined(self):
        # K38 called renderSvgSparkline(), which never existed: the overview chart code threw a ReferenceError on
        # every refresh and only the console showed it. Catch any call to a render*/load* helper nobody defines.
        js = self.client.get("/static/app.js").text
        called = set(re.findall(r"(?<![\.\w])((?:render|load)[A-Z]\w*)\(", js))
        defined = set(re.findall(r"function\s+((?:render|load)[A-Z]\w*)\s*\(", js))
        defined |= set(re.findall(r"(?:var|let|const)\s+((?:render|load)[A-Z]\w*)\s*=", js))
        self.assertEqual(sorted(called - defined), [])

    def test_tab_icons_are_one_family_of_svg_not_unicode_glyphs(self):
        # Unicode glyphs (◈ ▲ ⇄ ≡ ⧗ ♥ ⚙ and two emoji) measured 8 to 15 px wide, so the labels did not line up.
        html = self.client.get("/").text
        icons = re.findall(r'<(\w+)[^>]*class="tab-icon"', html)
        self.assertEqual(len(icons), 8)
        self.assertEqual(set(icons), {"svg"})
        for glyph in "◈▲⇄≡⧗♥⚙":
            self.assertNotIn(glyph, html)
        js = self.client.get("/static/app.js").text
        self.assertNotIn("navBwIcon.textContent", js)

    def test_small_controls_meet_the_minimum_target_size(self):
        # WCAG 2.2 AA (2.5.8) asks for 24 CSS px. Measured before: range pills 20 px, checkboxes 13 px, host links 15 px.
        css = (Path(__file__).resolve().parent.parent / "web" / "static" / "styles.css").read_text(encoding="utf-8")
        pills = "".join(re.findall(r"\.range-pill-btn\s*\{([^}]*)\}", css))
        self.assertRegex(pills, r"min-height:\s*(2[4-9]|[3-9]\d)px")
        chip = re.search(r"\.toggle-chip input\[type=\"checkbox\"\]\s*\{([^}]*)\}", css).group(1)
        self.assertRegex(chip, r"width:\s*(1[6-9]|[2-9]\d)px")
        link = re.search(r"\.host-link\s*\{([^}]*)\}", css).group(1)
        self.assertRegex(link, r"min-height:\s*(2[4-9]|[3-9]\d)px")

    def test_settings_grids_cannot_be_wider_than_a_phone(self):
        # At 375 px the Settings tab scrolled sideways by 4 px because the grids asked for 240-260 px columns.
        css = (Path(__file__).resolve().parent.parent / "web" / "static" / "styles.css").read_text(encoding="utf-8")
        for selector in (r"\.dest-fields", r"\.dest-fields\.toggles-grid", r"\.dest-fields\.thresholds-grid", r"\.destinations-grid"):
            block = re.search(selector + r"\s*\{([^}]*)\}", css).group(1)
            self.assertIn("min(100%", block)
        self.assertRegex(css, r"\.settings-form > \*, \.dest-fields > \*\s*\{[^}]*min-width:\s*0")

    def test_inventory_community_field_has_an_accessible_name(self):
        js = (Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "views" / "settings.js").read_text(encoding="utf-8")
        field = re.search(r"<input[^>]*class='inv-comm-input'[^>]*>", js).group(0)
        self.assertIn("aria-label", field)

    def _css(self):
        return (Path(__file__).resolve().parent.parent / "web" / "static" / "styles.css").read_text(encoding="utf-8")

    def _src(self, *parts):
        return (Path(__file__).resolve().parent.parent / "web" / Path(*parts)).read_text(encoding="utf-8")

    def test_top_interfaces_table_is_not_cut_in_the_middle_of_a_row(self):
        # The table had max-height 200 px and showed the fourth row sliced in half. It now lists five rows, whole.
        css = self._css()
        block = re.search(r"\.top-if-table-container\s*\{([^}]*)\}", css).group(1)
        self.assertNotIn("max-height", block)
        self.assertIn("TOP_INTERFACES_SHOWN", self._src("static", "js", "views", "overview.js"))

    def test_conversations_table_keeps_numbers_on_one_line(self):
        css = self._css()
        self.assertRegex(css, r"\.table-top-pairs td:nth-child\(n \+ 3\)[^{]*\{[^}]*white-space:\s*nowrap")

    def test_scrollable_tables_show_that_they_continue(self):
        # K44: the fade behind the rows vanished under any row with its own background (the hover row). An arrow on a
        # wrapper does not: it is drawn by the wrapper, outside the scrolling element, only on the side that continues.
        css = self._css()
        block = re.search(r"\.table-container\s*\{([^}]*)\}", css).group(1)
        self.assertNotIn("linear-gradient", block)
        self.assertNotIn("local", block)
        self.assertRegex(css, r"\.scroll-hint-right\s*\{")
        self.assertRegex(css, r"\.scroll-hint-left\s*\{")
        self.assertNotRegex(css, r"\.scroll-hint::(before|after)")  # a drawn arrow that ignores clicks is what K73 replaced
        js = self.client.get("/static/app.js").text
        self.assertIn("function initScrollHints", js)
        self.assertIn("ResizeObserver", js)

    def test_tables_take_the_room_the_window_has_instead_of_a_fixed_height(self):
        # K44: "Top Conversations" was cut at 380 px with 150 px of empty screen below; the logs at 520 px.
        css = self._css()
        for selector, variable in ((r"\.table-container\.compact", "--table-room"), (r"\.log-console-container", "--table-room-log")):
            block = re.search(selector + r"\s*\{([^}]*)\}", css).group(1)
            self.assertIn("max-height: var(" + variable + ")", block)
        self.assertRegex(css, r"--table-room:\s*max\(\d+rem,\s*calc\(100dvh")
        self.assertNotRegex(css, r"max-height:\s*(380|520|240)px")

    def test_header_banner_and_content_share_one_column(self):
        # K44: the viewport was capped at 1700 px while the header and the banner took the whole width.
        css = self._css()
        self.assertNotIn("max-width: 1700px", css)
        self.assertRegex(css, r"--edge:\s*max\(var\(--gutter\),\s*calc\(\(100% - var\(--page-max\)\) / 2\)\)")
        for selector in (r"\.noc-viewport", r"\.alerts-banner", r"\.noc-navbar"):
            block = re.search(selector + r"\s*\{([^}]*)\}", css).group(1)
            self.assertIn("var(--edge)", block, selector)

    def test_panel_actions_start_at_the_left_when_they_wrap_under_the_title(self):
        # K44: with justify-content: flex-end the row that wrapped started 105 px in and left the badge alone on the right.
        css = self._css()
        self.assertNotRegex(css, r"\.panel-actions\s*\{[^}]*justify-content:\s*flex-end")
        block = re.search(r"\.search-input-group\s*\{([^}]*)\}", css).group(1)
        # An explicit width, so the actions' intrinsic width counts the field at its real size and the row wraps once.
        self.assertRegex(block, r"width:\s*22rem")

    def test_header_controls_are_grouped_and_the_phone_clock_joins_the_target_row(self):
        css = self._css()
        html = self._src("partials", "header_nav.html")
        self.assertRegex(html, r'class="control-group icon-cluster"')
        self.assertRegex(css, r"\.auto-refresh-group,\s*\.icon-cluster\s*\{\s*gap:\s*0\.375rem")
        phone = css[css.rindex("Phone header: the clock sits on the Target row"):]
        self.assertRegex(phone, r"\.clock-display\s*\{[^}]*order:\s*2")
        self.assertRegex(phone, r"\.control-group:first-child\s*\{[^}]*order:\s*1")
        self.assertRegex(phone, r"border-left:\s*0")

    def test_chart_toggles_have_room_between_the_chips(self):
        css = self._css()
        block = css[css.rindex(".chart-toggles { flex-wrap"):].split("\n")[0]
        self.assertRegex(block, r"gap:\s*0\.35rem 1\.1rem")

    def test_active_tab_is_an_underline_that_animates_state_only(self):
        # K44: the boxed active tab was replaced by an underline that grows (transform), in 150-250 ms.
        css = self._css()
        block = css[css.rindex("Tab bar: a flat row"):]
        self.assertRegex(block, r"\.nav-tab-btn::before\s*\{[^}]*transform:\s*scaleX\(0\)[^}]*transition:\s*transform 0\.22s")
        self.assertRegex(block, r"\.nav-tab-btn\.active::before\s*\{\s*transform:\s*scaleX\(1\)")
        self.assertNotRegex(block, r"transition:\s*all")
        # In the menu list (<= 1100 px) the tick marks the current entry, so the underline steps aside.
        self.assertRegex(block, r"@media \(max-width: 1100px\)[^@]*\.nav-tab-btn::before\s*\{\s*display:\s*none")

    def test_pressing_the_current_tab_reloads_its_view(self):
        js = self.client.get("/static/app.js").text
        self.assertIn("async function reloadView(tabId)", js)
        self.assertRegex(js, r"if \(targetTab === STATE\.activeTab\) \{\s*reloadView\(targetTab\);\s*return;")
        for loader in ("loadOverviewAndDevice", "loadBandwidth", "loadFlows", "loadLogs", "loadTimeline", "loadHealth", "loadSettings"):
            self.assertIn("await " + loader + "()", js.split("async function reloadView")[1].split("function setNavMenuOpen")[0])

    def test_toasts_keep_clear_of_the_window_edge_and_are_announced_politely(self):
        css = self._css()
        block = css[css.rindex(".toast-container {\n  right:"):].split("}")[0]
        self.assertRegex(block, r"right:\s*max\(1\.5rem")
        self.assertRegex(block, r"bottom:\s*max\(1\.5rem")
        self.assertRegex(css, r"@media \(max-width: 700px\) \{\s*\.toast-container \{ left: var\(--gutter\); right: var\(--gutter\)")
        html = self._src("partials", "modals.html")
        self.assertIn('role="status" aria-live="polite"', html)
        self.assertNotIn("assertive", html)
        self.assertNotIn("ℹ", self._src("static", "js", "formatters.js"))

    def test_command_palette_keeps_air_between_field_list_and_rows(self):
        css = self._css()
        block = css[css.rindex("Command palette: air between"):]
        self.assertRegex(block, r"\.cmd-palette-results\s*\{[^}]*padding:\s*0\.75rem;\s*gap:\s*0\.35rem")
        self.assertRegex(block, r"\.cmd-item:hover,\s*\.cmd-item\.active\s*\{[^}]*border-color:\s*transparent")

    def test_settings_use_one_spacing_rhythm_and_no_inline_styles(self):
        css = self._css()
        html = self._src("partials", "tab_settings.html")
        self.assertNotIn("style=", html)
        block = css[css.rindex("Settings: one rhythm"):]
        self.assertRegex(block, r"--s-label:\s*0\.5rem;\s*--s-field:\s*1\.25rem;\s*--s-block:\s*1\.75rem")
        self.assertRegex(block, r"\.settings-field\s*\{\s*gap:\s*var\(--s-label\)")
        # Cards of a row share the same top: grid rows no longer stretch to fill spare height.
        self.assertRegex(block, r"\.destinations-grid \.dest\s*\{[^}]*align-content:\s*start")
        # A wide table inside a grid section must not widen the page on a phone.
        self.assertRegex(css, r"#settings-panel \.settings-section-block > \*, \.scroll-hint \{ min-width: 0")

    def test_no_frosted_glass_and_no_loose_colours_after_the_tokens(self):
        # The palette scrim and the TV badge used rgba() literals and blur; DESIGN.md bans decorative glass and loose colours.
        css = self._css()
        self.assertNotIn("backdrop-filter", css)
        after_tokens = css[css.index("* {\n  box-sizing"):]
        self.assertNotRegex(after_tokens, r"rgba\(")

    def test_settings_section_buttons_use_the_attribute_the_script_reads(self):
        # K51: the buttons carried data-settings-section but the script read data-sec, so they never switched anything
        # and "All Sections" was the only view there ever was.
        html = self._src("partials", "tab_settings.html")
        js = self.client.get("/static/app.js").text
        for name in ("rules", "inventory", "maintenance", "channels", "account", "all"):
            self.assertIn('data-settings-section="' + name + '"', html)
        self.assertNotIn('getAttribute("data-sec")', js)
        self.assertIn('getAttribute("data-settings-section")', js)
        groups = set(re.findall(r'data-section-group="([a-z]+)"', html))
        self.assertEqual(groups, {"account", "inventory", "maintenance", "rules", "channels", "always"})
        # The buttons use the "active" class the stylesheet styles, not an "is-active" nobody defines.
        self.assertNotIn("settings-sec-btn is-active", html)

    def test_settings_open_on_one_section_chosen_by_who_is_signed_in(self):
        js = self.client.get("/static/app.js").text
        self.assertIn("function pickDefaultSettingsSection(noAdminYet)", js)
        self.assertIn('switchSettingsSection("account", false)', js)
        self.assertIn('switchSettingsSection("rules", false)', js)
        # K117: only a server with no admin yet opens on the account; anyone else gets the rules and, if not signed in, a hint.
        body = js.split("function pickDefaultSettingsSection")[1].split("// Which write-only fields")[0]
        self.assertIn("} else if (noAdminYet) {", body)
        self.assertNotIn('authSession === null && readAdminToken() === ""', body)
        self.assertNotIn("canEditSettings", body)
        self.assertIn("function updateSigninHint()", js)
        self.assertIn('id="settings-signin-hint"', self._src("partials", "tab_settings.html"))
        # A link from Health opens the inventory without turning it into the remembered choice.
        self.assertIn('switchSettingsSection("inventory", "jump")', js)
        css = self._css()
        self.assertRegex(css, r"#tab-settings\.settings-pending \[data-section-group\]\s*\{\s*display:\s*none")

    def test_destination_filters_are_folded_and_rules_are_ticked_chips(self):
        html = self._src("partials", "tab_settings.html")
        self.assertNotIn("multiple size", html)
        self.assertNotIn("Ctrl-click", html)
        for name in ("ntfy", "discord", "slack", "telegram", "webhook", "email"):
            self.assertIn('<details class="dest-filters" id="dest-filters-' + name + '">', html)
            self.assertIn('id="set-' + name + '-rules" class="rule-chip-list"', html)
            self.assertIn('id="set-' + name + '-rules-count"', html)
        js = self.client.get("/static/app.js").text
        self.assertIn("function updateDestinationFilterSummaries()", js)
        self.assertNotIn("select.options[o].selected", js)
        css = self._css()
        self.assertRegex(css, r"\.rule-chip:has\(input:checked\)\s*\{[^}]*var\(--accent-soft\)")
        self.assertRegex(css, r"@media \(pointer: coarse\) \{ \.rule-chip \{ min-height: 40px; \}")

    def test_no_transition_animates_every_property(self):
        # "transition: all" also animates layout properties and made hover and state changes slide. Name the properties.
        self.assertNotIn("transition: all", self._css())

    def test_small_controls_reach_the_minimum_target_size(self):
        css = self._css()
        block = css[css.rindex("Audit (09-11): targets"):]
        self.assertRegex(block, r"@media \(pointer: fine\) \{[\s\S]*?\.btn-small \{ min-height: 26px; \}")
        self.assertRegex(block, r"\.inv-name-input, \.inv-comm-input, \.form-input-small \{ min-height: 28px")
        self.assertRegex(block, r"@media \(pointer: coarse\) \{[\s\S]*?\.tool-item, \.alert-strip, \.brand-link, \.settings-sec-btn, \.sev-pill \{ min-height: 40px")

    def test_static_files_are_versioned_so_a_deploy_cannot_serve_old_css(self):
        html = self.client.get("/").text
        versions = set(re.findall(r'/static/(?:styles\.css|app\.js|theme-init\.js)\?v=([0-9a-f]{8,})', html))
        self.assertEqual(len(re.findall(r'/static/(?:styles\.css|app\.js|theme-init\.js)\?v=', html)), 3)
        self.assertEqual(len(versions), 1)

    def test_host_banner_uses_svg_not_emoji(self):
        for text in (self._src("partials", "tab_bandwidth.html"), self._src("static", "js", "views", "bandwidth.js"), self._src("static", "js", "views", "overview.js")):
            self.assertNotIn("🛡", text)
            self.assertNotIn("🖥", text)

    def test_no_decorative_motion_or_glow(self):
        css = self._css()
        dot = re.search(r"\.tv-indicator-dot\s*\{([^}]*)\}", css).group(1)
        self.assertNotIn("animation", dot)
        self.assertNotIn("0 0 20px var(--color-cyan-glow)", css)

    def test_no_coloured_side_stripe_on_the_host_note(self):
        css = self._css()
        block = re.search(r"\.host-banner-note\s*\{([^}]*)\}", css).group(1)
        self.assertNotRegex(block, r"border-left:\s*[2-9]px")

    def test_overview_stats_are_one_strip_not_a_grid_of_identical_cards(self):
        # PRODUCT.md rejects repeated KPI cards: the stats are now cells of one bordered strip with 1 px dividers.
        css = self._css()
        self.assertNotRegex(css, r"\.kpi-card::before\s*\{")
        ribbon = re.search(r"\.kpi-ribbon\s*\{([^}]*)\}", css).group(1)
        self.assertIn("gap: 1px", ribbon)
        card = re.search(r"\.kpi-card\s*\{([^}]*)\}", css).group(1)
        self.assertNotIn("box-shadow", card)
        self.assertNotIn("transform", card)

    def test_tips_start_closed_so_the_data_is_above_the_fold(self):
        js = self._src("static", "js", "views", "welcome.js")
        self.assertIn('!== "full"', js)

    def test_secondary_header_controls_live_in_one_labelled_menu(self):
        html = self.client.get("/").text
        menu = re.search(r'<details[^>]*id="tools-menu".*?</details>', html, re.S).group(0)
        for control in ("sound-btn", "help-btn", "tv-mode-btn"):
            self.assertIn('id="%s"' % control, menu)
        for label in ("Alert sound", "TV wallboard", "Keyboard shortcuts"):
            self.assertIn(label, menu)

    def test_rate_chart_does_not_draw_markers_or_heavy_fills(self):
        js = self._src("static", "js", "components", "charts.js")
        self.assertNotIn("ptRadius = 3", js)
        self.assertNotIn("0.15)", re.search(r"label: rxName.*?borderWidth", js, re.S).group(0))

    def test_dns_chart_counts_queries_and_never_calls_them_bits(self):
        # The DNS chart plots running totals of queries: axis, tooltip and legend use a plain count, not "kbit/s".
        charts = self._src("static", "js", "components", "charts.js")
        self.assertIn("options.format", charts)
        self.assertIn("return fmt(val);", charts)
        bandwidth = self._src("static", "js", "views", "bandwidth.js")
        self.assertIn("format: formatQueryCount", bandwidth)
        self.assertIn("STATE.hostChartOptions = hostChartOptions;", bandwidth)
        main = self._src("static", "js", "main.js")
        self.assertNotIn("STATE.cacheHostPoints, [], {}", main)  # a redraw must keep the DNS options
        # a DNS target opens on its own chart, not on the empty IPFIX one
        overview = self._src("static", "js", "views", "overview.js")
        self.assertIn('STATE.hostChartMode = "dns"', overview)

    def test_open_dropdown_list_has_room_and_keeps_closed_lists_hidden(self):
        css = self._css()
        picker = re.search(r"select::picker\(select\)\s*\{([^}]*)\}", css).group(1)
        self.assertRegex(picker, r"padding:\s*0\.4rem")
        # display must only be set on the OPEN picker: on the closed one every list stays on screen
        self.assertNotRegex(picker, r"display:\s*flex")
        self.assertIn("select::picker(select):popover-open { display: flex;", css)
        option = re.search(r"select option\s*\{([^}]*display: flex[^}]*)\}", css).group(1)
        self.assertRegex(option, r"min-height:\s*38px")
        self.assertRegex(option, r"padding:\s*0\.5rem 0\.8rem")

    def test_header_controls_fit_between_761_and_1000_px_and_cut_long_names(self):
        # Found by the layout sweep: the K44 gap (1.25 rem) overrode the tight gap of this range and the Target select
        # slid 8-20 px off the left edge at 768 px; a long name wrapped onto two lines inside the 34 px control.
        css = self._css()
        self.assertRegex(css, r"@media \(min-width: 761px\) and \(max-width: 1000px\)\s*\{\s*\.noc-navbar-controls \{ gap: 0\.5rem 0\.75rem; \}")
        self.assertRegex(css, r"\.noc-navbar-controls \.form-select \{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; \}")
        # and the late rule that caused it still exists BEFORE the fix, so the fix wins by order
        self.assertLess(css.index(".noc-navbar-controls { gap: 0.75rem 1.25rem; }"), css.index("min-width: 761px) and (max-width: 1000px)"))

    def test_form_dropdowns_have_room_and_table_badges_share_a_width(self):
        css = self._css()
        block = re.search(r"\.tab-pane select\.form-select:not\(\.form-select-small\)\s*\{([^}]*)\}", css).group(1)
        self.assertRegex(block, r"min-height:\s*40px")
        self.assertRegex(block, r"padding-left:\s*0\.9rem")
        badge = re.search(r"\.data-table td \.badge\s*\{([^}]*)\}", css).group(1)
        self.assertRegex(badge, r"min-width:\s*3\.9rem")
        self.assertRegex(badge, r"text-align:\s*center")

    def test_motion_uses_one_easing_and_no_undefined_keyframes(self):
        css = self._css()
        self.assertNotIn("ease-in-out", css)
        used = set(re.findall(r"animation:\s*([A-Za-z0-9_-]+)\s", css))
        defined = set(re.findall(r"@keyframes\s+([A-Za-z0-9_-]+)", css))
        self.assertEqual(used - defined, set())

    def test_every_dropdown_has_its_arrow_inset_from_the_edge(self):
        # The native arrow sat glued to the right edge and to the text ("30 min v"). One rule, at the end of the sheet so it
        # also beats the per-control paddings, draws the arrow with room on both sides.
        css = self._css()
        block = re.findall(r"select:not\(\[multiple\]\):not\(\[size\]\)\s*\{([^}]*)\}", css)
        self.assertGreaterEqual(len(block), 1)
        self.assertRegex(block[0], r"appearance:\s*none")
        self.assertRegex(block[0], r"padding-right:\s*2rem")
        self.assertRegex(block[0], r"background-position:\s*right 0\.7rem center")
        self.assertIn("var(--select-chevron)", block[0])
        # K125: equal padding above and below by default (a native select centres its own text; measured in Falkon, the old
        # 0 / 3 px split put the text 1.5 px above its arrow). The lift measured against the "TARGET" label belongs to the
        # appearance: base-select block only.
        top = re.search(r"padding-top:\s*([\d.]+)px", block[0]).group(1)
        bottom = re.search(r"padding-bottom:\s*([\d.]+)px", block[0]).group(1)
        self.assertEqual(top, bottom)
        supports = css[css.index("@supports (appearance: base-select)"):]
        self.assertRegex(supports, r"padding-top:\s*0;\s*padding-bottom:\s*3px")
        self.assertRegex(css, r':root\[data-theme="light"\]\s*\{\s*--select-chevron')
        self.assertGreater(css.rindex("select:not([multiple])"), css.rindex(".alert-item-actions select.alert-silence-minutes"))
        # Bare text fields and selects (the inventory table in Settings) had 0 horizontal padding: text glued to the border.
        fields = re.search(r'input\[type="text"\][^{]*\{([^}]*)\}', css[css.rindex("Dropdowns: one arrow"):]).group(1)
        self.assertRegex(fields, r"padding-inline:\s*0\.6rem")

    def test_header_controls_share_one_height_and_the_refresh_icon_is_svg(self):
        # Measured: refresh 31.2, countdown 29.4, pause 30, selects 30.2, window pills 32 and icon buttons 34 px. The glyph
        # of the refresh button was a text character that sat low and small next to the label.
        css = self._css()
        self.assertRegex(css, r"--control-h:\s*34px")
        block = css[css.rindex("Header controls share one height"):]
        for selector in ("#refresh-btn", ".countdown-indicator", "#pause-btn", ".time-range-toggle"):
            self.assertIn(selector, block)
        self.assertRegex(block, r"height:\s*var\(--control-h\)")
        self.assertRegex(block, r"#refresh-btn \.refresh-icon[^}]*translate:")
        self.assertRegex(block, r"\.tool-item svg\s*\{[^}]*translate:")
        html = self.client.get("/").text
        self.assertNotIn("↻", html)
        self.assertRegex(html, r'<svg class="refresh-icon"')

    def test_deep_links_are_bundled_and_the_copy_button_is_in_the_menu(self):
        html = self._src("partials", "header_nav.html")
        self.assertIn('id="copy-link-btn"', html)
        self.assertIn("Copy link to this view", html)
        js = self._src("static", "app.js")
        for name in ("function parseRoute(", "function applyRoute(", "function routeSync(", "function copyViewLink(", "function parseTimelineHash("):
            self.assertIn(name, js)
        route = self._src("static", "js", "components", "route.js")
        # the hash is only ever read through the parser and written back through the validated state
        self.assertNotIn("innerHTML", route)
        self.assertNotIn("location.href =", route)
        self.assertIn("pushState", route)
        self.assertIn("replaceState", route)

    def test_timeline_legend_and_heading_are_not_duplicated(self):
        # The legend spans already sit next to a dot in the partial: the script added a second one ("■■TX Rate"), and the
        # hosts heading carried the flow count twice ("(0 flows) (0 flows)", two elements with the same id).
        js = self._src("static", "js", "views", "timeline.js")
        self.assertNotIn("<span class='legend-dot", js)
        self.assertNotIn("Hosts (<span", js)

    def test_no_coloured_side_stripes_anywhere_in_the_stylesheet(self):
        # DESIGN.md forbids them; timeline log items still used a 3 px coloured left border for severity.
        self.assertNotRegex(self._css(), r"border-(left|right):\s*[2-9]px")

    def test_panel_action_controls_share_one_height(self):
        css = self._css()
        block = css[css.rindex("Panel action rows share one height"):]
        self.assertRegex(block, r"min-height:\s*28px")
        for selector in (".panel-actions .btn-small", ".panel-actions .form-select-small", ".window-span-badge", ".freshness-badge", ".panel-actions .badge", ".alert-item-actions .btn-small", ".alert-item-actions select.alert-silence-minutes"):
            self.assertIn(selector, block)

    def test_view_modules_have_no_hard_coded_colours(self):
        # DESIGN.md: every colour comes from a variable. Nine sparkline calls passed rgba() literals that ignored the theme.
        root = Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "views"
        for path in sorted(root.glob("*.js")):
            self.assertNotRegex(path.read_text(encoding="utf-8"), r"rgba?\(", path.name)

    def test_brand_has_no_invented_version_badge(self):
        # "Ops v2.4" matched no version anywhere in the project: on a page that promises verifiable data it is noise.
        self.assertNotIn("Ops v", self.client.get("/").text)

    def test_open_dropdown_list_follows_the_theme(self):
        # The open list used the operating system's look (bright blue highlight, square corners). Chromium can style it with
        # appearance: base-select; other browsers keep the native list, which at least gets the theme colours.
        css = self._css()
        block = css[css.rindex("@supports (appearance: base-select)"):]
        for needle in ("::picker(select)", "option:checked", "option::checkmark", "select::picker-icon", "var(--bg-hover)", "var(--bg-active)"):
            self.assertIn(needle, block)
        self.assertRegex(css, r"select option\s*\{[^}]*background")

    def test_pause_button_is_square_like_the_other_icon_buttons(self):
        css = self._css()
        block = css[css.rindex("Icon-only buttons are square"):]
        self.assertRegex(block, r"#pause-btn\s*\{[^}]*width:\s*var\(--control-h\)")
        self.assertNotRegex(css, r"#pause-btn\s*\{\s*min-width:\s*30px")

    def test_layout_and_state_classes_used_by_the_pages_have_styles(self):
        # These classes were used in the templates or scripts but had no rule at all: the syslog filter field and its button
        # stacked as blocks, text-dim and muted had no colour and the Always Editable badge was not green.
        css = self._css()
        for cls in ("search-input-group", "export-button-group", "text-dim", "muted", "status-ok"):
            self.assertRegex(css, r"\." + cls + r"[\s{.:,]", cls)
        block = re.search(r"\.search-input-group\s*\{([^}]*)\}", css).group(1)
        self.assertIn("display: flex", block)
        self.assertNotIn("(e.g. login, interface)", self._src("partials", "tab_syslog.html"))

    def test_panel_actions_start_at_the_left_and_selects_never_wrap_on_a_phone(self):
        css = self._css()
        block = css[css.rindex("Panel actions on a phone"):]
        self.assertRegex(block, r"@media \(max-width: 700px\)")
        self.assertRegex(block, r"\.panel-actions\s*\{[^}]*justify-content:\s*flex-start")
        self.assertRegex(block, r"white-space:\s*nowrap")
        self.assertRegex(block, r"\.search-input-group\s*\{[^}]*flex:\s*1 1 100%")

    def test_the_dot_nudge_is_scoped_and_buttons_get_the_font_metric_fix(self):
        css = (Path(__file__).resolve().parent.parent / "web" / "static" / "styles.css").read_text(encoding="utf-8")
        # No pixel nudges on the dots: they were measured on one font and scaling and looked wrong on any other screen.
        # The pill text is trimmed to the capitals (text-box), the headline dot is centred on its 1.5rem first line by arithmetic.
        self.assertNotRegex(css, r"\.avail-dot\s*\{[^}]*translate")
        self.assertNotRegex(css, r"\.avail-pill \.avail-dot\s*\{[^}]*translate")
        self.assertRegex(css, r"\.avail-name\s*\{\s*text-box:\s*trim-both cap alphabetic")
        self.assertRegex(css, r"\.alerts-headline \.avail-dot\s*\{\s*margin-top:\s*calc\(\(1\.5rem - 0\.6rem\) / 2\)")
        # Button text sat 0.7-1.5 px low with Segoe UI: same font with corrected ascent/descent, for <button> only
        # (a link's line box is already centred and would overshoot).
        self.assertRegex(css, r'@font-face\s*\{\s*font-family:\s*"NOC Button"[^}]*local\("Segoe UI"\)[^}]*ascent-override')
        self.assertRegex(css, r'button\.btn\s*\{\s*font-family:\s*"NOC Button",\s*var\(--font-sans\)')
        self.assertNotRegex(css, r'(?m)^\.btn\s*\{[^}]*NOC Button')

    def test_the_bandwidth_chart_ticks_are_time_only_for_one_day_and_the_swatch_is_aligned(self):
        js = (Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "components" / "charts.js").read_text(encoding="utf-8")
        self.assertIn("sameLocalDay ? formatLocalShort", js)
        self.assertIn("alignTooltipSwatches(", js)
        self.assertIn("labelColor", js)

    def test_health_has_the_space_by_source_table(self):
        root = Path(__file__).resolve().parent.parent
        html = (root / "web" / "partials" / "tab_health.html").read_text(encoding="utf-8")
        js = (root / "web" / "static" / "js" / "views" / "health.js").read_text(encoding="utf-8")
        self.assertIn('id="capacity-sources-tbody"', html)
        self.assertIn("renderCapacitySources(data)", js)
        # An estimate (no dbstat) is marked with "~" so it is not read as a measurement.
        self.assertIn('"~" + size', js)

    def test_host_health_settings_and_panel_exist_in_both_script_copies(self):
        root = Path(__file__).resolve().parent.parent
        settings_html = (root / "web" / "partials" / "tab_settings.html").read_text(encoding="utf-8")
        health_html = (root / "web" / "partials" / "tab_health.html").read_text(encoding="utf-8")
        field_ids = {
            "set-thresh-host-cpu": "alert_host_cpu_pct",
            "set-thresh-host-cpu-hold": "alert_host_cpu_hold_seconds",
            "set-thresh-host-memory": "alert_host_memory_pct",
            "set-thresh-host-disk": "alert_host_disk_pct",
            "toggle-rule-host-cpu-high": "rule_host_cpu_high_enabled",
            "toggle-rule-host-memory-high": "rule_host_memory_high_enabled",
            "toggle-rule-host-disk-high": "rule_host_disk_high_enabled",
        }
        scripts = {
            "app.js": (root / "web" / "static" / "app.js").read_text(encoding="utf-8"),
            "settings.js": (root / "web" / "static" / "js" / "views" / "settings.js").read_text(encoding="utf-8"),
        }
        for element_id, key in field_ids.items():
            self.assertIn('id="%s"' % element_id, settings_html)
            for name, text in scripts.items():
                self.assertIn('{ id: "%s", key: "%s" }' % (element_id, key), text, name)
        for element_id in ("host-health-panel", "host-health-freshness", "host-health-empty", "host-health-tbody"):
            self.assertIn('id="%s"' % element_id, health_html)
        health_js = (root / "web" / "static" / "js" / "views" / "health.js").read_text(encoding="utf-8")
        for name, text in (("app.js", scripts["app.js"]), ("health.js", health_js)):
            self.assertIn('fetchJson("/api/host-health")', text, name)
            self.assertIn("await loadHostHealth();", text, name)

    def test_every_default_threshold_is_a_valid_value_of_its_number_input(self):
        # A default that does not fit min/step makes the browser refuse to submit the whole Settings form
        # ("An invalid form control is not focusable"), with no message: found with step=10 and min=1 (K80).
        import re
        from api.app import DEFAULT_RULE_THRESHOLDS, THRESHOLD_LIMITS

        root = Path(__file__).resolve().parent.parent
        html = (root / "web" / "partials" / "tab_settings.html").read_text(encoding="utf-8")
        js = (root / "web" / "static" / "app.js").read_text(encoding="utf-8")
        fields = dict((key, el) for el, key in re.findall(r'\{ id: "(set-thresh-[a-z-]+)", key: "(alert_[a-z_]+)" \}', js))
        self.assertGreater(len(fields), 10)
        for key, element_id in fields.items():
            tag = re.search(r'<input id="%s"[^>]*>' % element_id, html).group(0)
            minimum = float(re.search(r'min="([\d.]+)"', tag).group(1))
            maximum = float(re.search(r'max="([\d.]+)"', tag).group(1))
            step = float(re.search(r'step="([\d.]+)"', tag).group(1))
            default = float(DEFAULT_RULE_THRESHOLDS[key])
            self.assertTrue(minimum <= default <= maximum, key)
            self.assertAlmostEqual((default - minimum) % step, 0.0, msg=key + " does not fit the step of its input")
            low, high = THRESHOLD_LIMITS[key]
            self.assertTrue(low <= default <= high, key)

    def test_icmp_settings_and_panel_exist_in_both_script_copies(self):
        root = Path(__file__).resolve().parent.parent
        settings_html = (root / "web" / "partials" / "tab_settings.html").read_text(encoding="utf-8")
        health_html = (root / "web" / "partials" / "tab_health.html").read_text(encoding="utf-8")
        field_ids = {
            "set-thresh-icmp-loss": "alert_icmp_loss_pct",
            "set-thresh-icmp-latency": "alert_icmp_latency_ms",
            "toggle-rule-icmp-loss-high": "rule_icmp_loss_high_enabled",
            "toggle-rule-icmp-latency-high": "rule_icmp_latency_high_enabled",
        }
        scripts = {
            "app.js": (root / "web" / "static" / "app.js").read_text(encoding="utf-8"),
            "settings.js": (root / "web" / "static" / "js" / "views" / "settings.js").read_text(encoding="utf-8"),
        }
        for element_id, key in field_ids.items():
            self.assertIn('id="%s"' % element_id, settings_html)
            for name, text in scripts.items():
                self.assertIn('{ id: "%s", key: "%s" }' % (element_id, key), text, name)
        for element_id in ("icmp-health-panel", "icmp-health-freshness", "icmp-health-empty", "icmp-health-tbody"):
            self.assertIn('id="%s"' % element_id, health_html)
        health_js = (root / "web" / "static" / "js" / "views" / "health.js").read_text(encoding="utf-8")
        for name, text in (("app.js", scripts["app.js"]), ("health.js", health_js)):
            self.assertIn('fetchJson("/api/icmp-health")', text, name)
            self.assertIn("await loadIcmpHealth();", text, name)
        index = (root / "web" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("http://", index)
        self.assertNotIn("https://", index)


if __name__ == "__main__":
    unittest.main()
