# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for mask_secret_value / mask_settings_dict.

Before b5098370 migrated 14 secret fields to SecretStr, mask_secret_value
only accepted str. After that migration, ``settings.model_dump()`` returns
SecretStr instances (not unwrapped) for those fields, and mask_secret_value
crashed with ``TypeError: 'SecretStr' object is not subscriptable`` when
the GET /api/v1/settings endpoint tried to mask them. These tests pin the
fix: the function now unwraps SecretStr before returning the boolean-style
"configured" / None indicator.
"""

from pydantic import SecretStr

from chaoscypher_core.app_config import mask_secret_value


def test_mask_secret_value_plain_string_long():
    # Must not leak prefix or suffix; returns opaque "configured"
    result = mask_secret_value("sk-12345678901234567890")
    assert result == "configured"
    assert "sk-1" not in str(result)
    assert "7890" not in str(result)


def test_mask_secret_value_plain_string_short():
    # Under 12 chars → still returns "configured", not the old placeholder
    assert mask_secret_value("short") == "configured"


def test_mask_secret_value_none_returns_none():
    assert mask_secret_value(None) is None


def test_mask_secret_value_empty_string_returns_none():
    assert mask_secret_value("") is None


def test_mask_secret_value_secretstr_long():
    """SecretStr wrapping a >=12 char secret is unwrapped and masked."""
    wrapped = SecretStr("sk-12345678901234567890")
    result = mask_secret_value(wrapped)
    assert result == "configured"
    assert "sk-1" not in str(result)
    assert "7890" not in str(result)


def test_mask_secret_value_secretstr_short():
    assert mask_secret_value(SecretStr("short")) == "configured"


def test_mask_secret_value_secretstr_empty():
    assert mask_secret_value(SecretStr("")) is None
