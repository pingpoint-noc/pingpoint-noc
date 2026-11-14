"""scenario: the lab-specific parameters live in one module and keep the documented default values by default."""

import unittest
from unittest.mock import patch

import scenario
from api.routes import bootstrap
from collectors.ipfix import ingestor as ipfix_ingestor


D = scenario.DEFAULTS  # the tests say "the default", never the lab's literal values (tests/conftest.py sets those in the environment)


class TestDefaults(unittest.TestCase):
    def test_no_environment_gives_the_default_values(self):
        loaded = scenario.load({})
        self.assertEqual(loaded.router_ip, D["MININOC_ROUTER_IP"])
        self.assertEqual(loaded.device_id, D["MININOC_DEVICE_ID"])
        self.assertEqual(loaded.device_name, D["MININOC_DEVICE_NAME"])
        self.assertEqual(loaded.internal_nets, D["MININOC_INTERNAL_NETS"])
        self.assertEqual(loaded.router_own_ips, D["MININOC_ROUTER_OWN_IPS"])
        self.assertEqual(loaded.noc_ip, D["MININOC_NOC_IP"])
        self.assertEqual(loaded.tz, D["MININOC_TZ"])

    def test_the_variables_override_and_an_empty_one_means_the_default(self):
        loaded = scenario.load({"MININOC_ROUTER_IP": "10.0.0.1", "MININOC_NOC_IP": "  ", "MININOC_INTERNAL_NETS": "10.0.0.0/8"})
        self.assertEqual(loaded.router_ip, "10.0.0.1")
        self.assertEqual(loaded.internal_nets, "10.0.0.0/8")
        self.assertEqual(loaded.noc_ip, D["MININOC_NOC_IP"])

    def test_sources_tell_apart_the_variable_from_the_default(self):
        sources = scenario.sources({"MININOC_ROUTER_IP": "10.0.0.1"})
        self.assertEqual(sources["MININOC_ROUTER_IP"], "env")
        self.assertEqual(sources["MININOC_NOC_IP"], "default")

    def test_every_default_is_documented_with_a_description(self):
        self.assertEqual(set(scenario.DEFAULTS), set(scenario.DESCRIPTIONS))


class TestTimeZone(unittest.TestCase):
    def test_a_valid_zone_is_used(self):
        self.assertEqual(scenario.load({"MININOC_TZ": "Asia/Kolkata"}).tz, "Asia/Kolkata")
        self.assertEqual(scenario.tz_name({"MININOC_TZ": "Australia/Lord_Howe"}), "Australia/Lord_Howe")
        self.assertEqual(scenario.tz_name({"MININOC_TZ": "UTC"}), "UTC")

    def test_empty_or_unset_is_the_default(self):
        self.assertEqual(scenario.tz_name({}), D["MININOC_TZ"])
        self.assertEqual(scenario.tz_name({"MININOC_TZ": "  "}), D["MININOC_TZ"])

    def test_an_invalid_zone_is_reported_and_falls_back_to_the_default(self):
        self.assertFalse(scenario.is_valid_tz("Mars/Olympus_Mons"))
        self.assertFalse(scenario.is_valid_tz("../etc/passwd"))
        self.assertTrue(scenario.is_valid_tz("America/New_York"))
        scenario._reported_invalid.clear()
        with self.assertLogs("mini_noc.scenario", level="ERROR") as logs:
            self.assertEqual(scenario.tz_name({"MININOC_TZ": "Mars/Olympus_Mons"}), D["MININOC_TZ"])
        self.assertIn("Mars/Olympus_Mons", logs.output[0])
        # Reported once, not on every engine cycle.
        with self.assertNoLogs("mini_noc.scenario", level="ERROR"):
            scenario.tz_name({"MININOC_TZ": "Mars/Olympus_Mons"})


class TestConsumers(unittest.TestCase):
    def test_the_ipfix_defaults_are_the_scenario_ones(self):
        self.assertEqual(ipfix_ingestor.DEFAULT_INTERNAL_NETS, scenario.value("MININOC_INTERNAL_NETS"))
        self.assertEqual(ipfix_ingestor.DEFAULT_ROUTER_OWN_IPS, scenario.value("MININOC_ROUTER_OWN_IPS"))

    def test_the_bootstrap_fallback_is_the_collector_of_the_scenario(self):
        with patch.dict("os.environ", {"MININOC_NOC_IP": ""}):
            self.assertEqual(bootstrap.default_noc_ip(), D["MININOC_NOC_IP"])
        with patch.dict("os.environ", {"MININOC_NOC_IP": "10.9.9.9"}):
            self.assertEqual(bootstrap.default_noc_ip(), "10.9.9.9")


if __name__ == "__main__":
    unittest.main()
