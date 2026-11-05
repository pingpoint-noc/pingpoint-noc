"""The RouterOS script can also create the SNMPv3 (authPriv) user that the SNMP poller uses.

The keys never go in the HTTP route (they would end up in access logs); only the command line of
`python -m tools.generate_router_config`, reading the same MININOC_SNMP_V3_* variables as the poller.
"""

from pathlib import Path
import tempfile
import unittest

from tools import generate_router_config as gen

ENV = {
    "MININOC_SNMP_V3_USER": "mininoc3",
    "MININOC_SNMP_V3_AUTH_KEY": "auth-secret-123",
    "MININOC_SNMP_V3_PRIV_KEY": "priv-secret-456",
}


class TestV3Block(unittest.TestCase):
    def test_default_script_has_no_v3_and_no_keys(self):
        text = gen.build_rsc_content(noc_ip="192.168.88.4")
        self.assertNotIn("security=private", text)
        self.assertNotIn("authentication-password", text)

    def test_v3_user_is_added_next_to_the_v2c_community(self):
        text = gen.build_rsc_content(noc_ip="192.168.88.4", v3=gen.load_v3_for_router(ENV))
        self.assertIn("/snmp community remove [find name=mininoc3]", text)
        line = [l for l in text.splitlines() if l.startswith("/snmp community add name=mininoc3")][0]
        self.assertIn("security=private", line)
        self.assertIn("authentication-protocol=SHA1", line)
        self.assertIn("encryption-protocol=AES", line)
        self.assertIn('authentication-password="auth-secret-123"', line)
        self.assertIn('encryption-password="priv-secret-456"', line)
        self.assertIn("addresses=192.168.88.4/32", line)
        self.assertIn("read-access=yes", line)
        self.assertIn("write-access=no", line)
        # the v2c community is still there unless it is dropped on purpose
        self.assertIn("/snmp community add name=mininoc read-access=yes", text)

    def test_v2c_can_be_dropped_when_v3_is_enough(self):
        text = gen.build_rsc_content(noc_ip="192.168.88.4", v3=gen.load_v3_for_router(ENV), keep_v2c=False)
        self.assertNotIn("/snmp community add name=mininoc read-access=yes", text)
        self.assertIn("name=mininoc3", text)

    def test_protocol_names_are_translated_to_routeros(self):
        env = dict(ENV, MININOC_SNMP_V3_AUTH_PROTOCOL="MD5", MININOC_SNMP_V3_PRIV_PROTOCOL="DES",
                   MININOC_SNMP_V3_ALLOW_WEAK="yes")
        text = gen.build_rsc_content(noc_ip="192.168.88.4", v3=gen.load_v3_for_router(env))
        self.assertIn("authentication-protocol=MD5", text)
        self.assertIn("encryption-protocol=DES", text)

    def test_protocols_routeros_does_not_offer_are_refused_with_a_clear_message(self):
        for name in ("SHA224", "SHA256", "SHA384", "SHA512"):
            with self.subTest(auth=name):
                with self.assertRaises(ValueError) as caught:
                    gen.load_v3_for_router(dict(ENV, MININOC_SNMP_V3_AUTH_PROTOCOL=name))
                self.assertIn("SHA", str(caught.exception))
                self.assertNotIn("auth-secret-123", str(caught.exception))

    def test_keys_with_characters_that_routeros_would_interpret_are_refused(self):
        for bad in ('pass"word123', "pass$word123", "pass[word123", "pass\\word123", "pass word123"):
            with self.subTest(key=bad):
                with self.assertRaises(ValueError) as caught:
                    gen.load_v3_for_router(dict(ENV, MININOC_SNMP_V3_AUTH_KEY=bad))
                self.assertNotIn(bad, str(caught.exception))

    def test_same_rules_as_the_poller_for_missing_and_short_keys(self):
        with self.assertRaises(ValueError):
            gen.load_v3_for_router({"MININOC_SNMP_V3_USER": "u"})
        with self.assertRaises(ValueError):
            gen.load_v3_for_router(dict(ENV, MININOC_SNMP_V3_PRIV_KEY="short"))

    def test_no_user_means_no_v3(self):
        self.assertIsNone(gen.load_v3_for_router({}))


class TestCliWithV3(unittest.TestCase):
    def test_the_file_with_keys_is_not_written_over_the_tracked_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "v3.rsc"
            gen.main(["10.10.10.5", "--snmp-v3", "--output", str(out)], env=ENV)
            self.assertIn("authentication-password", out.read_text(encoding="utf-8"))
        # without --output the v3 script goes to an ignored path, never to deploy/setup_router.rsc
        self.assertNotEqual(gen.default_output_path(v3=True), gen.DEFAULT_OUTPUT_PATH)
        self.assertTrue(gen.default_output_path(v3=True).startswith("data/"))

    def test_git_ignores_the_script_that_holds_the_keys(self):
        root = Path(__file__).resolve().parents[1]
        ignored = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(gen.DEFAULT_V3_OUTPUT_PATH, ignored)

    def test_the_route_never_takes_keys(self):
        # build_rsc_content is the only thing the HTTP route calls, and it gets no v3 argument there
        from api.routes import bootstrap

        source = Path(bootstrap.__file__).read_text(encoding="utf-8")
        self.assertNotIn("v3=", source)


if __name__ == "__main__":
    unittest.main()
