"""SNMP credentials: v2c community or SNMPv3 authPriv, read from the environment.

PySNMP 7 falls back to MD5 and DES when a key is given without a protocol, so the protocols are
always set explicitly here. The keys live only in the environment: they are masked in repr and
never put in an error message or in the health page.
"""

import importlib.util
import re

SERVICE_PREFIX = "MININOC_SNMP_V3_"
PROFILE_PREFIX = "MININOC_SNMP_CRED_"
PROFILE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

WEAK_AUTH = ("MD5",)
WEAK_PRIV = ("DES",)
MIN_KEY_LENGTH = 8

AUTH_NAMES = ("SHA", "SHA224", "SHA256", "SHA384", "SHA512", "MD5")
# AES-192/256 are left out on purpose: PySNMP maps them to vendor (Cisco) OIDs, not to a standard,
# and RouterOS only offers AES-128.
PRIV_NAMES = ("AES", "DES")


class SnmpCredentialError(ValueError):
    """The SNMPv3 settings are missing, too weak or unknown. The message never contains a key."""


class V3Config(dict):
    """The SNMPv3 settings. Behaves like a dict, but its repr hides both keys."""

    def __repr__(self):
        return "V3Config(user=%r, auth=%s, priv=%s, keys=<hidden>)" % (
            self.get("user"),
            self.get("auth_protocol"),
            self.get("priv_protocol"),
        )


def _read(env, name, prefix=SERVICE_PREFIX):
    value = env.get(prefix + name)
    if value is None:
        return ""
    return str(value).strip()


def load_v3_config(env, prefix=SERVICE_PREFIX):
    """Return the SNMPv3 settings, or None when no user is configured (v2c stays in use).

    `prefix` is the start of the variable names: MININOC_SNMP_V3_ for the service-wide settings (K35) and
    MININOC_SNMP_CRED_<NAME>_ for a per-device profile (K83). The rules are the same.
    """
    user = _read(env, "USER", prefix)
    if user == "":
        return None

    auth_key = _read(env, "AUTH_KEY", prefix)
    priv_key = _read(env, "PRIV_KEY", prefix)
    if auth_key == "" or priv_key == "":
        raise SnmpCredentialError("SNMPv3 authPriv needs both %sAUTH_KEY and %sPRIV_KEY" % (prefix, prefix))
    if len(auth_key) < MIN_KEY_LENGTH:
        raise SnmpCredentialError("the SNMPv3 authentication key is too short (minimum %d characters)" % MIN_KEY_LENGTH)
    if len(priv_key) < MIN_KEY_LENGTH:
        raise SnmpCredentialError("the SNMPv3 encryption key is too short (minimum %d characters)" % MIN_KEY_LENGTH)

    auth_protocol = _read(env, "AUTH_PROTOCOL", prefix).upper()
    if auth_protocol == "":
        auth_protocol = "SHA"
    priv_protocol = _read(env, "PRIV_PROTOCOL", prefix).upper()
    if priv_protocol == "":
        priv_protocol = "AES"
    if auth_protocol not in AUTH_NAMES:
        raise SnmpCredentialError("unknown SNMPv3 authentication protocol (use one of: %s)" % ", ".join(AUTH_NAMES))
    if priv_protocol not in PRIV_NAMES:
        raise SnmpCredentialError("unknown SNMPv3 encryption protocol (use one of: %s)" % ", ".join(PRIV_NAMES))

    allow_weak = _read(env, "ALLOW_WEAK", prefix).lower() in ("1", "yes", "true")
    if auth_protocol in WEAK_AUTH and not allow_weak:
        raise SnmpCredentialError("MD5 authentication is weak: set %sALLOW_WEAK=yes to accept it" % prefix)
    if priv_protocol in WEAK_PRIV and not allow_weak:
        raise SnmpCredentialError("DES encryption is weak: set %sALLOW_WEAK=yes to accept it" % prefix)

    return V3Config(
        user=user,
        auth_key=auth_key,
        priv_key=priv_key,
        auth_protocol=auth_protocol,
        priv_protocol=priv_protocol,
    )


def check_crypto_available():
    """SNMPv3 privacy needs the 'cryptography' package; without it PySNMP only says «ciphering not available»."""
    if importlib.util.find_spec("cryptography") is None:
        raise SnmpCredentialError(
            "SNMPv3 encryption needs the 'cryptography' package: run pip install -r requirements.txt"
        )


class CredentialProfile:
    """What a named profile resolves to: a v2c community or SNMPv3 settings (exactly one of them)."""

    def __init__(self, community=None, v3_config=None):
        self.community = community
        self.v3_config = v3_config

    def __repr__(self):
        if self.v3_config is not None:
            return "CredentialProfile(%r)" % (self.v3_config,)
        return "CredentialProfile(community=<hidden>)"


def profile_env_prefix(name):
    """Start of the variables of a profile: the name in upper case, with '_' where the inventory name has '-'.

    core-v3 -> MININOC_SNMP_CRED_CORE_V3_. Names are letters, digits, '-' and '_'; core-v3 and core_v3 are the same
    profile for the environment, so an inventory should not use both.
    """
    clean = str(name).strip()
    if clean == "" or not PROFILE_NAME.match(clean):
        raise SnmpCredentialError("a credential profile name uses letters, digits, '-' and '_' only")
    return PROFILE_PREFIX + clean.upper().replace("-", "_") + "_"


def load_profile(env, name):
    """Resolve a profile name from the environment. Raises SnmpCredentialError (never with a key in the text)."""
    prefix = profile_env_prefix(name)
    community = _read(env, "COMMUNITY", prefix)
    has_user = _read(env, "USER", prefix) != ""
    if community != "" and has_user:
        raise SnmpCredentialError(
            "profile '%s' defines both %sCOMMUNITY and %sUSER: use only one of them" % (name, prefix, prefix)
        )
    if community != "":
        return CredentialProfile(community=community)
    if not has_user:
        raise SnmpCredentialError(
            "unknown credential profile '%s': define %sCOMMUNITY (SNMPv2c) or %sUSER with its keys (SNMPv3)"
            % (name, prefix, prefix)
        )
    try:
        v3_config = load_v3_config(env, prefix)
        check_crypto_available()
    except SnmpCredentialError as exc:
        raise SnmpCredentialError("profile '%s': %s" % (name, exc))
    return CredentialProfile(v3_config=v3_config)


def build_auth_data(community, v3_config):
    """Return the PySNMP credentials: UsmUserData for v3, CommunityData (v2c) otherwise."""
    from pysnmp.hlapi.v3arch.asyncio import (
        USM_AUTH_HMAC96_MD5,
        USM_AUTH_HMAC96_SHA,
        USM_AUTH_HMAC128_SHA224,
        USM_AUTH_HMAC192_SHA256,
        USM_AUTH_HMAC256_SHA384,
        USM_AUTH_HMAC384_SHA512,
        USM_PRIV_CBC56_DES,
        USM_PRIV_CFB128_AES,
        CommunityData,
        UsmUserData,
    )

    if v3_config is None:
        return CommunityData(community, mpModel=1)

    auth_oids = {
        "SHA": USM_AUTH_HMAC96_SHA,
        "SHA224": USM_AUTH_HMAC128_SHA224,
        "SHA256": USM_AUTH_HMAC192_SHA256,
        "SHA384": USM_AUTH_HMAC256_SHA384,
        "SHA512": USM_AUTH_HMAC384_SHA512,
        "MD5": USM_AUTH_HMAC96_MD5,
    }
    priv_oids = {
        "AES": USM_PRIV_CFB128_AES,
        "DES": USM_PRIV_CBC56_DES,
    }
    return UsmUserData(
        v3_config["user"],
        authKey=v3_config["auth_key"],
        privKey=v3_config["priv_key"],
        authProtocol=auth_oids[v3_config["auth_protocol"]],
        privProtocol=priv_oids[v3_config["priv_protocol"]],
    )


def describe_security(v3_config):
    """Short label for the health page. Derived from the settings, never a secret."""
    if v3_config is None:
        return "SNMPv2c"
    auth = v3_config["auth_protocol"]
    priv = v3_config["priv_protocol"]
    return "SNMPv3 authPriv (%s/%s)" % (auth, priv)


ERROR_LABELS = (
    ("digest", "SNMPv3 authentication failed: check the authentication key and protocol"),
    ("usm user", "SNMPv3 user rejected: check the user name"),
    (
        "ciphering",
        "SNMPv3 decryption failed: check the encryption key and protocol, "
        "and that the 'cryptography' package is installed",
    ),
    ("windows of trust", "SNMPv3 message outside the time window: check the clock of the router and of the NOC"),
    ("security level", "SNMPv3 security level not accepted: the router user is not authPriv"),
)


def explain_error(text, v3=False):
    """Turn a PySNMP v3 error into a label that says what to check. Other texts pass through.

    With v3 a timeout gets a hint: RouterOS stays silent on a wrong user or key instead of replying.
    """
    lowered = str(text).lower()
    if v3 and "no snmp response" in lowered:
        return str(text) + " (SNMPv3: a wrong user, keys or protocol is also answered with silence: check them and the router's SNMP community)"
    for needle, label in ERROR_LABELS:
        if needle in lowered:
            return label
    return str(text)
