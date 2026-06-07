# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for API key generation / verification."""

from __future__ import annotations

from chaoscypher_core.services.local_auth.api_keys import (
    API_KEY_PREFIX,
    generate_api_key,
    hash_api_key,
    verify_api_key,
)


def test_generated_key_has_prefix() -> None:
    key = generate_api_key()
    assert key.startswith(API_KEY_PREFIX)
    assert key.startswith("cc_live_")


def test_generated_key_has_enough_entropy() -> None:
    key = generate_api_key()
    assert len(key) >= len(API_KEY_PREFIX) + 32


def test_generate_is_unique() -> None:
    assert generate_api_key() != generate_api_key()


def test_hash_key_produces_bcrypt() -> None:
    h = hash_api_key(generate_api_key())
    assert h.startswith("$2b$")


def test_verify_matches() -> None:
    key = generate_api_key()
    h = hash_api_key(key)
    assert verify_api_key(key, h) is True


def test_verify_mismatch() -> None:
    h = hash_api_key(generate_api_key())
    assert verify_api_key("cc_live_wrong", h) is False


def test_verify_invalid_hash_returns_false() -> None:
    """Malformed hash must not raise."""
    assert verify_api_key(generate_api_key(), "not-a-hash") is False


def test_verify_empty_key_returns_false() -> None:
    """Empty string key should not accidentally match."""
    h = hash_api_key(generate_api_key())
    assert verify_api_key("", h) is False


def test_hash_is_slow_enough() -> None:
    """Sanity check bcrypt cost factor - hashing should take noticeable time.

    Not a strict perf test (flaky on CI); asserts hash prefix contains '$12$'
    reflecting the configured cost factor BCRYPT_ROUNDS=12.
    """
    h = hash_api_key(generate_api_key())
    assert "$12$" in h
