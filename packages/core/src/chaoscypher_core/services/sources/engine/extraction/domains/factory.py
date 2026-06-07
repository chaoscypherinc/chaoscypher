# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Domain Registry Factory.

Provides cached access to the DomainRegistry.
Similar to get_loader_registry() pattern for document loaders.

Example:
    from chaoscypher_core.services.sources.engine.extraction.domains import (
        get_domain_registry,
    )

    registry = get_domain_registry(settings, database_name="research")
    domain, confidence = registry.get_best_domain(text, filename, metadata)

    # Or force a specific domain
    domain = registry.get_domain("technical")
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.domains.registry import (
        DomainRegistry,
    )
    from chaoscypher_core.settings import EngineSettings


# Cache for registry instances, keyed by (data_dir, database_name).
# Using a value-based key — not ``id(settings)`` — so FastAPI handlers
# that receive a fresh Settings instance per request still hit the cache.
# Prior id()-based key caused a full 16-file domain jsonld re-scan on
# every handler invocation.
_registry_cache: dict[tuple[str, str], DomainRegistry] = {}


def _cache_key(settings: EngineSettings | None, database_name: str) -> tuple[str, str]:
    """Build the registry cache key.

    The only settings field the registry depends on is the user-plugin
    root under ``settings.paths.data_dir``; everything else about domain
    discovery is package-relative and settings-independent. Keying on
    ``data_dir`` captures every way the resolved domain set can differ
    across callers without being fooled by per-request Settings
    instances that share all underlying values.
    """
    if settings is None:
        return ("", database_name)
    data_dir = str(getattr(getattr(settings, "paths", None), "data_dir", ""))
    return (data_dir, database_name)


def get_domain_registry(
    settings: EngineSettings | None = None,
    database_name: str = "default",
) -> DomainRegistry:
    """Get cached domain registry.

    Uses singleton pattern - returns the same registry instance
    for the same (data_dir, database_name) combination.

    Args:
        settings: Application settings. If None, uses default key.
        database_name: Database name for per-database domain lookup.

    Returns:
        DomainRegistry instance with auto-discovered domains.
    """
    # Import here to avoid circular imports
    from chaoscypher_core.services.sources.engine.extraction.domains.registry import (
        DomainRegistry,
    )

    cache_key = _cache_key(settings, database_name)

    if cache_key not in _registry_cache:
        _registry_cache[cache_key] = DomainRegistry(settings, database_name)

    return _registry_cache[cache_key]


def clear_domain_registry_cache() -> None:
    """Clear the registry cache.

    Useful for testing or when domains are added/removed at runtime.
    """
    global _registry_cache
    _registry_cache = {}


__all__ = ["clear_domain_registry_cache", "get_domain_registry"]
