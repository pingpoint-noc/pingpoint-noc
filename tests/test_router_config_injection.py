import unittest

from tools.generate_router_config import build_rsc_content


class RouterConfigInputTests(unittest.TestCase):
    def test_newline_in_community_is_rejected(self):
        with self.assertRaises(ValueError):
            build_rsc_content(noc_ip="10.0.0.5", community="x\n/system reset-configuration")

    def test_newline_or_space_in_noc_ip_is_rejected(self):
        for bad in ("10.0.0.5\n/user add", "10.0.0.5 foo", "10.0.0.5;ls"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                build_rsc_content(noc_ip=bad, community="mininoc")

    def test_valid_values_still_work(self):
        text = build_rsc_content(noc_ip="collector.example.org", community="mini-noc_1")
        self.assertIn("remote=collector.example.org", text)


if __name__ == "__main__":
    unittest.main()
