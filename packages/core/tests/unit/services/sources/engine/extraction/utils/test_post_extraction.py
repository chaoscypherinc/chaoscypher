# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the shared post-extraction pipeline helpers in
``post_extraction.py``.

The helpers exist so the standalone, service, and finalizer paths run
the same sequence with the same inputs — this test file guards the
contracts each helper exposes to its three callers.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from chaoscypher_core.services.sources.engine.extraction.utils.post_extraction import (
    apply_domain_type_aliases,
)


class TestApplyDomainTypeAliases:
    """Tests for ``apply_domain_type_aliases`` — the shared wrapper that
    resolves a domain's type_aliases mapping and applies it to a list of
    entities.

    Regression context (2026-05-19): a re-import of
    ``war_and_peace_tiny.txt`` after the initial type_aliases commit
    (``f0e243ab1``) showed zero entities had the ``entity_subtype``
    property — the wiring was only on the standalone extractor path,
    not on the production finalizer. This helper consolidates the call
    so every finalize site picks it up uniformly.
    """

    def test_applies_aliases_from_domain(self) -> None:
        """Aliases declared on the domain are applied to the entity list."""
        domain = MagicMock()
        domain.get_type_aliases.return_value = {"Historical Figure": "Character"}
        entities = [
            {"name": "Buonaparte", "type": "Historical Figure"},
            {"name": "Anna", "type": "Character"},
        ]
        count = apply_domain_type_aliases(entities, domain)

        assert count == 1
        assert entities[0]["type"] == "Character"
        assert entities[0]["properties"]["entity_subtype"] == "Historical Figure"
        assert entities[1]["type"] == "Character"

    def test_no_op_when_domain_is_none(self) -> None:
        """A ``None`` domain (rare; standalone helper fallback) is a no-op."""
        entities = [{"name": "Buonaparte", "type": "Historical Figure"}]
        count = apply_domain_type_aliases(entities, None)

        assert count == 0
        assert entities[0] == {"name": "Buonaparte", "type": "Historical Figure"}

    def test_no_op_when_domain_has_no_accessor(self) -> None:
        """A domain object without ``get_type_aliases`` is a no-op.

        Defensive against older / minimal domain stubs used in tests and
        early plugin shapes that pre-date the accessor.
        """

        class _MinimalDomain:
            name = "minimal"

        entities = [{"name": "Buonaparte", "type": "Historical Figure"}]
        count = apply_domain_type_aliases(entities, _MinimalDomain())

        assert count == 0

    def test_no_op_when_accessor_returns_empty(self) -> None:
        """A domain whose accessor returns ``{}`` performs no rewrite."""
        domain = MagicMock()
        domain.get_type_aliases.return_value = {}
        entities = [{"name": "Buonaparte", "type": "Historical Figure"}]
        count = apply_domain_type_aliases(entities, domain)

        assert count == 0
        assert entities[0]["type"] == "Historical Figure"

    def test_swallows_accessor_exception(self) -> None:
        """A raising accessor doesn't blow up finalization — falls through as no-op.

        Finalization is critical; an aliasing bug must not block commit.
        """
        domain = MagicMock()
        domain.get_type_aliases.side_effect = RuntimeError("boom")
        entities = [{"name": "Buonaparte", "type": "Historical Figure"}]
        count = apply_domain_type_aliases(entities, domain)

        assert count == 0
        assert entities[0]["type"] == "Historical Figure"

    def test_empty_entity_list(self) -> None:
        """Empty entity list returns 0 regardless of domain."""
        domain = MagicMock()
        domain.get_type_aliases.return_value = {"Historical Figure": "Character"}
        count = apply_domain_type_aliases([], domain)

        assert count == 0
