# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 6 Task 3: empty-template fallback opt-in + depth validation.

Tests:
- format_domain_node_templates raises ValidationError when templates are empty
  and allow_template_fallback=False.
- format_domain_edge_templates raises ValidationError similarly.
- apply_depth_strategy raises ValidationError for invalid depth values.
- Valid depths ("quick", "full") still work correctly.
- DomainConfigModel accepts allow_template_fallback field.
"""

from __future__ import annotations

import pytest

from chaoscypher_core.exceptions import ValidationError


class TestTemplateFormatterFallbackGating:
    """format_domain_node/edge_templates respects allow_template_fallback."""

    def test_node_templates_empty_raises_when_fallback_disabled(self) -> None:
        from chaoscypher_core.services.sources.engine.extraction.utils.template_formatter import (
            format_domain_node_templates,
        )

        with pytest.raises(ValidationError, match="no node templates"):
            format_domain_node_templates(
                {"node_templates": [], "edge_templates": []},
                allow_template_fallback=False,
            )

    def test_edge_templates_empty_raises_when_fallback_disabled(self) -> None:
        from chaoscypher_core.services.sources.engine.extraction.utils.template_formatter import (
            format_domain_edge_templates,
        )

        with pytest.raises(ValidationError, match="no edge templates"):
            format_domain_edge_templates(
                {"node_templates": [], "edge_templates": []},
                allow_template_fallback=False,
            )

    def test_node_templates_empty_uses_fallback_when_allowed(self) -> None:
        from chaoscypher_core.services.sources.engine.extraction.utils.template_formatter import (
            format_domain_node_templates,
        )

        result = format_domain_node_templates(
            {"node_templates": [], "edge_templates": []},
            allow_template_fallback=True,
        )
        # Should return the generic fallback content
        assert "Person" in result or "Item" in result

    def test_node_templates_with_content_works_regardless_of_flag(self) -> None:
        from chaoscypher_core.services.sources.engine.extraction.utils.template_formatter import (
            format_domain_node_templates,
        )

        templates = {"node_templates": [{"name": "Actor", "description": "A film actor"}]}
        result = format_domain_node_templates(templates, allow_template_fallback=False)
        assert "Actor" in result

    def test_default_allow_template_fallback_is_true(self) -> None:
        """Legacy callers without the kwarg get the original behaviour."""
        from chaoscypher_core.services.sources.engine.extraction.utils.template_formatter import (
            format_domain_node_templates,
        )

        # No raise expected — default is True (back-compat)
        result = format_domain_node_templates({"node_templates": [], "edge_templates": []})
        assert result  # non-empty fallback


class TestDepthValidation:
    """apply_depth_strategy rejects invalid depth strings."""

    def test_valid_depth_full_returns_all_groups(self) -> None:
        from chaoscypher_core.services.sources.engine.extraction.orchestration import (
            apply_depth_strategy,
        )

        groups = [{"id": i} for i in range(10)]
        result = apply_depth_strategy(groups, "full")
        assert result == groups

    def test_valid_depth_quick_returns_subset(self) -> None:
        from chaoscypher_core.services.sources.engine.extraction.orchestration import (
            apply_depth_strategy,
        )

        groups = [{"id": i} for i in range(20)]
        result = apply_depth_strategy(groups, "quick", quick_sample_size=5)
        assert len(result) == 5

    def test_invalid_depth_raises_validation_error(self) -> None:
        from chaoscypher_core.services.sources.engine.extraction.orchestration import (
            apply_depth_strategy,
        )

        with pytest.raises(ValidationError, match="Invalid extraction depth"):
            apply_depth_strategy([], "ful")  # typo

    def test_invalid_depth_with_trailing_space_raises(self) -> None:
        from chaoscypher_core.services.sources.engine.extraction.orchestration import (
            apply_depth_strategy,
        )

        with pytest.raises(ValidationError, match="Invalid extraction depth"):
            apply_depth_strategy([], "quick ")  # trailing space

    def test_invalid_depth_empty_string_raises(self) -> None:
        from chaoscypher_core.services.sources.engine.extraction.orchestration import (
            apply_depth_strategy,
        )

        with pytest.raises(ValidationError):
            apply_depth_strategy([], "")


class TestDomainConfigSchema:
    """DomainConfigModel exposes allow_template_fallback field."""

    def test_allow_template_fallback_defaults_false(self) -> None:
        from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
            DomainConfigModel,
        )

        model = DomainConfigModel(name="test")
        assert model.allow_template_fallback is False

    def test_allow_template_fallback_can_be_set_true(self) -> None:
        from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
            DomainConfigModel,
        )

        model = DomainConfigModel(name="test", allow_template_fallback=True)
        assert model.allow_template_fallback is True
