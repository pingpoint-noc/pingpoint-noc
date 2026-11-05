"""SNMPv3 authPriv credentials: built from the environment, strong protocols always explicit, keys never shown."""

import unittest
import warnings

from collectors.snmp.credentials import (
    SnmpCredentialError,
    build_auth_data,
    check_crypto_available,
    describe_security,
    explain_error,
    load_v3_config,
)

GOOD_ENV = {
    "MININOC_SNMP_V3_USER": "mininoc",
    "MININOC_SNMP_V3_AUTH_KEY": "auth-secret-123",
    "MININOC_SNMP_V3_PRIV_KEY": "priv-secret-456",
}

SHA1_OID = (1, 3, 6, 1, 6, 3, 10, 1, 1, 3)
SHA256_OID = (1, 3, 6, 1, 6, 3, 10, 1, 1, 5)
MD5_OID = (1, 3, 6, 1, 6, 3, 10, 1, 1, 2)
AES128_OID = (1, 3, 6, 1, 6, 3, 10, 1, 2, 4)
DES_OID = (1, 3, 6, 1, 6, 3, 10, 1, 2, 2)


def make_env(**changes):
    env = dict(GOOD_ENV)
    for key, value in changes.items():
        env["MININOC_SNMP_V3_" + key] = value
    return env


class TestLoadConfig(unittest.TestCase):
    def test_without_a_user_v3_is_off(self):
        self.assertIsNone(load_v3_config({}))
        self.assertIsNone(load_v3_config({"MININOC_SNMP_V3_USER": "  "}))

    def test_defaults_are_sha_and_aes(self):
        config = load_v3_config(GOOD_ENV)
        self.assertEqual(config["auth_protocol"], "SHA")
        self.assertEqual(config["priv_protocol"], "AES")

    def test_a_user_without_both_keys_is_refused(self):
        with self.assertRaises(SnmpCredentialError):
            load_v3_config({"MININOC_SNMP_V3_USER": "mininoc"})
        with self.assertRaises(SnmpCredentialError):
            load_v3_config({"MININOC_SNMP_V3_USER": "mininoc", "MININOC_SNMP_V3_AUTH_KEY": "auth-secret-123"})

    def test_short_keys_are_refused(self):
        with self.assertRaises(SnmpCredentialError):
            load_v3_config(make_env(AUTH_KEY="short"))
        with self.assertRaises(SnmpCredentialError):
            load_v3_config(make_env(PRIV_KEY="short"))

    def test_unknown_protocols_are_refused(self):
        with self.assertRaises(SnmpCredentialError):
            load_v3_config(make_env(AUTH_PROTOCOL="SHA999"))
        with self.assertRaises(SnmpCredentialError):
            load_v3_config(make_env(PRIV_PROTOCOL="ROT13"))
        # AES-192/256 use vendor OIDs in PySNMP (Cisco), not the standard ones, and RouterOS only has AES-128.
        with self.assertRaises(SnmpCredentialError):
            load_v3_config(make_env(PRIV_PROTOCOL="AES256"))

    def test_weak_protocols_need_an_explicit_opt_in(self):
        with self.assertRaises(SnmpCredentialError):
            load_v3_config(make_env(PRIV_PROTOCOL="DES"))
        with self.assertRaises(SnmpCredentialError):
            load_v3_config(make_env(AUTH_PROTOCOL="MD5"))
        config = load_v3_config(make_env(PRIV_PROTOCOL="DES", AUTH_PROTOCOL="MD5", ALLOW_WEAK="yes"))
        self.assertEqual(config["priv_protocol"], "DES")
        self.assertEqual(config["auth_protocol"], "MD5")


class TestBuildAuthData(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter("ignore")

    def test_without_v3_it_is_the_v2c_community(self):
        data = build_auth_data("public", None)
        self.assertEqual(type(data).__name__, "CommunityData")
        self.assertEqual(data.communityName, "public")

    def test_v3_uses_sha_and_aes_explicitly(self):
        data = build_auth_data("public", load_v3_config(GOOD_ENV))
        self.assertEqual(type(data).__name__, "UsmUserData")
        self.assertEqual(data.userName, "mininoc")
        self.assertEqual(tuple(data.authProtocol), SHA1_OID)
        self.assertEqual(tuple(data.privProtocol), AES128_OID)

    def test_a_stronger_authentication_protocol_can_be_chosen(self):
        data = build_auth_data("public", load_v3_config(make_env(AUTH_PROTOCOL="SHA256")))
        self.assertEqual(tuple(data.authProtocol), SHA256_OID)
        self.assertEqual(tuple(data.privProtocol), AES128_OID)

    def test_the_keys_never_appear_in_repr_or_description(self):
        config = load_v3_config(GOOD_ENV)
        data = build_auth_data("public", config)
        for text in (repr(data), describe_security(config), repr(config)):
            self.assertNotIn("auth-secret-123", text)
            self.assertNotIn("priv-secret-456", text)

    def test_error_messages_never_contain_the_keys(self):
        try:
            load_v3_config(make_env(AUTH_KEY="short", PRIV_PROTOCOL="ROT13"))
        except SnmpCredentialError as exc:
            self.assertNotIn("short", str(exc).replace("too short", ""))
            self.assertNotIn("priv-secret-456", str(exc))


class TestDescribeSecurity(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(describe_security(None), "SNMPv2c")
        self.assertEqual(describe_security(load_v3_config(GOOD_ENV)), "SNMPv3 authPriv (SHA/AES)")


class TestCryptoPackage(unittest.TestCase):
    """PySNMP 7 does AES/DES through the 'cryptography' package, which it does not install by itself."""

    def test_missing_package_gives_a_clear_error(self):
        from unittest.mock import patch

        with patch("collectors.snmp.credentials.importlib.util.find_spec", return_value=None):
            with self.assertRaises(SnmpCredentialError) as caught:
                check_crypto_available()
        self.assertIn("cryptography", str(caught.exception))
        self.assertIn("requirements.txt", str(caught.exception))

    def test_installed_package_passes(self):
        from unittest.mock import patch

        with patch("collectors.snmp.credentials.importlib.util.find_spec", return_value=object()):
            check_crypto_available()


class TestExplainError(unittest.TestCase):
    """The texts are PySNMP 7's own (pysnmp.proto.errind); the label says what to check, never a key."""

    def test_known_v3_failures_get_a_short_label(self):
        cases = {
            "Wrong SNMP PDU digest": "authentication key",
            "Unknown USM user": "user name",
            "Ciphering services not available or ciphertext is broken": "cryptography",
            "SNMP message timing parameters not in windows of trust": "clock",
            "Unsupported SNMP security level": "security level",
        }
        for text, expected in cases.items():
            self.assertIn(expected, explain_error(text))

    def test_a_v3_timeout_hints_at_the_credentials(self):
        # RouterOS (7.23.5, measured) does not answer a wrong user or key: the only symptom is a timeout.
        text = explain_error("No SNMP response received before timeout", v3=True)
        self.assertIn("No SNMP response", text)
        self.assertIn("user", text)
        self.assertIn("keys", text)

    def test_a_v2c_timeout_is_left_alone(self):
        text = "No SNMP response received before timeout"
        self.assertEqual(explain_error(text, v3=False), text)

    def test_other_errors_pass_through_unchanged(self):
        self.assertEqual(explain_error("No SNMP response received before timeout"), "No SNMP response received before timeout")


class TestSessionUsesTheCredentials(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter("ignore")

    def test_session_is_v2c_unless_v3_is_given(self):
        from collectors.snmp.poller import SnmpSession

        session = SnmpSession("192.168.88.1", 161, "public")
        self.assertEqual(type(session.auth_data()).__name__, "CommunityData")

    def test_session_with_v3_config_uses_usm(self):
        from collectors.snmp.poller import SnmpSession

        session = SnmpSession("192.168.88.1", 161, "public", v3_config=load_v3_config(GOOD_ENV))
        self.assertEqual(type(session.auth_data()).__name__, "UsmUserData")


if __name__ == "__main__":
    unittest.main()
