# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests for Core exception hygiene in services/sources/engine/.

Covers the two CC045-flagged sites in this sub-tree:

- ``commit/matcher.py`` — RuntimeError (noqa'd programmer-error invariant)
  when default_node_template drifts from DEFAULT_NODE_TEMPLATES.
- ``extraction/extractor.py`` — ValidationError (field="hierarchical_groups")
  when the caller passes an empty group list.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.engine.commit.matcher import EntityTemplateMatcher
from chaoscypher_core.services.sources.engine.extraction.extractor import (
    extract_entities_from_groups,
)


# ---------------------------------------------------------------------------
# matcher.py — _ensure_fallback_template_exists invariant guard
# ---------------------------------------------------------------------------


class TestMatcherFallbackInvariantGuard:
    """RuntimeError is raised when the fallback template constant has drifted."""

    def test_raises_runtime_error_on_template_constant_drift(self) -> None:
        """RuntimeError fires when default_node_template is absent from DEFAULT_NODE_TEMPLATES.

        This is a programmer-error invariant (config drift), not a user-input
        error. The raise is noqa'd CC045 and must remain RuntimeError to
        signal misconfiguration as loudly as possible.
        """
        repo = MagicMock()
        repo.get_template.return_value = None  # template is missing from DB

        # Patch DEFAULT_NODE_TEMPLATES to *not* contain the configured id.
        # The matcher reads _DEFAULT_NODE_TEMPLATE at module import time
        # from GraphSettings().default_node_template, so we patch the list
        # seen inside _ensure_fallback_template_exists.
        with patch(
            "chaoscypher_core.templates.default_templates.DEFAULT_NODE_TEMPLATES",
            [],  # empty — configured id is absent
        ):
            matcher = EntityTemplateMatcher(graph_repository=repo)

            with pytest.raises(RuntimeError, match="drifted apart"):
                matcher._ensure_fallback_template_exists()


# ---------------------------------------------------------------------------
# extractor.py — ValidationError on empty hierarchical_groups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExtractEntitiesFromGroupsValidation:
    """extract_entities_from_groups raises ValidationError for empty input."""

    async def test_raises_validation_error_for_empty_groups(self) -> None:
        """Empty hierarchical_groups triggers a ValidationError, not ValueError.

        The field tag must be 'hierarchical_groups' so the HTTP error envelope
        can surface the offending parameter to the caller.
        """
        from types import SimpleNamespace

        fake_settings = SimpleNamespace(extraction=SimpleNamespace())

        with pytest.raises(ValidationError) as exc_info:
            await extract_entities_from_groups(
                hierarchical_groups=[],
                settings=fake_settings,  # type: ignore[arg-type]
                embedding_service=None,
            )

        err = exc_info.value
        assert err.details.get("field") == "hierarchical_groups"
        assert "hierarchical_groups" in str(err)

    async def test_raises_validation_error_not_value_error_for_empty_groups(self) -> None:
        """Confirms the exception is ValidationError, not the old bare ValueError."""
        from types import SimpleNamespace

        fake_settings = SimpleNamespace(extraction=SimpleNamespace())

        with pytest.raises(ValidationError):
            await extract_entities_from_groups(
                hierarchical_groups=[],
                settings=fake_settings,  # type: ignore[arg-type]
                embedding_service=None,
            )
