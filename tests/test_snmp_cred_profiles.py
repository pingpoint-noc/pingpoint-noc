"""SNMP credential profiles (K83): named in the inventory, defined in the environment, never in the database."""

import unittest
from unittest.mock import patch

from collectors.snmp.credentials import (
    SnmpCredentialError,
    V3Config,
    load_profile,
    load_v3_config,
    profile_env_prefix,
)

V3_PROFILE_ENV = {
    "MININOC_SNMP_CRED_CORE_V3_USER": "core-user",
    "MININOC_SNMP_CRED_CORE_V3_AUTH_KEY": "core-auth-key-111",
    "MININOC_SNMP_CRED_CORE_V3_PRIV_KEY": "core-priv-key-222",
}


class TestProfileNames(unittest.TestCase):
    def test_the_variable_name_is_upper_case_with_underscores_for_hyphens(self):
        self.assertEqual(profile_env_prefix("core-v3"), "MININOC_SNMP_CRED_CORE_V3_")
        self.assertEqual(profile_env_prefix("LabV2"), "MININOC_SNMP_CRED_LABV2_")
        self.assertEqual(profile_env_prefix("edge_1"), "MININOC_SNMP_CRED_EDGE_1_")

    def test_names_with_other_characters_are_rejected(self):
        for bad in ("", "  ", "a b", "a.b", "a=b", "ñ", "a/b"):
            with self.assertRaises(SnmpCredentialError):
                profile_env_prefix(bad)


class TestLoadProfile(unittest.TestCase):
    def test_a_community_profile_is_v2c(self):
        profile = load_profile({"MININOC_SNMP_CRED_LABV2_COMMUNITY": "lab-comm"}, "labv2")
        self.assertEqual(profile.community, "lab-comm")
        self.assertIsNone(profile.v3_config)

    def test_a_v3_profile_reuses_the_k35_rules(self):
        with patch("collectors.snmp.credentials.importlib.util.find_spec", return_value=object()):
            profile = load_profile(V3_PROFILE_ENV, "core-v3")
        self.assertIsNone(profile.community)
        self.assertEqual(profile.v3_config["user"], "core-user")
        self.assertEqual(profile.v3_config["auth_protocol"], "SHA")
        self.assertEqual(profile.v3_config["priv_protocol"], "AES")

    def test_the_v3_profile_is_the_same_object_type_as_the_service_wide_one(self):
        with patch("collectors.snmp.credentials.importlib.util.find_spec", return_value=object()):
            profile = load_profile(V3_PROFILE_ENV, "core-v3")
        self.assertIsInstance(profile.v3_config, V3Config)
        self.assertNotIn("core-auth-key-111", repr(profile.v3_config))

    def test_an_unknown_profile_names_the_variables_to_define(self):
        with self.assertRaises(SnmpCredentialError) as caught:
            load_profile({}, "nope")
        text = str(caught.exception)
        self.assertIn("nope", text)
        self.assertIn("MININOC_SNMP_CRED_NOPE_COMMUNITY", text)
        self.assertIn("MININOC_SNMP_CRED_NOPE_USER", text)

    def test_community_and_user_together_are_ambiguous(self):
        env = dict(V3_PROFILE_ENV)
        env["MININOC_SNMP_CRED_CORE_V3_COMMUNITY"] = "x-community"
        with self.assertRaises(SnmpCredentialError) as caught:
            load_profile(env, "core-v3")
        self.assertNotIn("x-community", str(caught.exception))

    def test_short_keys_missing_keys_and_weak_protocols_fail_with_the_profile_variable_names(self):
        cases = (
            ({"MININOC_SNMP_CRED_P_USER": "u", "MININOC_SNMP_CRED_P_AUTH_KEY": "short",
              "MININOC_SNMP_CRED_P_PRIV_KEY": "long-enough-key"}, "too short"),
            ({"MININOC_SNMP_CRED_P_USER": "u"}, "MININOC_SNMP_CRED_P_AUTH_KEY"),
            ({"MININOC_SNMP_CRED_P_USER": "u", "MININOC_SNMP_CRED_P_AUTH_KEY": "long-enough-key",
              "MININOC_SNMP_CRED_P_PRIV_KEY": "long-enough-key", "MININOC_SNMP_CRED_P_AUTH_PROTOCOL": "MD5"},
             "MININOC_SNMP_CRED_P_ALLOW_WEAK"),
            ({"MININOC_SNMP_CRED_P_USER": "u", "MININOC_SNMP_CRED_P_AUTH_KEY": "long-enough-key",
              "MININOC_SNMP_CRED_P_PRIV_KEY": "long-enough-key", "MININOC_SNMP_CRED_P_PRIV_PROTOCOL": "AES256"},
             "unknown SNMPv3 encryption protocol"),
        )
        for env, expected in cases:
            with patch("collectors.snmp.credentials.importlib.util.find_spec", return_value=object()):
                with self.assertRaises(SnmpCredentialError) as caught:
                    load_profile(env, "p")
            self.assertIn(expected, str(caught.exception))
            self.assertNotIn("MININOC_SNMP_V3_", str(caught.exception))

    def test_weak_protocols_are_accepted_with_the_profile_allow_weak(self):
        env = {
            "MININOC_SNMP_CRED_OLD_USER": "u",
            "MININOC_SNMP_CRED_OLD_AUTH_KEY": "long-enough-key",
            "MININOC_SNMP_CRED_OLD_PRIV_KEY": "long-enough-key",
            "MININOC_SNMP_CRED_OLD_AUTH_PROTOCOL": "MD5",
            "MININOC_SNMP_CRED_OLD_PRIV_PROTOCOL": "DES",
            "MININOC_SNMP_CRED_OLD_ALLOW_WEAK": "yes",
        }
        with patch("collectors.snmp.credentials.importlib.util.find_spec", return_value=object()):
            profile = load_profile(env, "old")
        self.assertEqual(profile.v3_config["auth_protocol"], "MD5")

    def test_a_v3_profile_without_the_cryptography_package_fails(self):
        with patch("collectors.snmp.credentials.importlib.util.find_spec", return_value=None):
            with self.assertRaises(SnmpCredentialError) as caught:
                load_profile(V3_PROFILE_ENV, "core-v3")
        self.assertIn("cryptography", str(caught.exception))

    def test_no_error_contains_a_key(self):
        env = dict(V3_PROFILE_ENV)
        env["MININOC_SNMP_CRED_CORE_V3_AUTH_PROTOCOL"] = "BOGUS"
        with patch("collectors.snmp.credentials.importlib.util.find_spec", return_value=object()):
            with self.assertRaises(SnmpCredentialError) as caught:
                load_profile(env, "core-v3")
        for secret in ("core-auth-key-111", "core-priv-key-222"):
            self.assertNotIn(secret, str(caught.exception))


class TestServiceWidePrefixIsUntouched(unittest.TestCase):
    def test_the_default_prefix_still_reads_mininoc_snmp_v3(self):
        env = {
            "MININOC_SNMP_V3_USER": "mik",
            "MININOC_SNMP_V3_AUTH_KEY": "auth-secret-123",
            "MININOC_SNMP_V3_PRIV_KEY": "priv-secret-456",
        }
        self.assertEqual(load_v3_config(env)["user"], "mik")
        # The profile variables do not leak into the service-wide settings.
        self.assertIsNone(load_v3_config(V3_PROFILE_ENV))


if __name__ == "__main__":
    unittest.main()
