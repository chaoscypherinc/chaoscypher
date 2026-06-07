# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for chaoscypher_core.policy — non-tunable security/protocol constants."""

from chaoscypher_core import policy


def test_policy_username_constraints() -> None:
    assert policy.USERNAME_MIN_LENGTH == 3
    assert policy.USERNAME_MAX_LENGTH == 64


def test_policy_password_constraints() -> None:
    assert policy.PASSWORD_MIN_LENGTH == 8
    assert policy.PASSWORD_MAX_LENGTH == 128


def test_policy_api_key_name_max() -> None:
    assert policy.API_KEY_NAME_MAX_LENGTH == 64


def test_policy_queue_name_max() -> None:
    # Queue/op name caps are protocol-shape, not user content.
    assert policy.QUEUE_NAME_MAX_LENGTH == 50
    assert policy.OPERATION_NAME_MAX_LENGTH == 100


def test_policy_database_name_max() -> None:
    assert policy.DATABASE_NAME_MAX_LENGTH == 100


def test_policy_role_max() -> None:
    # Chat role: "user" / "assistant" / "system" / "tool" — short by spec.
    assert policy.CHAT_ROLE_MAX_LENGTH == 50


def test_policy_seconds_per_day() -> None:
    # Removes the magic-number `retention_days * 86400` site.
    assert policy.SECONDS_PER_DAY == 86_400


def test_policy_backup_interval_presets() -> None:
    assert policy.BACKUP_INTERVAL_PRESETS == {
        "hourly": 3600,
        "daily": 86_400,
        "weekly": 604_800,
    }


# Names that are intentionally not int scalars (e.g. preset mappings).
_NON_INT_POLICY_NAMES = {"BACKUP_INTERVAL_PRESETS"}


def test_policy_constants_are_int() -> None:
    """All scalar constants are ints — no string surprises."""
    for name in dir(policy):
        if name.startswith("_") or not name.isupper():
            continue
        if name in _NON_INT_POLICY_NAMES:
            continue
        val = getattr(policy, name)
        assert isinstance(val, int), f"{name} must be int, got {type(val).__name__}"
