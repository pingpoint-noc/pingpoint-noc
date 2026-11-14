"""Rule interface_speed_drop (ROADMAP D2): a link that renegotiated to a lower speed while staying up."""

import copy

from alerts import engine
from tests.test_flapping import DEVICE, FlappingBase, IF_INDEX

GIGABIT = 1_000_000_000
FAST = 100_000_000
RULE = "interface_speed_drop"


class SpeedBase(FlappingBase):
    def poll_speed(self, speed, oper=None, seconds=15):
        self.ts += seconds
        self.clock_ticks += seconds * 100
        data = copy.deepcopy(self.base)
        data[IF_INDEX]["speed_bps"] = speed
        if oper is not None:
            data[IF_INDEX]["oper"] = oper
        self.repo.record_snmp_poll(DEVICE, self.ts, self.clock_ticks, data)

    def evaluate(self, **kwargs):
        engine.evaluate_interface_rules(self.repo, DEVICE, self.ts, 10_000_000, 120, **kwargs)

    def alert(self):
        return self.repo.get_open_alert(DEVICE, RULE, "if:%d" % IF_INDEX)

    def interface(self):
        return [row for row in self.repo.get_device_interfaces(DEVICE) if row["if_index"] == IF_INDEX][0]


class TestRecording(SpeedBase):
    def test_a_drop_while_up_remembers_the_old_speed_and_when(self):
        self.poll_speed(GIGABIT)
        self.poll_speed(FAST)
        row = self.interface()
        self.assertEqual((row["speed_bps"], row["speed_prev_bps"], row["speed_changed_utc"]), (FAST, GIGABIT, self.ts))

    def test_nothing_is_remembered_without_a_change(self):
        self.poll_speed(GIGABIT)
        self.poll_speed(GIGABIT)
        row = self.interface()
        self.assertIsNone(row["speed_prev_bps"])

    def test_a_port_that_is_down_is_not_a_renegotiation(self):
        self.poll_speed(GIGABIT)
        self.poll_speed(0, oper="down")
        self.poll_speed(GIGABIT)
        self.assertIsNone(self.interface()["speed_prev_bps"])

    def test_a_zero_speed_is_never_a_previous_speed(self):
        self.poll_speed(0)
        self.poll_speed(GIGABIT)
        self.assertIsNone(self.interface()["speed_prev_bps"])

    def test_the_first_poll_records_nothing(self):
        self.poll_speed(FAST)
        self.assertIsNone(self.interface()["speed_prev_bps"])


class TestRule(SpeedBase):
    def test_a_drop_fires_one_warning_that_names_both_speeds(self):
        self.poll_speed(GIGABIT)
        self.poll_speed(FAST)
        self.evaluate()
        alert = self.alert()
        self.assertIsNotNone(alert)
        self.assertEqual(alert["severity"], "warning")
        self.assertIn("1 Gbit/s", alert["message"])
        self.assertIn("100 Mbit/s", alert["message"])

    def test_an_increase_does_not_fire(self):
        self.poll_speed(FAST)
        self.poll_speed(GIGABIT)
        self.evaluate()
        self.assertIsNone(self.alert())

    def test_it_closes_when_the_speed_returns(self):
        self.poll_speed(GIGABIT)
        self.poll_speed(FAST)
        self.evaluate()
        self.assertIsNotNone(self.alert())
        self.poll_speed(GIGABIT)
        self.evaluate()
        self.assertIsNone(self.alert())

    def test_it_closes_after_a_day(self):
        self.poll_speed(GIGABIT)
        self.poll_speed(FAST)
        self.evaluate()
        self.ts += engine.SPEED_DROP_HOLD_SECONDS + 60
        self.poll_speed(FAST)  # still polled, so the interface is seen
        self.evaluate()
        self.assertIsNone(self.alert())

    def test_the_toggle_turns_it_off(self):
        self.poll_speed(GIGABIT)
        self.poll_speed(FAST)
        self.evaluate(rule_toggles={"rule_interface_speed_drop_enabled": False})
        self.assertIsNone(self.alert())

    def test_the_format_of_a_speed(self):
        self.assertEqual(engine.format_link_speed(GIGABIT), "1 Gbit/s")
        self.assertEqual(engine.format_link_speed(2_500_000_000), "2.5 Gbit/s")
        self.assertEqual(engine.format_link_speed(FAST), "100 Mbit/s")
        self.assertEqual(engine.format_link_speed(10_000), "10 kbit/s")
