"""Shared helpers for the browser tests: a real API server on a free port and a Playwright browser.

The tests are optional: when Playwright or its Chromium build is missing, `BrowserTestCase`
skips the whole class, so the normal suite stays green without them.
Install with `pip install playwright` and `python -m playwright install chromium`.
"""

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
    PlaywrightError = Exception

REPO_ROOT = Path(__file__).resolve().parents[2]
USERNAME = "browser-tester"
PASSWORD = "correct horse battery staple"
STARTUP_TIMEOUT = 30


def free_port():
    """Ask the OS for a free TCP port on the loopback interface."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def clean_env():
    """Environment for child processes: no admin token or password inherited from the caller."""
    env = dict(os.environ)
    for name in ("MININOC_ADMIN_TOKEN", "MININOC_USER_PASSWORD", "MININOC_DB_PATH"):
        env.pop(name, None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_tool(args, extra_env=None):
    """Run `python -m <tool> ...` in the repo and fail loudly with its output."""
    env = clean_env()
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [sys.executable, "-m"] + args,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError("tool failed: " + " ".join(args) + "\n" + result.stdout + result.stderr)


class ApiServer:
    """A throw-away `python -m api` process over a seeded temporary database."""

    def __init__(self, with_user):
        self.with_user = with_user
        self.tmp = Path(tempfile.mkdtemp(prefix="mininoc-browser-"))
        self.db_path = self.tmp / "demo.db"
        self.port = free_port()
        self.base_url = "http://127.0.0.1:" + str(self.port)
        self.process = None
        self.log_file = None

    def start(self):
        run_tool(["tools.seed_demo", "--db-path", str(self.db_path)])
        if self.with_user:
            # The password goes through the environment, never through argv.
            run_tool(
                ["tools.manage_users", "--db-path", str(self.db_path), "add", USERNAME],
                {"MININOC_USER_PASSWORD": PASSWORD},
            )
        self.log_file = open(str(self.tmp / "api.log"), "wb")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "api", "--db-path", str(self.db_path),
             "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=str(REPO_ROOT),
            env=clean_env(),
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
        )
        self.wait_until_ready()

    def wait_until_ready(self):
        deadline = time.time() + STARTUP_TIMEOUT
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("API server exited early:\n" + self.read_log())
            try:
                with urllib.request.urlopen(self.base_url + "/api/health", timeout=2) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.2)
        raise RuntimeError("API server did not answer in time:\n" + self.read_log())

    def read_log(self):
        self.log_file.flush()
        return (self.tmp / "api.log").read_text(encoding="utf-8", errors="replace")

    def stop(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.log_file is not None:
            self.log_file.close()
        shutil.rmtree(str(self.tmp), ignore_errors=True)


class BrowserTestCase(unittest.TestCase):
    """Starts one server and one Chromium per class; skips the class if they are unavailable."""

    WITH_USER = True
    # True: a new server and database for every test (for tests that change the stored data).
    SERVER_PER_TEST = False

    server = None
    playwright = None
    browser = None

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise unittest.SkipTest("playwright is not installed (optional: pip install playwright)")
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except PlaywrightError as err:
            cls.playwright.stop()
            cls.playwright = None
            raise unittest.SkipTest("Chromium is not installed: " + str(err).splitlines()[0])
        if not cls.SERVER_PER_TEST:
            cls.server = ApiServer(cls.WITH_USER)
            try:
                cls.server.start()
            except Exception:
                cls.tearDownClass()
                raise

    @classmethod
    def tearDownClass(cls):
        if cls.browser is not None:
            cls.browser.close()
            cls.browser = None
        if cls.playwright is not None:
            cls.playwright.stop()
            cls.playwright = None
        if cls.server is not None:
            cls.server.stop()
            cls.server = None

    def setUp(self):
        if self.SERVER_PER_TEST:
            self.server = ApiServer(self.WITH_USER)
            self.addCleanup(self.server.stop)
            self.server.start()

    def open_page(self, color_scheme="dark", path="/"):
        """A fresh context (empty storage) on the dashboard; closed automatically after the test."""
        context = self.browser.new_context(color_scheme=color_scheme, viewport={"width": 1400, "height": 900})
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.goto(self.server.base_url + path)
        return page

    def sign_in(self, page, password=PASSWORD):
        page.locator("button[data-tab='tab-settings']").click()
        # Settings opens on the alert rules since K117; the sign-in box is behind the "Account" section.
        page.locator("#settings-nav-bar [data-settings-section='account']").click()
        page.fill("#login-username", USERNAME)
        page.fill("#login-password", password)
        page.click("#login-submit")
