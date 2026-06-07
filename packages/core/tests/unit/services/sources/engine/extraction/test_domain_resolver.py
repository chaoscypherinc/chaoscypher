# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the standalone DomainResolver."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
    DomainResolver,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_resolver() -> DomainResolver:
    """Build a DomainResolver with a minimal settings mock."""
    settings = MagicMock(name="settings")
    return DomainResolver(settings)


def _domain_with(**overrides: object) -> MagicMock:
    """Create a mock domain object with overridable return values."""
    domain = MagicMock()
    domain.get_title_words.return_value = overrides.get("title_words")
    domain.get_type_compatibility.return_value = overrides.get("type_compatibility")
    domain.get_symmetric_relationships.return_value = overrides.get("symmetric_relationships", [])
    domain.get_inverse_relationships.return_value = overrides.get("inverse_relationships", {})
    return domain


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

    def test_none_domain_name_type_compat(self) -> None:
        """None domain name returns None type compatibility without registry lookup."""
        resolver = _make_resolver()
        assert resolver.get_domain_type_compatibility(None) is None

    def test_none_domain_name_symmetric(self) -> None:
        """None domain name returns empty symmetric list."""
        resolver = _make_resolver()
        assert resolver.get_domain_symmetric_relationships(None) == []

    def test_none_domain_name_inverse(self) -> None:
        """None domain name returns empty inverse dict."""
        resolver = _make_resolver()
        assert resolver.get_domain_inverse_relationships(None) == {}

    def test_empty_domain_name_returns_none(self) -> None:
        """Empty domain name string is treated the same as None."""
        resolver = _make_resolver()
        assert resolver.get_domain_title_words("") is None
        assert resolver.get_domain_type_compatibility("") is None
        assert resolver.get_domain_symmetric_relationships("") == []
        assert resolver.get_domain_inverse_relationships("") == {}


# ---------------------------------------------------------------------------
# TestDomainResolverLookups
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDomainResolverLookups:
    """Tests that verify lookups delegate to the resolved domain object."""

    def _patch_registry(self, domain: object | None):
        """Return a patch context for get_domain_registry."""
        registry = MagicMock()
        registry.get_domain.return_value = domain
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

    def test_type_compatibility_returned_as_dict(self) -> None:
        """Type compatibility dict is returned verbatim when non-empty."""
        compat = {"people": ["Person", "Character"]}
        domain = _domain_with(type_compatibility=compat)
        resolver = _make_resolver()
        with self._patch_registry(domain):
            assert resolver.get_domain_type_compatibility("literary") == compat

    def test_type_compatibility_empty_returns_none(self) -> None:
        """Empty type compatibility returns None."""
        domain = _domain_with(type_compatibility={})
        resolver = _make_resolver()
        with self._patch_registry(domain):
            assert resolver.get_domain_type_compatibility("literary") is None

    def test_symmetric_relationships(self) -> None:
        """Symmetric relationships list is passed through unchanged."""
        domain = _domain_with(symmetric_relationships=["spouse_of", "interacts_with"])
        resolver = _make_resolver()
        with self._patch_registry(domain):
            assert resolver.get_domain_symmetric_relationships("literary") == [
                "spouse_of",
                "interacts_with",
            ]

    def test_inverse_relationships(self) -> None:
        """Inverse relationships map is passed through unchanged."""
        inv = {"parent_of": "child_of"}
        domain = _domain_with(inverse_relationships=inv)
        resolver = _make_resolver()
        with self._patch_registry(domain):
            assert resolver.get_domain_inverse_relationships("literary") == inv


# ---------------------------------------------------------------------------
# TestDomainResolverCaching
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDomainResolverCaching:
    """Tests that repeated lookups reuse the cached domain."""

    def test_domain_lookup_cached_across_calls(self) -> None:
        """Registry is queried once, then cached for subsequent method calls."""
        domain = _domain_with(title_words=["Mr"], type_compatibility={"x": ["y"]})
        registry = MagicMock()
        registry.get_domain.return_value = domain
        resolver = _make_resolver()
        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=registry,
        ):
            resolver.get_domain_title_words("literary")
            resolver.get_domain_type_compatibility("literary")
            resolver.get_domain_symmetric_relationships("literary")
        # Only one registry get_domain call across all lookups
        assert registry.get_domain.call_count == 1


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
            assert resolver.get_domain_type_compatibility("literary") is None
            assert resolver.get_domain_symmetric_relationships("literary") == []
            assert resolver.get_domain_inverse_relationships("literary") == {}

    def test_domain_method_raises_returns_safe_default(self) -> None:
        """Exceptions in the domain object methods yield safe defaults."""
        domain = MagicMock()
        domain.get_title_words.side_effect = RuntimeError("boom")
        domain.get_type_compatibility.side_effect = RuntimeError("boom")
        domain.get_symmetric_relationships.side_effect = RuntimeError("boom")
        domain.get_inverse_relationships.side_effect = RuntimeError("boom")
        registry = MagicMock()
        registry.get_domain.return_value = domain
        resolver = _make_resolver()
        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=registry,
        ):
            assert resolver.get_domain_title_words("literary") is None
            assert resolver.get_domain_type_compatibility("literary") is None
            assert resolver.get_domain_symmetric_relationships("literary") == []
            assert resolver.get_domain_inverse_relationships("literary") == {}
