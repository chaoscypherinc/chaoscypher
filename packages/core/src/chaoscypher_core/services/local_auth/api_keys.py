# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""API key generation, hashing, verification.

Format: cc_live_<32 url-safe base64 chars from secrets.token_urlsafe(24)>
Storage: bcrypt hash in credentials.json; plaintext shown to user ONCE on creation.
"""

from __future__ import annotations

import secrets

from passlib.hash import bcrypt  # type: ignore[import-untyped]


API_KEY_PREFIX = "cc_live_"
API_KEY_SECRET_LEN = 24  # bytes -> ~32 chars after base64 urlsafe
BCRYPT_ROUNDS = 12


def generate_api_key() -> str:
    """Generate a cryptographically strong API key with the cc_live_ prefix."""
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(API_KEY_SECRET_LEN)}"


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
