# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 6 Task 6 tests: system_prompt as a configurable ExtractionSettings field.

Tests:
1. ExtractionSettings.system_prompt defaults to the historical hardcoded value.
2. The AIEntityExtractor.__init__ resolves _system_prompt from settings.
3. A domain config refuses ``extraction_overrides`` (removed, spec 2026-10-07 § 3).
4. ConfigurableDomain offers no system-prompt override.
"""

from __future__ import annotations

from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
    DomainConfigModel,
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
# Domain-level overrides were removed (spec 2026-10-07 "additive domains" § 3):
# the system prompt is the extraction settings' alone.
# ---------------------------------------------------------------------------


def test_domain_config_model_refuses_extraction_overrides() -> None:
    """A domain config may no longer carry ``extraction_overrides`` (it fails loudly at load)."""
    import pytest
    from pydantic import ValidationError

    pack = {"name": "bio", "role": "pack", "extends": "core"}
    DomainConfigModel.model_validate(pack)
    with pytest.raises(ValidationError, match="extraction_overrides"):
        DomainConfigModel.model_validate(
            {**pack, "extraction_overrides": {"system_prompt": "Custom prompt."}}
        )


def test_configurable_domain_offers_no_system_prompt_override() -> None:
    """The extractor reads the prompt from settings: a domain has no override accessor."""
    from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
        ConfigurableDomain,
    )

    domain = ConfigurableDomain({"name": "bio", "role": "pack", "extends": "core", "templates": {}})
    assert not hasattr(domain, "get_system_prompt_override")


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
