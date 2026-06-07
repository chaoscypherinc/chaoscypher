# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for domain-level granular cleaner overrides.

Phase 4 Task 4 (2026-05-08): individual ``NormalizerSettings`` boolean
flags can be overridden per domain via ``DomainNormalizerOverrides``.

Design constraints tested here:
- ``None`` fields fall through to the global default (no-op).
- Only explicitly-set fields change; all others stay at global value.
- ``_resolved_settings`` with ``None`` overrides returns global unchanged.
- ``DomainNormalizerOverrides`` rejects unknown keys (``extra="forbid"``).
"""

from __future__ import annotations

import pytest

from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
    DomainNormalizerOverrides,
)
from chaoscypher_core.services.sources.normalizer.service import _resolved_settings
from chaoscypher_core.settings import NormalizerSettings


# ---------------------------------------------------------------------------
# _resolved_settings unit tests
# ---------------------------------------------------------------------------


def test_resolved_settings_no_override_returns_global_unchanged() -> None:
    """When domain_overrides is None, global settings are returned as-is."""
    global_settings = NormalizerSettings()
    result = _resolved_settings(global_settings, None)
    # Must be the *same object* — no unnecessary copy.
    assert result is global_settings


def test_resolved_settings_all_none_fields_returns_global_unchanged() -> None:
    """An all-None DomainNormalizerOverrides is a no-op; returns global as-is."""
    global_settings = NormalizerSettings()
    overrides = DomainNormalizerOverrides()  # every field is None by default
    result = _resolved_settings(global_settings, overrides)
    assert result is global_settings


def test_resolved_settings_single_flag_flipped() -> None:
    """Domain override flips one flag without affecting the others."""
    global_settings = NormalizerSettings(enable_ocr_cleaning=True)
    overrides = DomainNormalizerOverrides(enable_ocr_cleaning=False)
    result = _resolved_settings(global_settings, overrides)

    assert result.enable_ocr_cleaning is False
    # All other flags should match the global settings exactly.
    assert result.enable_encoding_fix == global_settings.enable_encoding_fix
    assert result.enable_unicode_normalize == global_settings.enable_unicode_normalize
    assert result.enable_control_char_removal == global_settings.enable_control_char_removal
    assert result.enable_whitespace_normalize == global_settings.enable_whitespace_normalize
    assert result.enable_duplicate_removal == global_settings.enable_duplicate_removal
    assert result.enable_markdown_normalize == global_settings.enable_markdown_normalize


def test_resolved_settings_partial_override_preserves_unset_flags() -> None:
    """Partial override: only the set flags change; unset flags fall back to global."""
    global_settings = NormalizerSettings(enable_ocr_cleaning=True, enable_duplicate_removal=True)
    overrides = DomainNormalizerOverrides(enable_ocr_cleaning=False)  # only this one set
    result = _resolved_settings(global_settings, overrides)

    assert result.enable_ocr_cleaning is False
    assert result.enable_duplicate_removal is True  # fell through to global


def test_resolved_settings_all_flags_can_be_overridden() -> None:
    """Every one of the seven flags can be individually overridden."""
    global_settings = NormalizerSettings(
        enable_encoding_fix=True,
        enable_unicode_normalize=True,
        enable_control_char_removal=True,
        enable_whitespace_normalize=True,
        enable_ocr_cleaning=True,
        enable_duplicate_removal=True,
        enable_markdown_normalize=True,
    )
    overrides = DomainNormalizerOverrides(
        enable_encoding_fix=False,
        enable_unicode_normalize=False,
        enable_control_char_removal=False,
        enable_whitespace_normalize=False,
        enable_ocr_cleaning=False,
        enable_duplicate_removal=False,
        enable_markdown_normalize=False,
    )
    result = _resolved_settings(global_settings, overrides)

    assert result.enable_encoding_fix is False
    assert result.enable_unicode_normalize is False
    assert result.enable_control_char_removal is False
    assert result.enable_whitespace_normalize is False
    assert result.enable_ocr_cleaning is False
    assert result.enable_duplicate_removal is False
    assert result.enable_markdown_normalize is False


def test_resolved_settings_can_enable_flag_that_was_globally_off() -> None:
    """Domain override can turn a globally-disabled flag ON."""
    global_settings = NormalizerSettings(enable_ocr_cleaning=False)
    overrides = DomainNormalizerOverrides(enable_ocr_cleaning=True)
    result = _resolved_settings(global_settings, overrides)

    assert result.enable_ocr_cleaning is True


def test_resolved_settings_returns_new_object_when_overrides_present() -> None:
    """When overrides are non-empty, a new settings object is returned, not global."""
    global_settings = NormalizerSettings(enable_ocr_cleaning=True)
    overrides = DomainNormalizerOverrides(enable_ocr_cleaning=False)
    result = _resolved_settings(global_settings, overrides)

    assert result is not global_settings


def test_resolved_settings_non_boolean_flags_unchanged() -> None:
    """Numeric / string threshold fields on NormalizerSettings are not clobbered."""
    global_settings = NormalizerSettings(
        min_line_length=10,
        min_alpha_ratio=0.6,
        gibberish_threshold=0.3,
        duplicate_similarity_threshold=0.9,
        target_format="markdown",
    )
    overrides = DomainNormalizerOverrides(enable_ocr_cleaning=False)
    result = _resolved_settings(global_settings, overrides)

    assert result.min_line_length == 10
    assert result.min_alpha_ratio == pytest.approx(0.6)
    assert result.gibberish_threshold == pytest.approx(0.3)
    assert result.duplicate_similarity_threshold == pytest.approx(0.9)
    assert result.target_format == "markdown"


# ---------------------------------------------------------------------------
# DomainNormalizerOverrides schema tests
# ---------------------------------------------------------------------------


def test_domain_normalizer_overrides_all_defaults_none() -> None:
    """All fields default to None (no override)."""
    overrides = DomainNormalizerOverrides()
    assert overrides.enable_encoding_fix is None
    assert overrides.enable_unicode_normalize is None
    assert overrides.enable_control_char_removal is None
    assert overrides.enable_whitespace_normalize is None
    assert overrides.enable_ocr_cleaning is None
    assert overrides.enable_duplicate_removal is None
    assert overrides.enable_markdown_normalize is None


def test_domain_normalizer_overrides_rejects_unknown_keys() -> None:
    """extra='forbid': unknown keys raise ValidationError at parse time."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DomainNormalizerOverrides.model_validate({"enable_typo_fix": True})


def test_domain_normalizer_overrides_model_validate_from_jsonld_fragment() -> None:
    """model_validate round-trips a typical JSONLD normalizer_overrides block."""
    raw = {"enable_ocr_cleaning": False, "enable_duplicate_removal": True}
    overrides = DomainNormalizerOverrides.model_validate(raw)

    assert overrides.enable_ocr_cleaning is False
    assert overrides.enable_duplicate_removal is True
    # Unset fields remain None.
    assert overrides.enable_encoding_fix is None


def test_domain_normalizer_overrides_model_dump_exclude_none() -> None:
    """model_dump(exclude_none=True) only yields explicitly-set fields."""
    overrides = DomainNormalizerOverrides(enable_ocr_cleaning=False)
    dumped = overrides.model_dump(exclude_none=True)

    assert dumped == {"enable_ocr_cleaning": False}
