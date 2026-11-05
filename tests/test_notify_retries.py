"""Failed background notifications are retried with growing waits (ROADMAP: retries and queue)."""

import unittest

from alerts.notify import DEFAULT_RETRY_DELAYS, DiscordNotifier, NtfyNotifier, run_with_retries


class FlakyAttempt:
    """Fails `failures` times, then succeeds."""

    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            return False
        return True


class TestRunWithRetries(unittest.TestCase):
    def test_success_on_first_try_never_sleeps(self):
        waits = []
        attempt = FlakyAttempt(0)
        self.assertTrue(run_with_retries(attempt, (5, 30), waits.append, "x"))
        self.assertEqual(attempt.calls, 1)
        self.assertEqual(waits, [])

    def test_retries_with_the_configured_waits_then_succeeds(self):
        waits = []
        attempt = FlakyAttempt(2)
        self.assertTrue(run_with_retries(attempt, (5, 30, 120), waits.append, "x"))
        self.assertEqual(attempt.calls, 3)
        self.assertEqual(waits, [5, 30])

    def test_gives_up_after_the_last_wait(self):
        waits = []
        attempt = FlakyAttempt(99)
        self.assertFalse(run_with_retries(attempt, (5, 30), waits.append, "x"))
        self.assertEqual(attempt.calls, 3)
        self.assertEqual(waits, [5, 30])

    def test_no_delays_means_a_single_attempt(self):
        attempt = FlakyAttempt(99)
        self.assertFalse(run_with_retries(attempt, (), lambda s: None, "x"))
        self.assertEqual(attempt.calls, 1)


class TestNotifierRetries(unittest.TestCase):
    def test_defaults_are_short_enough_to_matter_during_an_outage(self):
        self.assertEqual(DEFAULT_RETRY_DELAYS, (5, 30, 120))

    def test_ntfy_background_send_retries_a_failing_post(self):
        notifier = NtfyNotifier("http://127.0.0.1:9/none")
        waits = []
        notifier.sleep = waits.append
        attempt = FlakyAttempt(1)
        notifier._post = lambda headers, data: attempt()
        self.assertTrue(notifier._post_with_retries({}, b""))
        self.assertEqual(attempt.calls, 2)
        self.assertEqual(waits, [5])

    def test_other_destinations_retry_too(self):
        notifier = DiscordNotifier("https://discord.invalid/hook")
        waits = []
        notifier.sleep = waits.append
        attempt = FlakyAttempt(2)
        notifier._safe_deliver = lambda *args: attempt()
        self.assertTrue(notifier._deliver_with_retries("firing", "d", "r", "t", "warning", "m"))
        self.assertEqual(waits, [5, 30])

    def test_synchronous_mode_does_not_retry(self):
        notifier = NtfyNotifier("http://127.0.0.1:9/none", asynchronous=False)
        waits = []
        notifier.sleep = waits.append
        attempt = FlakyAttempt(99)
        notifier._post = lambda headers, data: attempt()
        self.assertFalse(notifier.send_transition("firing", "d", "r", "t", "critical", "m"))
        self.assertEqual(attempt.calls, 1)
        self.assertEqual(waits, [])


if __name__ == "__main__":
    unittest.main()
