"""Password hashing and signed session tokens for the web login (standard library only).

Passwords use scrypt with a random salt per user. A session token is
`base64url(json claims) + "." + base64url(HMAC-SHA256)`; the claims carry the username, the expiry and a
short fingerprint of the stored password hash, so a password change invalidates old tokens. The role is NOT
in the token: the API reads it from the database on every request.
"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import re

SESSION_SECONDS = 8 * 3600
VALID_ROLES = ("admin", "viewer")
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 200

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_KEY_LENGTH = 32
SALT_BYTES = 16

USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]{1,32}$")


def normalize_username(value):
    """Return the lower-case username or raise ValueError when it is not 1-32 of [a-z0-9_.-]."""
    if not isinstance(value, str):
        raise ValueError("username must be text")
    name = value.strip().lower()
    if not USERNAME_PATTERN.match(name):
        raise ValueError("username must be 1-32 characters: letters, digits, dot, dash or underscore")
    return name


def check_password_strength(password):
    """Raise ValueError when the password is too short or too long."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError("password must have at least " + str(MIN_PASSWORD_LENGTH) + " characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError("password must have at most " + str(MAX_PASSWORD_LENGTH) + " characters")


def derive(password, salt, n, r, p):
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=SCRYPT_KEY_LENGTH, maxmem=128 * n * r * 2 + 1024 * 1024
    )


def hash_password(password):
    """Return `scrypt$n$r$p$salt_hex$hash_hex` with a fresh random salt."""
    salt = os.urandom(SALT_BYTES)
    digest = derive(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return "scrypt$%d$%d$%d$%s$%s" % (SCRYPT_N, SCRYPT_R, SCRYPT_P, salt.hex(), digest.hex())


def verify_password(password, stored):
    """Constant-time check of a password against a stored hash; any malformed hash is a plain False."""
    if not isinstance(password, str) or not isinstance(stored, str):
        return False
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n = int(parts[1])
        r = int(parts[2])
        p = int(parts[3])
        salt = bytes.fromhex(parts[4])
        expected = bytes.fromhex(parts[5])
        # Refuse absurd parameters from a corrupted row instead of burning memory.
        if n < 2 or n > 2 ** 20 or r < 1 or r > 32 or p < 1 or p > 16:
            return False
        actual = derive(password, salt, n, r, p)
    except (ValueError, OverflowError):
        return False
    return hmac.compare_digest(actual, expected)


# Verified for unknown users so that "no such user" costs the same time as "wrong password".
DUMMY_HASH = hash_password("mini-noc-dummy-password")


def password_fingerprint(stored_hash):
    """Short public fingerprint of a stored hash, used to invalidate tokens when the password changes."""
    return hashlib.sha256(stored_hash.encode("utf-8")).hexdigest()[:16]


def b64_encode(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64_decode(text):
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def sign(secret, body):
    return hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()


def make_token(secret, username, fingerprint, now_ts, lifetime_seconds):
    claims = {"u": username, "pv": fingerprint, "exp": int(now_ts) + int(lifetime_seconds)}
    body = b64_encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    return body + "." + b64_encode(sign(secret, body))


def parse_token(secret, token, now_ts):
    """Return the claims of a valid, unexpired token, or None for anything else."""
    if not isinstance(token, str) or token.count(".") != 1:
        return None
    body, signature = token.split(".")
    try:
        given = b64_decode(signature)
        if not hmac.compare_digest(given, sign(secret, body)):
            return None
        claims = json.loads(b64_decode(body).decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeError):
        return None
    if not isinstance(claims, dict):
        return None
    expires = claims.get("exp")
    if isinstance(expires, bool) or not isinstance(expires, int) or now_ts >= expires:
        return None
    if not isinstance(claims.get("u"), str) or not isinstance(claims.get("pv"), str):
        return None
    return claims
