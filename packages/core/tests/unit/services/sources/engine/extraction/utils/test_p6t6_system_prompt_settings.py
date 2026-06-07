# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 6 Task 6 tests: system_prompt as a configurable ExtractionSettings field.

Tests:
1. ExtractionSettings.system_prompt defaults to the historical hardcoded value.
2. The AIEntityExtractor.__init__ resolves _system_prompt from settings.
3. DomainExtractionOverrides accepts a system_prompt field.
4. DomainConfigModel.extraction_overrides is None by default.
5. ConfigurableDomain.get_system_prompt_override() returns None when absent.
"""

from __future__ import annotations

from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
    DomainConfigModel,
    DomainExtractionOverrides,
)


# ---------------------------------------------------------------------------
# ExtractionSettings.system_prompt default
# ---------------------------------------------------------------------------


def test_extraction_settings_system_prompt_default() -> None:
    """Default system_prompt must match the historical hardcoded value."""
    from chaoscypher_core.settings import ExtractionSettings

    settings = ExtractionSettings()
    expected = (
        "You are an expert at extracting structured knowledge from text. "
        "Output ONLY the requested format with no additional text."
    )
    assert settings.system_prompt == expected


# ---------------------------------------------------------------------------
# DomainExtractionOverrides
# ---------------------------------------------------------------------------


def test_domain_extraction_overrides_default_none() -> None:
    """DomainExtractionOverrides.system_prompt defaults to None."""
    overrides = DomainExtractionOverrides()
    assert overrides.system_prompt is None


def test_domain_extraction_overrides_accepts_custom_prompt() -> None:
    """A custom system_prompt is accepted and returned."""
    custom = "You are a biomedical extractor."
    overrides = DomainExtractionOverrides(system_prompt=custom)
    assert overrides.system_prompt == custom


def test_domain_config_model_extraction_overrides_default_none() -> None:
    """extraction_overrides is absent (None) by default on DomainConfigModel."""
    model = DomainConfigModel(name="test_domain")
    assert model.extraction_overrides is None


def test_domain_config_model_accepts_extraction_overrides() -> None:
    """DomainConfigModel correctly stores extraction_overrides when provided."""
    overrides = DomainExtractionOverrides(system_prompt="Custom prompt.")
    model = DomainConfigModel(name="bio", extraction_overrides=overrides)
    assert model.extraction_overrides is not None
    assert model.extraction_overrides.system_prompt == "Custom prompt."


# ---------------------------------------------------------------------------
# ConfigurableDomain.get_system_prompt_override
# ---------------------------------------------------------------------------


def test_configurable_domain_no_override_returns_none() -> None:
    """Without extraction_overrides in config, returns None."""
    from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
        ConfigurableDomain,
    )

    domain = ConfigurableDomain({"name": "test", "templates": {}})
    assert domain.get_system_prompt_override() is None


def test_configurable_domain_returns_override_when_set() -> None:
    """When extraction_overrides.system_prompt is present, returns it."""
    from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
        ConfigurableDomain,
    )

    config = {
        "name": "bio",
        "templates": {},
        "extraction_overrides": {"system_prompt": "You are a biomedical entity extractor."},
    }
    domain = ConfigurableDomain(config)
    result = domain.get_system_prompt_override()
    assert result == "You are a biomedical entity extractor."


# ---------------------------------------------------------------------------
# AIEntityExtractor resolves _system_prompt from settings
# ---------------------------------------------------------------------------


def test_extractor_resolves_system_prompt_from_settings() -> None:
    """_system_prompt is populated from settings.extraction.system_prompt."""
    from unittest.mock import MagicMock

    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )

    mock_settings = MagicMock()
    mock_settings.extraction.system_prompt = "Custom extraction prompt."

    extractor = AIEntityExtractor(settings=mock_settings)
    assert extractor._system_prompt == "Custom extraction prompt."


__all__: list[str] = []
