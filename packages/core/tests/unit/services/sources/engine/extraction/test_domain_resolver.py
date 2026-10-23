# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the standalone DomainResolver and the floor-plus-packs union it reads (spec 2026-10-07 § 6)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DEFAULT_NAME_CONVENTIONS,
    fold,
)
from chaoscypher_core.services.sources.engine.extraction import domain_resolver
from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
    DomainResolver,
    conventions_of_packs,
    conventions_of_sources,
    domain_name_conventions,
    name_conventions_of_packs,
    registered_name_conventions,
    source_packs,
    sources_packs,
    vocabulary_of_sources,
)
from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
    ConfigurableDomain,
)
from chaoscypher_core.services.sources.engine.extraction.domains.core_types import FLOOR_NAME
from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
    UNIVERSAL_VOCABULARY,
    RelationVocabulary,
    canonical_edge_label,
    domain_relation_config,
)
from chaoscypher_core.settings import EngineSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_resolver() -> DomainResolver:
    """Build a DomainResolver with a minimal settings mock."""
    settings = MagicMock(name="settings")
    return DomainResolver(settings)


def _domain_with(**overrides: object) -> MagicMock:
    """Create a mock pack (``literary``) with overridable return values."""
    domain = MagicMock()
    domain.role = "pack"
    domain.name = "literary"
    domain.get_title_words.return_value = overrides.get("title_words")
    domain.get_symmetric_relationships.return_value = overrides.get("symmetric_relationships", [])
    domain.get_inverse_relationships.return_value = overrides.get("inverse_relationships", {})
    return domain


def _registry_of(pack: object | None) -> MagicMock:
    """A registry holding a bare floor (no title words) and ``pack`` as ``literary``.

    The resolver reads the source's ``DomainStack`` (Task 7), so a lookup
    resolves the floor and the pack.
    """
    floor = MagicMock()
    floor.role = "floor"
    floor.name = FLOOR_NAME
    floor.get_title_words.return_value = []
    registry = MagicMock()
    registry.get_domain.side_effect = {FLOOR_NAME: floor, "literary": pack}.get
    return registry


# ---------------------------------------------------------------------------
# TestDomainResolverNoneHandling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDomainResolverNoneHandling:
    """Tests for None/missing domain name handling."""

    def test_none_domain_name_title_words(self) -> None:
        """None domain name returns None title words without registry lookup."""
        resolver = _make_resolver()
        assert resolver.get_domain_title_words(None) is None

    def test_none_domain_name_reads_the_floor(self) -> None:
        """No domain is the floor alone: its symmetric labels and inverse pairs hold."""
        resolver = DomainResolver(EngineSettings())
        floor = get_domain_registry(EngineSettings()).get_domain(FLOOR_NAME)
        for name in (None, "", FLOOR_NAME):
            assert resolver.get_domain_symmetric_relationships(name) == (
                floor.get_symmetric_relationships()
            )
            assert resolver.get_domain_inverse_relationships(name) == (
                floor.get_inverse_relationships()
            )
            assert resolver.get_domain_name_conventions(name) == _stack().conventions()

    def test_empty_domain_name_has_no_title_words(self) -> None:
        """Empty domain name string is treated the same as None for the raw title words."""
        resolver = _make_resolver()
        assert resolver.get_domain_title_words("") is None


# ---------------------------------------------------------------------------
# TestDomainResolverLookups
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDomainResolverLookups:
    """Tests that verify lookups delegate to the resolved domain object."""

    def _patch_registry(self, domain: object | None):
        """Return a patch context for get_domain_registry."""
        registry = _registry_of(domain)
        return patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=registry,
        )

    def test_title_words_lowercased_and_frozen(self) -> None:
        """Title words are lowercased and returned as a frozenset."""
        domain = _domain_with(title_words=["Mr", "DR", "Lady"])
        resolver = _make_resolver()
        with self._patch_registry(domain):
            result = resolver.get_domain_title_words("literary")
        assert isinstance(result, frozenset)
        assert result == frozenset({"mr", "dr", "lady"})

    def test_title_words_empty_returns_none(self) -> None:
        """Empty title-word list returns None (not an empty frozenset)."""
        domain = _domain_with(title_words=[])
        resolver = _make_resolver()
        with self._patch_registry(domain):
            assert resolver.get_domain_title_words("literary") is None

    def test_a_packs_maps_are_the_floors_and_its_own(self) -> None:
        """Literary keeps the floor's ``parent_of``/``child_of`` pair and adds its ``godparent_of`` pair."""
        resolver = DomainResolver(EngineSettings())
        inverse = resolver.get_domain_inverse_relationships("literary")
        assert inverse["parent_of"] == "child_of"
        assert inverse["godparent_of"] == "godchild_of"
        assert inverse == _stack("literary").inverse_relationships()
        symmetric = resolver.get_domain_symmetric_relationships("literary")
        assert {"spouse_of", "betrothed_to"} <= set(symmetric)
        assert len(symmetric) == len(set(symmetric))


# ---------------------------------------------------------------------------
# TestDomainResolverCaching
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDomainResolverCaching:
    """Tests that repeated lookups reuse the cached domain."""

    def test_domain_lookup_cached_across_calls(self) -> None:
        """Registry is queried once, then cached for subsequent method calls."""
        domain = _domain_with(title_words=["Mr"])
        registry = _registry_of(domain)
        resolver = _make_resolver()
        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=registry,
        ):
            resolver.get_domain_title_words("literary")
            first = registry.get_domain.call_count
            resolver.get_domain_title_words("literary")
        # The stack is resolved once; the second lookup asks the registry nothing
        assert first > 0
        assert registry.get_domain.call_count == first

    def test_the_stack_is_resolved_once_per_name(self) -> None:
        """The conventions, symmetric labels and inverse map of one name share one stack."""
        resolver = DomainResolver(EngineSettings())
        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domain_resolver.domain_stack",
            wraps=domain_resolver.domain_stack,
        ) as spy:
            resolver.get_domain_name_conventions("literary")
            resolver.get_domain_symmetric_relationships("literary")
            resolver.get_domain_inverse_relationships("literary")
        assert spy.call_count == 1


# ---------------------------------------------------------------------------
# TestDomainResolverErrorHandling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDomainResolverErrorHandling:
    """Tests that domain lookup errors degrade gracefully."""

    def test_registry_import_failure_returns_defaults(self) -> None:
        """A registry exception caches None and returns safe defaults."""
        resolver = _make_resolver()
        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            side_effect=RuntimeError("no registry"),
        ):
            assert resolver.get_domain_title_words("literary") is None
            assert resolver.get_domain_symmetric_relationships("literary") == []
            assert resolver.get_domain_inverse_relationships("literary") == {}

    def test_domain_method_raises_returns_safe_default(self) -> None:
        """Exceptions in the domain object methods yield safe defaults."""
        domain = _domain_with()
        domain.get_title_words.side_effect = RuntimeError("boom")
        registry = _registry_of(domain)
        resolver = _make_resolver()
        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=registry,
        ):
            assert resolver.get_domain_title_words("literary") is None

    def test_a_stack_member_that_raises_yields_safe_defaults(self) -> None:
        """A member's table that cannot be read: no maps, no conventions (the universal ones downstream)."""
        resolver = DomainResolver(EngineSettings())
        with (
            patch.object(
                ConfigurableDomain, "get_symmetric_relationships", side_effect=RuntimeError
            ),
            patch.object(ConfigurableDomain, "get_inverse_relationships", side_effect=RuntimeError),
            patch.object(ConfigurableDomain, "get_title_words", side_effect=RuntimeError),
        ):
            assert resolver.get_domain_symmetric_relationships("literary") == []
            assert resolver.get_domain_inverse_relationships("literary") == {}
            assert resolver.get_domain_name_conventions("literary") is None
            assert domain_name_conventions("literary", resolver) == DEFAULT_NAME_CONVENTIONS


# ---------------------------------------------------------------------------
# The union of the floor and the packs (spec 2026-10-07 § 6)
# ---------------------------------------------------------------------------


def _stack(*packs: str) -> DomainStack:
    """The real floor plus ``packs``."""
    return DomainStack.resolve(get_domain_registry(EngineSettings()), list(packs))


def _pack_names() -> list[str]:
    registry = get_domain_registry(EngineSettings())
    return sorted(n for n in registry.names() if registry.get_domain(n).role == "pack")


@pytest.mark.unit
class TestPackUnion:
    """Two packs' conventions and relation tables are the floor's and both packs'."""

    def test_mixed_packs_union_their_conventions(self) -> None:
        """Literary + historical: every title word, gendered title and form of either, over the floor's."""
        both = name_conventions_of_packs(_stack("historical", "literary"))
        literary = name_conventions_of_packs(_stack("literary"))
        historical = name_conventions_of_packs(_stack("historical"))
        assert both is not None and literary is not None and historical is not None
        assert both.titles == literary.titles | historical.titles
        registry = get_domain_registry(EngineSettings())
        for pack in ("historical", "literary"):  # both packs' own title words
            assert {t.lower() for t in registry.get_domain(pack).get_title_words()} <= both.titles
        # each pack's marked person types, which the other lacks
        assert both.person_types == literary.person_types | historical.person_types
        assert {"character", "historical figure"} <= (both.person_types or frozenset())
        assert both.feminine_titles == literary.feminine_titles | historical.feminine_titles
        assert both.masculine_titles == literary.masculine_titles | historical.masculine_titles
        assert both.name_forms == literary.name_forms
        assert both.generation == literary.generation
        assert both.relation_words == literary.relation_words | historical.relation_words
        assert both.title_equivalents == literary.title_equivalents
        assert both.given_name_titles == literary.given_name_titles & historical.given_name_titles
        assert both == _stack("literary", "historical").conventions()

    def test_one_pack_is_the_floor_and_its_own(self) -> None:
        """A pack that marks no person type still reads Person as the floor marks it."""
        floor = name_conventions_of_packs(_stack())
        assert floor is not None and floor.person_types == frozenset({"person"})
        for pack in _pack_names():
            conv = name_conventions_of_packs(_stack(pack))
            assert conv is not None and conv.person_types is not None, pack
            assert floor.titles <= conv.titles, pack

    def test_the_relation_vocabulary_is_every_members(self) -> None:
        """Literary's godparent and patron relations survive beside the floor's in a two-pack stack."""
        config = domain_relation_config(_stack("historical", "literary"))
        assert {"godparent", "patron", "parent", "sibling"} <= set(config)
        assert config == domain_relation_config(_stack("literary", "historical"))
        assert _stack("historical", "literary").relation_vocabulary().as_config() == (
            RelationVocabulary.from_config(config).as_config()
        )

    def test_the_inverse_and_symmetric_maps_are_every_members(self) -> None:
        """The floor's pairs hold in a pack's stack (the journey test's ``parent_of``/``child_of``)."""
        stack = _stack("historical", "literary")
        inverse = stack.inverse_relationships()
        for member in stack.members:
            assert member.get_inverse_relationships().items() <= inverse.items(), member.name
            assert set(member.get_symmetric_relationships()) <= set(stack.symmetric_relationships())

    def test_no_pack_contradicts_the_floor_or_another_pack(self) -> None:
        """The union resolves no conflict because there is none: a label has one inverse, a word one generation.

        A pack only adds to the floor (spec 2026-10-07 § 2). Were a pack to
        give a floor label another inverse, or a generation word another
        generation, the union would keep the floor's (the first member's)
        silently; this fails instead. Nor may a pack give relation words to a
        label the floor marks two-endpoints-only: the union would let one
        named end carry it (literary's ``hosts`` words did until 2026-10-08).
        """
        registry = get_domain_registry(EngineSettings())
        members = [
            registry.get_domain(FLOOR_NAME),
            *(registry.get_domain(p) for p in _pack_names()),
        ]
        inverse: dict[str, tuple[str, str]] = {}
        generation: dict[str, tuple[str, str]] = {}
        floor_two_endpoints = set(members[0].get_two_endpoint_relations())
        assert "hosts" in floor_two_endpoints  # the guard reads the floor's marks
        loosened = {
            (member.name, relation, label)
            for member in members[1:]
            for relation, entry in member.get_relation_vocabulary().items()
            for label in entry["labels"]
            if canonical_edge_label(label) in floor_two_endpoints
        }
        assert loosened == set()
        for member in members:
            # a symmetric label is its own inverse here, so a pack making it one-way fails too
            for label, inv in member.get_inverse_relationships().items():
                seen = inverse.setdefault(label, (inv, member.name))
                assert seen[0] == inv, (label, seen, (inv, member.name))
            for word, gen in member.get_generation_words().items():
                seen = generation.setdefault(fold(word), (gen, member.name))
                assert seen[0] == gen, (word, seen, (gen, member.name))


@pytest.mark.unit
class TestSourcePacks:
    """The storage shim: a source row names one domain until sources carry a list."""

    def test_a_row_names_one_pack_or_none(self) -> None:
        assert source_packs(None) == ()
        assert source_packs({"forced_domain": None, "extraction_domain": None}) == ()
        assert source_packs({"extraction_domain": FLOOR_NAME}) == ()
        assert source_packs({"extraction_domain": "literary"}) == ("literary",)
        assert source_packs({"forced_domain": "legal", "extraction_domain": "literary"}) == (
            "legal",
        )

    def test_rows_union_their_packs(self) -> None:
        rows = [{"forced_domain": "literary"}, {"extraction_domain": "historical"}, None, {}]
        assert sources_packs(rows) == ("historical", "literary")

    def test_the_conventions_of_sources_are_the_union_of_their_packs(self) -> None:
        """Mixed sources read by both packs; no pack is the floor; an unregistered pack is left out."""
        settings = EngineSettings()
        rows = [{"forced_domain": "literary"}, {"forced_domain": "historical"}]
        assert (
            conventions_of_sources(rows, settings) == _stack("historical", "literary").conventions()
        )
        assert conventions_of_sources([{"forced_domain": None}], settings) == _stack().conventions()
        assert conventions_of_packs(["literary", "no-such-pack"], settings) == (
            _stack("literary").conventions()
        )

    def test_the_vocabulary_of_sources_is_their_stacks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The stack the conventions read, read for its relations; None when the packs cannot be built."""
        settings = EngineSettings()
        rows = [{"forced_domain": "literary"}, {"forced_domain": "historical"}]
        assert (
            vocabulary_of_sources(rows, settings)
            == _stack("historical", "literary").relation_vocabulary()
        )
        monkeypatch.setattr(domain_resolver, "packs_stack", lambda packs, settings: None)
        # packs the registry cannot build: None, so the reader records its fault
        assert vocabulary_of_sources(rows, settings) is None
        # no pack listed: the universal vocabulary is the answer, not a failure
        assert vocabulary_of_sources([{"forced_domain": None}], settings) is UNIVERSAL_VOCABULARY


@pytest.mark.unit
class TestRegisteredNameConventions:
    """What the recipes sample: the universal set, the floor, and the floor plus each pack."""

    def test_the_sets_are_the_floor_and_each_pack_not_every_combination(self) -> None:
        sets = registered_name_conventions()
        expected = {
            json.dumps(c.as_dict(), sort_keys=True)
            for c in (
                DEFAULT_NAME_CONVENTIONS,
                _stack().conventions(),
                *(_stack(p).conventions() for p in _pack_names()),
            )
        }
        assert {json.dumps(c.as_dict(), sort_keys=True) for c in sets} == expected
        assert len(sets) == len(expected)
