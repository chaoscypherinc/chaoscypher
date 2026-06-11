# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Docstrings rendered into the public API docs must match the code.

These tests pin endpoint docstrings against the actual route tables and
service behavior so the prose cannot silently drift from the code again.
"""

import re

import pytest


@pytest.mark.unit
@pytest.mark.cortex
class TestPauseApiModuleDocstring:
    """The pause/api.py module docstring lists every route, no more, no less."""

    def test_docstring_route_list_matches_registered_routes(self):
        """Each `METHOD /path` line maps 1:1 to a registered route."""
        from fastapi.routing import APIRoute

        from chaoscypher_cortex.features.pause import api as pause_api

        doc = pause_api.__doc__ or ""
        documented = re.findall(r"^\s*(GET|POST|PUT|PATCH|DELETE)\s+(/\S*)", doc, re.MULTILINE)

        registered = [
            (method, route.path)
            for router in (pause_api.sources_router, pause_api.system_router)
            for route in router.routes
            if isinstance(route, APIRoute)
            for method in sorted(route.methods)
        ]

        assert sorted(documented) == sorted(registered)

    def test_docstring_has_no_hardcoded_route_count(self):
        """No spelled-out endpoint count that can drift when routes change."""
        from chaoscypher_cortex.features.pause import api as pause_api

        doc = (pause_api.__doc__ or "").lower()
        count_words = ["eight endpoints", "nine endpoints", "ten endpoints", "seven endpoints"]
        assert not any(w in doc for w in count_words)


@pytest.mark.unit
@pytest.mark.cortex
class TestDuplicateWorkflowDocstring:
    """duplicate_workflow docs reflect the real on_duplicate='rename' naming."""

    def test_docstring_names_the_real_rename_suffix(self):
        """import_workflow renames with ' (imported)', not '(Copy)'."""
        from chaoscypher_cortex.features.workflows.api import duplicate_workflow

        doc = duplicate_workflow.__doc__ or ""
        assert "(Copy)" not in doc
        assert "(imported)" in doc


@pytest.mark.unit
@pytest.mark.cortex
class TestGroundingNeighborsDocstring:
    """get_neighbors docs reflect the settings-driven limit default."""

    def test_docstring_does_not_hardcode_limit_default(self):
        """Limit defaults to pagination settings, not a hardcoded 100."""
        from chaoscypher_cortex.features.graph.grounding_api import get_neighbors

        doc = get_neighbors.__doc__ or ""
        assert "default: 100" not in doc
        assert "defaults to settings" in doc
