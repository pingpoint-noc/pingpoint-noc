"""Browser test: the Getting started guide in Settings lists the four steps and hides itself when they are all done. Optional (Playwright)."""

import unittest

from .support import BrowserTestCase


def step(step_id, title, state, detail, **extra):
    data = {"id": step_id, "title": title, "state": state, "detail": detail}
    data.update(extra)
    return data


OPEN = {
    "complete": False,
    "steps": [
        step("networks", "Tell the NOC about your network", "attention", "Not set yet: INTERNAL_NETS.", missing=["MININOC_INTERNAL_NETS"]),
        step("devices", "Review the devices found", "attention", "1 of 2 devices have no role yet.", total=2,
             to_review=[{"id": "host-a", "name": "host-a", "ip": "192.0.2.7"}]),
        step("learning", "Let it learn", "waiting", "Observed for 3 of 24 hours.", observed_hours=3, learning_hours=24, proposals=[]),
        step("notifications", "Choose where alerts are sent", "todo", "No destination is set.", destinations=[], tested_utc=None),
    ],
}


class SetupGuideTest(BrowserTestCase):
    def open_guide(self, payload):
        page = self.open_page()
        page.wait_for_function("typeof switchTab === 'function'")
        page.route("**/api/setup/status", lambda route: route.fulfill(json=payload))
        page.evaluate("() => { switchTab('tab-settings'); }")
        page.click("[data-settings-section='inventory']")
        return page

    def test_the_four_steps_are_listed_with_their_state(self):
        page = self.open_guide(OPEN)
        page.wait_for_function("document.querySelectorAll('#setup-guide-steps li').length === 4")
        text = page.inner_text("#setup-guide-steps")
        self.assertIn("Without a role: host-a (192.0.2.7)", text)
        self.assertIn("Waiting", text)
        self.assertIn("Not started", text)
        self.assertTrue(page.is_visible("#setup-guide"))

    def test_the_blurb_and_the_steps_do_not_touch_each_other(self):
        page = self.open_guide(OPEN)
        page.wait_for_function("document.querySelectorAll('#setup-guide-steps li').length === 4")
        gaps = page.evaluate("""() => {
          const top = e => e.getBoundingClientRect().top, bottom = e => e.getBoundingClientRect().bottom;
          const blurb = document.querySelector('#setup-guide > .dest-blurb');
          const steps = [...document.querySelectorAll('#setup-guide-steps > li')];
          const out = [top(steps[0]) - bottom(blurb)];
          for (let i = 1; i < steps.length; i += 1) out.push(top(steps[i]) - bottom(steps[i - 1]));
          return out; }""")
        for gap in gaps:
            self.assertGreaterEqual(gap, 8)
        # inside a step the text and the buttons are apart too
        inner = page.evaluate("""() => { const li = document.querySelector('#setup-guide-steps > li');
          const head = li.querySelector('.setup-step-head'), next = head.nextElementSibling;
          return next.getBoundingClientRect().top - head.getBoundingClientRect().bottom; }""")
        self.assertGreaterEqual(inner, 4)

    def test_the_guide_is_hidden_when_everything_is_done(self):
        done = {"complete": True, "steps": [dict(s, state="done") for s in OPEN["steps"]]}
        page = self.open_guide(done)
        page.wait_for_function("document.getElementById('setup-guide') !== null")
        page.wait_for_timeout(300)
        self.assertFalse(page.is_visible("#setup-guide"))


if __name__ == "__main__":
    unittest.main()
