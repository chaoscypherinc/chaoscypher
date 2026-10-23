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
    """Tests for ``apply_domain_type_aliases``: the finalize sites' one type rewrite.

    It reads the domain's type vocabulary (its ``type_aliases``, its
    subtypes when it is a pack) and the mapping table, and rewrites every
    old type name to its core type, the subtype under ``entity_subtype``.

    Regression context (2026-05-19): a re-import of
    ``war_and_peace_tiny.txt`` after the initial type_aliases commit
    (``f0e243ab1``) showed zero entities had the ``entity_subtype``
    property — the wiring was only on the standalone extractor path,
    not on the production finalizer. This helper consolidates the call
    so every finalize site picks it up uniformly.
    """

    def test_applies_aliases_from_domain(self) -> None:
        """Aliases declared on the domain are applied; core-typed entities are left alone."""
        domain = MagicMock()
        domain.get_type_aliases.return_value = {"Historical Figure": "Character"}
        entities = [
            {"name": "Buonaparte", "type": "Historical Figure"},
            {"name": "Anna", "type": "Person"},
        ]
        count = apply_domain_type_aliases(entities, domain)

        assert count == 1
        assert entities[0]["type"] == "Person"
        assert entities[0]["properties"]["entity_subtype"] == "character"
        assert entities[1] == {"name": "Anna", "type": "Person"}

    def test_reads_the_real_packs_vocabulary(self) -> None:
        """A registered pack's aliases, subtypes and qualified rows (literary, news)."""
        from chaoscypher_core.services.sources.engine.extraction.domains.factory import (
            get_domain_registry,
        )

        registry = get_domain_registry()
        entities = [{"name": "Funke", "type": "Historical Figure"}]
        apply_domain_type_aliases(entities, registry.get_domain("literary"))
        assert entities[0]["properties"]["entity_subtype"] == "character"

        entities = [{"name": "The Times", "type": "Publication"}]
        apply_domain_type_aliases(entities, registry.get_domain("news"))
        assert (entities[0]["type"], entities[0]["properties"]["entity_subtype"]) == (
            "Organization",
            "publication",
        )

    def test_the_mapping_table_applies_when_domain_is_none(self) -> None:
        """A ``None`` domain (rare; standalone helper fallback) still reads the mapping table."""
        entities = [{"name": "Buonaparte", "type": "Historical Figure"}]
        count = apply_domain_type_aliases(entities, None)

        assert count == 1
        assert entities[0]["type"] == "Person"
        assert entities[0]["properties"]["entity_subtype"] == "historical_figure"

    def test_a_domain_without_accessors_contributes_nothing(self) -> None:
        """A domain object without ``get_type_aliases`` adds no alias.

        Defensive against older / minimal domain stubs used in tests and
        early plugin shapes that pre-date the accessor.
        """

        class _MinimalDomain:
            name = "minimal"

        entities = [{"name": "Glorp", "type": "Quux"}]
        count = apply_domain_type_aliases(entities, _MinimalDomain())

        assert count == 0
        assert entities[0] == {"name": "Glorp", "type": "Quux"}

    def test_swallows_accessor_exception(self) -> None:
        """A raising accessor doesn't blow up finalization (the table still applies).

        Finalization is critical; an aliasing bug must not block commit.
        """
        domain = MagicMock()
        domain.get_type_aliases.side_effect = RuntimeError("boom")
        entities = [{"name": "Glorp", "type": "Quux"}, {"name": "Lise", "type": "Character"}]
        count = apply_domain_type_aliases(entities, domain)

        assert count == 1
        assert entities[0]["type"] == "Quux"
        assert entities[1]["type"] == "Person"

    def test_empty_entity_list(self) -> None:
        """Empty entity list returns 0 regardless of domain."""
        domain = MagicMock()
        domain.get_type_aliases.return_value = {"Historical Figure": "Character"}
        count = apply_domain_type_aliases([], domain)

        assert count == 0
