# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the PublicSettings builder service."""

from chaoscypher_core.app_config import Settings
from chaoscypher_cortex.features.settings_public.models import PublicSettings
from chaoscypher_cortex.features.settings_public.service import build_public_settings


def test_build_returns_public_settings_instance() -> None:
    s = Settings()
    public = build_public_settings(s)
    assert isinstance(public, PublicSettings)


def test_build_pagination_fields_match_settings() -> None:
    s = Settings()
    s.pagination.default_page_size = 77
    public = build_public_settings(s)
    assert public.pagination_default_page_size == 77


def test_build_omits_no_secrets() -> None:
    secret_substrings = ("password", "secret", "token", "api_key")
    for field in PublicSettings.model_fields:
        assert not any(sub in field.lower() for sub in secret_substrings), (
            f"PublicSettings field {field!r} looks like it could leak a secret"
        )


def test_build_recovery_thresholds_match_settings() -> None:
    s = Settings()
    s.source_recovery.recovery_warn_threshold = 7
    s.source_recovery.max_recovery_attempts = 12
    public = build_public_settings(s)
    assert public.recovery_warn_threshold == 7
    assert public.recovery_max_attempts == 12
