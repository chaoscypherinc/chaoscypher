# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""API key generation, hashing, verification.

Format: cc_live_<32 url-safe base64 chars from secrets.token_urlsafe(24)>
Storage: bcrypt hash in credentials.json; plaintext shown to user ONCE on creation.

Each stored record also carries a *selector* — ``HMAC-SHA256(per-install
secret, key_plaintext)`` in hex — used purely as a lookup index so that
verification bcrypt-checks exactly one candidate instead of scanning every
stored hash. The selector is not a credential: it cannot be inverted to the
key, and bcrypt still authenticates. See ``api_key_lookup`` for the flow.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from passlib.hash import bcrypt  # type: ignore[import-untyped]


API_KEY_PREFIX = "cc_live_"
API_KEY_SECRET_LEN = 24  # bytes -> ~32 chars after base64 urlsafe
BCRYPT_ROUNDS = 12
SELECTOR_SECRET_BYTES = 32


def generate_api_key() -> str:
    """Generate a cryptographically strong API key with the cc_live_ prefix."""
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(API_KEY_SECRET_LEN)}"


def generate_selector_secret() -> str:
    """Return a fresh per-install selector secret as a hex string."""
    return secrets.token_hex(SELECTOR_SECRET_BYTES)


def compute_api_key_selector(key: str, secret: str) -> str:
    """Return the lookup selector for ``key`` under the per-install ``secret``.

    Args:
        key: Plaintext API key.
        secret: Per-install selector secret, hex-encoded.

    Returns:
        Hex ``HMAC-SHA256(secret, key)``. Deterministic for a given install,
        so it can index the single stored record that may match ``key``.

    Raises:
        ValueError: If ``secret`` is not valid hex.

    """
    return hmac.new(bytes.fromhex(secret), key.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_api_key(key: str) -> str:
    """Return a bcrypt hash of the key for storage."""
    hashed: str = bcrypt.using(rounds=BCRYPT_ROUNDS).hash(key)
    return hashed


def verify_api_key(key: str, hashed: str) -> bool:
    """Constant-time verify a key against its bcrypt hash.

    Returns False (never raises) for malformed hashes or any other error.
    """
    try:
        result: bool = bcrypt.verify(key, hashed)
        return result
    except (ValueError, TypeError):  # fmt: skip
        return False
