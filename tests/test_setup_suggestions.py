"""First-run suggestions: parsing what the machine says about its own networks; no real network needed."""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api import setup
from api.app import create_app
from storage import create_repository

IP_OUTPUT = """\
2: eth0    inet 192.0.2.40/24 brd 192.0.2.255 scope global dynamic eth0\\       valid_lft 86000sec preferred_lft 86000sec
3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\       valid_lft forever preferred_lft forever
4: br-1a2b3c4d5e6f    inet 172.18.0.1/16 brd 172.18.255.255 scope global br-1a2b3c4d5e6f\\       valid_lft forever preferred_lft forever
5: wlan0    inet 198.51.100.8/25 brd 198.51.100.127 scope global wlan0\\       valid_lft forever preferred_lft forever
6: veth12ab@if7    inet 10.99.0.2/24 scope global veth12ab\\       valid_lft forever preferred_lft forever
"""


class ParseTest(unittest.TestCase):
    def test_each_global_address_gets_its_network_and_plumbing_is_flagged(self):
        found = {a["interface"]: a for a in setup.parse_ip_addr(IP_OUTPUT)}
        self.assertEqual(found["eth0"]["network"], "192.0.2.0/24")
        self.assertEqual(found["wlan0"]["network"], "198.51.100.0/25")  # the mask is kept, not assumed /24
        for name in ("docker0", "br-1a2b3c4d5e6f", "veth12ab"):
            self.assertTrue(found[name]["plumbing"], name)
        self.assertFalse(found["eth0"]["plumbing"])

    def test_junk_and_empty_input_give_nothing(self):
        self.assertEqual(setup.parse_ip_addr(""), [])
        self.assertEqual(setup.parse_ip_addr(None), [])
        self.assertEqual(setup.parse_ip_addr("not an ip output\n2: eth0 inet6 fe80::1/64"), [])


class SuggestTest(unittest.TestCase):
    def test_the_networks_of_real_interfaces_become_the_internal_networks(self):
        result = setup.suggest({}, ip_output=IP_OUTPUT, primary="192.0.2.40", time_zone="Europe/Paris")
        self.assertEqual(result["suggestions"]["MININOC_INTERNAL_NETS"], "192.0.2.0/24,198.51.100.0/25")
        self.assertEqual(result["suggestions"]["MININOC_NOC_IP"], "192.0.2.40")
        self.assertEqual(result["suggestions"]["MININOC_TZ"], "Europe/Paris")
        self.assertEqual(sorted(result["ignored_interfaces"]), ["br-1a2b3c4d5e6f", "docker0", "veth12ab"])

    def test_without_the_ip_tool_the_route_address_gives_a_slash_24(self):
        result = setup.suggest({}, ip_output="", primary="203.0.113.77", time_zone="")
        self.assertEqual(result["suggestions"]["MININOC_INTERNAL_NETS"], "203.0.113.0/24")
        self.assertEqual(result["suggestions"]["MININOC_NOC_IP"], "203.0.113.77")

    def test_what_is_already_configured_is_reported(self):
        result = setup.suggest({"MININOC_NOC_IP": "192.0.2.9"}, ip_output="", primary="", time_zone="")
        self.assertTrue(result["configured"]["MININOC_NOC_IP"])
        self.assertFalse(result["configured"]["MININOC_ROUTER_IP"])

    def test_nothing_known_gives_empty_suggestions_not_the_lab(self):
        result = setup.suggest({}, ip_output="", primary="", time_zone="")
        self.assertEqual(result["suggestions"], {"MININOC_INTERNAL_NETS": "", "MININOC_NOC_IP": "", "MININOC_TZ": ""})


class EndpointTest(unittest.TestCase):
    def test_the_endpoint_answers_with_suggestions(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "t.db"
            create_repository("sqlite", db_path).init_storage()
            client = TestClient(create_app(db_path))
            response = client.get("/api/setup/suggestions")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(sorted(body["suggestions"]), ["MININOC_INTERNAL_NETS", "MININOC_NOC_IP", "MININOC_TZ"])
            self.assertIn("configured", body)
            client.close()


if __name__ == "__main__":
    unittest.main()
