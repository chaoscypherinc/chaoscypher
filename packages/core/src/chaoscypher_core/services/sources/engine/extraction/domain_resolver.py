# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Domain resolver protocol and standalone implementation.

Provides domain-specific configuration for entity deduplication:
title words, type compatibility groups, symmetric relationships,
and inverse relationship mappings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class DomainResolverProtocol(Protocol):
    """Protocol for resolving domain-specific configuration.

    Implemented by :class:`DomainResolver` (standalone) and
    :class:`ExtractionService` (full-stack).
    """

    def get_domain_title_words(self, domain_name: str | None) -> frozenset[str] | None:
        """Get title words for a domain.

        Args:
            domain_name: Name of the domain.

        Returns:
            Frozenset of lowercase title words, or None.

        """
        ...

    def get_domain_type_compatibility(self, domain_name: str | None) -> dict[str, list[str]] | None:
        """Get type compatibility groups for a domain.

        Args:
            domain_name: Name of the domain.

        Returns:
            Dictionary of compatibility groups, or None.

        """
        ...

    def get_domain_symmetric_relationships(self, domain_name: str | None) -> list[str]:
        """Get symmetric relationship types for a domain.

        Args:
            domain_name: Name of the domain.

        Returns:
            List of symmetric relationship type names.

        """
        ...

    def get_domain_inverse_relationships(self, domain_name: str | None) -> dict[str, str]:
        """Get inverse relationship mappings for a domain.

        Args:
            domain_name: Name of the domain.

        Returns:
            Mapping of edge type to inverse edge type.

        """
        ...


class DomainResolver:
    """Standalone domain resolver using DomainRegistry.

    Used by ``extract_entities_from_groups()`` and other standalone
    callers that don't have an ``ExtractionService`` instance.
    """

    def __init__(self, settings: EngineSettings) -> None:
        """Initialize domain resolver.

        Args:
            settings: Settings instance for domain registry lookup.

        """
        self.settings = settings
        self._cache: dict[str, Any] = {}

    def _resolve_domain(self, domain_name: str | None) -> Any | None:
        """Resolve a domain object by name with caching.

        Args:
            domain_name: Name of the domain.

        Returns:
            Domain object or None.

        """
        if not domain_name:
            return None
        if domain_name in self._cache:
            return self._cache[domain_name]
        try:
            from chaoscypher_core.services.sources.engine.extraction.domains import (
                get_domain_registry,
            )

            registry = get_domain_registry(self.settings)
            domain = registry.get_domain(domain_name)
            self._cache[domain_name] = domain
            return domain
        except Exception:
            logger.debug("domain_resolution_failed", domain=domain_name)
            self._cache[domain_name] = None
            return None

    def get_domain_title_words(self, domain_name: str | None) -> frozenset[str] | None:
        """Get title words from domain config.

        Args:
            domain_name: Name of the domain.

        Returns:
            Frozenset of lowercase title words, or None.

        """
        domain = self._resolve_domain(domain_name)
        if not domain:
            return None
        try:
            words = domain.get_title_words()
            return frozenset(w.lower() for w in words) if words else None
        except Exception:
            logger.debug("domain_title_words_unavailable", domain=domain_name)
            return None

    def get_domain_type_compatibility(self, domain_name: str | None) -> dict[str, list[str]] | None:
        """Get type compatibility groups from domain config.

        Args:
            domain_name: Name of the domain.

        Returns:
            Dictionary of compatibility groups, or None.

        """
        domain = self._resolve_domain(domain_name)
        if not domain:
            return None
        try:
            compat = domain.get_type_compatibility()
            return compat if compat else None
        except Exception:
            logger.debug("domain_type_compatibility_unavailable", domain=domain_name)
            return None

    def get_domain_symmetric_relationships(self, domain_name: str | None) -> list[str]:
        """Get symmetric relationship types from domain config.

        Args:
            domain_name: Name of the domain.

        Returns:
            List of symmetric relationship type names.

        """
        domain = self._resolve_domain(domain_name)
        if not domain:
            return []
        try:
            result: list[str] = domain.get_symmetric_relationships()
            return result
        except Exception:
            logger.debug("symmetric_relationships_unavailable", domain=domain_name)
            return []

    def get_domain_inverse_relationships(self, domain_name: str | None) -> dict[str, str]:
        """Get inverse relationship mappings from domain config.

        Args:
            domain_name: Name of the domain.

        Returns:
            Mapping of edge type to inverse edge type.

        """
        domain = self._resolve_domain(domain_name)
        if not domain:
            return {}
        try:
            result: dict[str, str] = domain.get_inverse_relationships()
            return result
        except Exception:
            logger.debug("domain_inverse_relationships_unavailable", domain=domain_name)
            return {}
