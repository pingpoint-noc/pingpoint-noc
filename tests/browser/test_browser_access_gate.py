"""Browser test: with MININOC_AUTH_READ=1 the page shows a sign-in (or the first-run screen) before anything else."""

from tests.browser.support import BrowserTestCase, PASSWORD, USERNAME

STRONG = "Correct-Horse-Battery-9"


class FirstRunTest(BrowserTestCase):
    WITH_USER = False
    SERVER_PER_TEST = True
    SERVER_ENV = {"MININOC_AUTH_READ": "1"}

    def test_the_first_visit_creates_the_administrator_and_the_session_survives_a_reload(self):
        requested = []
        context = self.browser.new_context(viewport={"width": 1400, "height": 900})
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.on("request", lambda request: requested.append(request.url))
        page.goto(self.server.base_url + "/")
        page.locator("#access-gate").wait_for()
        self.assertEqual(page.locator("#gate-title").inner_text(), "Welcome to PingPoint NOC")
        self.assertTrue(page.locator("#gate-confirm-wrap").is_visible())
        # nothing of the NOC is asked for before the visitor is in
        self.assertEqual([u for u in requested if "/api/overview" in u or "/api/alerts" in u], [])

        page.fill("#gate-username", "owner")
        page.fill("#gate-password", STRONG)
        page.fill("#gate-confirm", "something else entirely")
        page.click("#gate-submit")
        self.assertIn("not the same", page.locator("#gate-msg").inner_text())
        page.fill("#gate-password", STRONG)
        page.fill("#gate-confirm", STRONG)
        page.click("#gate-submit")
        page.locator("#access-gate").wait_for(state="hidden")
        page.wait_for_function("document.getElementById('kpi-alerts-count').textContent.trim() !== ''")

        page.reload()
        page.wait_for_function("document.getElementById('kpi-alerts-count').textContent.trim() !== ''")
        self.assertTrue(page.locator("#access-gate").is_hidden())


class SignInTest(BrowserTestCase):
    SERVER_ENV = {"MININOC_AUTH_READ": "1"}

    def test_a_wrong_password_is_refused_and_the_right_one_opens_the_page(self):
        page = self.open_page()
        page.locator("#access-gate").wait_for()
        self.assertEqual(page.locator("#gate-title").inner_text(), "Sign in to PingPoint NOC")
        self.assertTrue(page.locator("#gate-confirm-wrap").is_hidden())
        page.fill("#gate-username", USERNAME)
        page.fill("#gate-password", "wrong password")
        page.click("#gate-submit")
        page.wait_for_function("document.getElementById('gate-msg').textContent.includes('wrong')")
        self.assertTrue(page.locator("#access-gate").is_visible())
        page.fill("#gate-password", PASSWORD)
        page.click("#gate-submit")
        page.locator("#access-gate").wait_for(state="hidden")
        page.wait_for_function("document.getElementById('kpi-alerts-count').textContent.trim() !== ''")

    def test_exports_work_with_the_session_cookie_and_signing_out_returns_to_the_gate(self):
        page = self.open_page()
        page.fill("#gate-username", USERNAME)
        page.fill("#gate-password", PASSWORD)
        page.click("#gate-submit")
        page.locator("#access-gate").wait_for(state="hidden")
        # a plain link (no headers) is allowed by the cookie
        status = page.evaluate("fetch('/api/alerts/export?format=csv').then(r => r.status)")
        self.assertEqual(status, 200)
        page.locator("button[data-tab='tab-settings']").click()
        page.locator("#settings-nav-bar [data-settings-section='account']").click()
        page.click("#logout-btn")
        page.locator("#access-gate").wait_for(state="visible")
        status = page.evaluate("fetch('/api/alerts').then(r => r.status)")
        self.assertEqual(status, 401)
