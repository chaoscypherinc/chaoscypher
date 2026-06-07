# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LoaderRegistry factory with singleton caching.

Provides a cached factory function for LoaderRegistry instances to avoid
expensive loader discovery on every document import. The registry performs
dynamic module loading and class inspection which is costly (~10-35ms).

Usage:
    from chaoscypher_core.services.sources.loaders import get_loader_registry

    # Get cached registry (creates on first call, reuses thereafter)
    registry = get_loader_registry(settings)

    # Load documents using cached registry
    chunks = registry.load_document('/path/to/file.pdf')

"""

from typing import TYPE_CHECKING

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.loaders.registry import LoaderRegistry
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)

# Module-level cache for registry instances keyed by settings object id
_registry_cache: dict[int, LoaderRegistry] = {}


def get_loader_registry(settings: EngineSettings) -> LoaderRegistry:
    """Get cached LoaderRegistry instance.

    Uses settings-based caching to avoid expensive loader discovery
    on every document import. The registry is cached per unique settings
    configuration (using object identity).

    Args:
        settings: Engine settings for loader configuration.

    Returns:
        Cached LoaderRegistry instance.

    Example:
        >>> from chaoscypher_core.settings import get_engine_settings
        >>> settings = get_engine_settings()
        >>> registry = get_loader_registry(settings)  # First call: creates
        >>> registry2 = get_loader_registry(settings)  # Second call: cached
        >>> registry is registry2
        True

    """
    from chaoscypher_core.services.sources.loaders.registry import LoaderRegistry

    # Use id(settings) as cache key (settings object is typically singleton)
    cache_key = id(settings)

    if cache_key not in _registry_cache:
        logger.info(
            "loader_registry_singleton_created",
            settings_id=cache_key,
        )
        _registry_cache[cache_key] = LoaderRegistry(settings)

    return _registry_cache[cache_key]


__all__ = ["get_loader_registry"]
