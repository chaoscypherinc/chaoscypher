# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the settings masking helpers.

Covers the boolean-style mask_secret_value (returns "configured" / None)
and the allow-list-aware mask_settings_dict.
"""

from __future__ import annotations

from pydantic import SecretStr

from chaoscypher_core.app_config import mask_secret_value, mask_settings_dict


# ---------------------------------------------------------------------------
# mask_secret_value
# ---------------------------------------------------------------------------


class TestMaskSecretValueSecurity:
    """Ensure no secret content is ever leaked through the masked value."""

    def test_long_string_returns_configured(self) -> None:
        masked = mask_secret_value("sk-abcdefghijklmnopqrstuvwx")
        assert masked == "configured"

    def test_long_string_does_not_leak_prefix(self) -> None:
        masked = mask_secret_value("sk-abcdefghijklmnopqrstuvwx")
        assert "sk-a" not in str(masked)

    def test_long_string_does_not_leak_suffix(self) -> None:
        masked = mask_secret_value("sk-abcdefghijklmnopqrstuvwx")
        assert "uvwx" not in str(masked)

    def test_short_string_returns_configured(self) -> None:
        # Previously returned "****configured****"; must still not leak value
        masked = mask_secret_value("short")
        assert masked == "configured"
        assert "short" not in str(masked)


class TestMaskSecretValueUnset:
    def test_none_returns_none(self) -> None:
        assert mask_secret_value(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert mask_secret_value("") is None


class TestMaskSecretValueSecretStr:
    """Accepts SecretStr.

    Pydantic v2 model_dump returns SecretStr unwrapped only when mode='json';
    dict mode returns SecretStr objects.
    """

    def test_secretstr_long_returns_configured(self) -> None:
        assert mask_secret_value(SecretStr("sk-12345678901234567890")) == "configured"

    def test_secretstr_short_returns_configured(self) -> None:
        assert mask_secret_value(SecretStr("short")) == "configured"

    def test_secretstr_empty_returns_none(self) -> None:
        assert mask_secret_value(SecretStr("")) is None


# ---------------------------------------------------------------------------
# mask_settings_dict — allow-list parameter
# ---------------------------------------------------------------------------


class TestMaskSettingsDictAllowList:
    def test_custom_secret_key_masked(self) -> None:
        """Future secret keys not on the default list are masked when enrolled."""
        raw = {"plugins": {"my_plugin": {"credential": "supersecret"}}}
        masked = mask_settings_dict(
            raw,
            secret_keys=("api_key", "password", "credential", "token"),
        )
        assert masked["plugins"]["my_plugin"]["credential"] != "supersecret"
        assert masked["plugins"]["my_plugin"]["credential"] == "configured"

    def test_default_api_key_masked(self) -> None:
        raw = {"llm": {"openai_api_key": "sk-supersecret"}}
        masked = mask_settings_dict(raw)
        # openai_api_key contains "api_key" substring → should be masked
        assert masked["llm"]["openai_api_key"] == "configured"

    def test_non_secret_key_not_masked(self) -> None:
        raw = {"llm": {"model": "gpt-4"}}
        masked = mask_settings_dict(raw)
        assert masked["llm"]["model"] == "gpt-4"

    def test_nested_password_masked(self) -> None:
        raw = {"db": {"password": "hunter2", "host": "localhost"}}
        masked = mask_settings_dict(raw)
        assert masked["db"]["password"] == "configured"
        assert masked["db"]["host"] == "localhost"

    def test_allow_list_none_not_masked(self) -> None:
        """Keys not in the allow-list are not masked even if they look secret-ish."""
        raw = {"db": {"credential": "supersecret"}}
        # "credential" is NOT in the default allow-list
        masked = mask_settings_dict(raw)
        assert masked["db"]["credential"] == "supersecret"


class TestMaskSettingsDictTypeAware:
    """Keyword-based masking must only touch string-like values.

    Many production fields contain a secret keyword as a substring while
    holding numeric or boolean values (token-count caps, rate-limit
    counters, cost-tracking flags). Masking those would clobber the value
    with the literal string "configured", breaking PATCH /settings round
    trips with pydantic int/float/bool parsing errors.
    """

    def test_int_field_with_token_substring_not_masked(self) -> None:
        raw = {"llm": {"openai_max_output_tokens": 8192}}
        masked = mask_settings_dict(raw)
        assert masked["llm"]["openai_max_output_tokens"] == 8192

    def test_float_field_with_token_substring_not_masked(self) -> None:
        raw = {"llm": {"token_cost_input_per_million": 1.5}}
        masked = mask_settings_dict(raw)
        assert masked["llm"]["token_cost_input_per_million"] == 1.5

    def test_bool_field_with_token_substring_not_masked(self) -> None:
        raw = {"llm": {"enable_token_cost_tracking": True}}
        masked = mask_settings_dict(raw)
        assert masked["llm"]["enable_token_cost_tracking"] is True

    def test_int_field_with_api_key_substring_not_masked(self) -> None:
        # rate_limit.api_key_max_requests / api_key_window_seconds are int
        # counters keyed by API key, not secrets themselves.
        raw = {"rate_limit": {"api_key_max_requests": 100, "api_key_window_seconds": 60}}
        masked = mask_settings_dict(raw)
        assert masked["rate_limit"]["api_key_max_requests"] == 100
        assert masked["rate_limit"]["api_key_window_seconds"] == 60

    def test_string_secret_still_masked_when_keyword_match(self) -> None:
        raw = {"plugins": {"x": {"my_token": "ghp_supersecret"}}}
        masked = mask_settings_dict(raw)
        assert masked["plugins"]["x"]["my_token"] == "configured"

    def test_full_settings_dict_round_trips_after_masking(self) -> None:
        """End-to-end: mask a realistic settings shape, ensure non-secret fields are unchanged."""
        raw: dict[str, object] = {
            "llm": {
                "openai_api_key": "sk-realsecret",
                "openai_max_output_tokens": 4096,
                "ai_max_tokens": 2048,
                "extraction_max_tokens": 8192,
                "enable_token_cost_tracking": False,
                "token_cost_input_per_million": 0.0,
            },
            "rate_limit": {
                "api_key_max_requests": 1000,
                "api_key_window_seconds": 3600,
            },
            "chunking": {
                "target_group_tokens": 600,
                "output_tokens_per_chunk": 2000,
            },
        }
        masked = mask_settings_dict(raw)
        # The actual secret is masked.
        assert masked["llm"]["openai_api_key"] == "configured"
        # Numeric/bool fields with keyword-matching names are preserved.
        assert masked["llm"]["openai_max_output_tokens"] == 4096
        assert masked["llm"]["ai_max_tokens"] == 2048
        assert masked["llm"]["extraction_max_tokens"] == 8192
        assert masked["llm"]["enable_token_cost_tracking"] is False
        assert masked["llm"]["token_cost_input_per_million"] == 0.0
        assert masked["rate_limit"]["api_key_max_requests"] == 1000
        assert masked["rate_limit"]["api_key_window_seconds"] == 3600
        assert masked["chunking"]["target_group_tokens"] == 600
        assert masked["chunking"]["output_tokens_per_chunk"] == 2000
