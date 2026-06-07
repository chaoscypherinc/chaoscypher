# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for domain config schema validation."""

import json
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
    DomainConfigModel,
    ExclusionRule,
)


def test_valid_minimal_config() -> None:
    model = DomainConfigModel.model_validate(
        {
            "name": "technical",
            "description": "Technical docs",
            "entity_templates": [],
        }
    )
    assert model.name == "technical"
    assert model.entity_templates == []


def test_missing_name_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DomainConfigModel.model_validate({"description": "No name", "entity_templates": []})


def test_blank_name_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DomainConfigModel.model_validate(
            {"name": "   ", "description": "blank", "entity_templates": []}
        )


def test_entity_templates_must_be_list() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DomainConfigModel.model_validate(
            {
                "name": "x",
                "description": "",
                "entity_templates": "not-a-list",
            }
        )


def test_content_exclusions_optional_and_validated() -> None:
    # Missing is fine.
    DomainConfigModel.model_validate({"name": "x", "description": "", "entity_templates": []})
    # Present but shaped right is fine.
    DomainConfigModel.model_validate(
        {
            "name": "x",
            "description": "",
            "entity_templates": [],
            "content_exclusions": {"categories": ["toc"], "custom_patterns": []},
        }
    )


# ---------------------------------------------------------------------------
# ExclusionRule
# ---------------------------------------------------------------------------


class TestExclusionRule:
    """Tests for ExclusionRule — structured form of domain entity exclusions.

    The legacy format was ``list[str]`` where each string crammed a
    description and quoted examples into one regex-parsed blob. That made
    silent-degrade failures possible (missing quotes, smart quotes,
    typos) — extraction would proceed with an empty exclusion set and no
    signal. The structured shape requires both a description and a
    non-empty examples list, validated at load time.
    """

    def test_valid_rule(self) -> None:
        rule = ExclusionRule(
            description="Bare titles without names",
            examples=["Prince", "Princess", "The Count"],
        )
        assert rule.description == "Bare titles without names"
        assert rule.examples == ["Prince", "Princess", "The Count"]

    def test_blank_description_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExclusionRule(description="   ", examples=["Prince"])

    def test_empty_examples_rejected(self) -> None:
        """A rule with no examples is the silent-degrade case we're fixing.

        Without examples there is nothing for ``filter_excluded_entities``
        to match against — the rule is effectively a no-op. Reject at
        load.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExclusionRule(description="Bare titles", examples=[])

    def test_blank_example_rejected(self) -> None:
        """A whitespace-only example matches everything via ``str in str``.

        That would silently filter every entity. Reject at load.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExclusionRule(description="Bare titles", examples=["Prince", "   "])

    def test_as_prompt_text_format(self) -> None:
        """``as_prompt_text`` returns the LLM-facing natural-language form.

        Replaces the legacy hand-formatted string. Format pinned because
        both the entity-extraction prompt and the MCP-tool prompt
        consume it.
        """
        rule = ExclusionRule(
            description="Bare titles without names",
            examples=["Prince", "Princess", "The Count"],
        )
        assert (
            rule.as_prompt_text() == 'Bare titles without names: "Prince", "Princess", "The Count"'
        )

    def test_as_prompt_text_single_example(self) -> None:
        rule = ExclusionRule(description="X", examples=["Solo"])
        assert rule.as_prompt_text() == 'X: "Solo"'


class TestDomainConfigEntityExclusions:
    """Tests for DomainConfigModel.entity_exclusions field validation."""

    def test_typed_exclusions_validate(self) -> None:
        model = DomainConfigModel.model_validate(
            {
                "name": "literary",
                "description": "",
                "entity_templates": [],
                "entity_exclusions": [
                    {
                        "description": "Bare titles without names",
                        "examples": ["Prince", "Princess"],
                    },
                    {
                        "description": "Unnamed people",
                        "examples": ["the old man", "a servant"],
                    },
                ],
            }
        )
        assert len(model.entity_exclusions) == 2
        assert model.entity_exclusions[0].description == "Bare titles without names"
        assert model.entity_exclusions[1].examples == ["the old man", "a servant"]

    def test_legacy_string_exclusions_rejected(self) -> None:
        """Legacy ``list[str]`` form must fail loud at load.

        The old format is exactly what the bug makes fragile. Refusing
        it forces every domain plugin to migrate to the typed shape.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DomainConfigModel.model_validate(
                {
                    "name": "literary",
                    "description": "",
                    "entity_templates": [],
                    "entity_exclusions": [
                        'Bare titles: "Prince", "Princess"',  # legacy string form
                    ],
                }
            )

    def test_missing_field_defaults_to_empty(self) -> None:
        model = DomainConfigModel.model_validate(
            {"name": "x", "description": "", "entity_templates": []}
        )
        assert model.entity_exclusions == []


def test_registry_skips_invalid_domain_with_warning(tmp_path: Path) -> None:
    from chaoscypher_core.services.sources.engine.extraction.domains.registry import (
        DomainRegistry,
    )
    from chaoscypher_core.settings import EngineSettings, PathSettings

    # Write an obviously broken jsonld to user plugins dir.
    user_dir = tmp_path / "plugins" / "domains"
    user_dir.mkdir(parents=True)
    (user_dir / "broken.jsonld").write_text(
        json.dumps({"description": "no name field here"}),
        encoding="utf-8",
    )

    settings = EngineSettings(
        paths=PathSettings(
            data_dir=str(tmp_path),
            config_dir=str(tmp_path / "c"),
            cache_dir=str(tmp_path / "ch"),
        )
    )

    with capture_logs() as logs:
        registry = DomainRegistry(settings=settings)

    # The broken domain must not be in the registry.
    assert "broken" not in registry._plugins

    # A warning event must identify the file.
    events = [e for e in logs if e.get("event") == "domain_schema_invalid"]
    assert len(events) == 1
    assert "broken.jsonld" in str(events[0].get("file", ""))


def test_registry_still_loads_builtin_domains() -> None:
    from chaoscypher_core.services.sources.engine.extraction.domains.registry import (
        DomainRegistry,
    )

    # No settings => built-ins only.
    registry = DomainRegistry()

    # Every shipped domain must still parse with the new validator.
    assert registry.count() > 0
