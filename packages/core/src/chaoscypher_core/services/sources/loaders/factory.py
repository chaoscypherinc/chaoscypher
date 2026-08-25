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

from chaoscypher_core.plugins.factory import register_registry_cache


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.loaders.registry import LoaderRegistry
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)

# Cache for registry instances, keyed by the resolved user-plugin root.
# Using a value-based key — not ``id(settings)`` — so callers that build a
# fresh EngineSettings per request/task still hit the cache instead of
# re-running loader discovery and leaking one dead entry per settings
# object (same fix as the domain registry factory). Registered with the
# plugins factory so invalidate_all_caches (the /admin/plugins/reload
# backend) clears it; mutate in place, never rebind.
_registry_cache: dict[str, LoaderRegistry] = register_registry_cache("LoaderRegistry", {})


def _cache_key(settings: EngineSettings) -> str:
    """Build the registry cache key.

    The only settings field discovery depends on is the user-plugin root,
    which ``LoaderRegistry._get_user_plugins_path`` resolves from a flat
    ``data_dir`` attribute falling back to ``paths.data_dir`` — mirror
    that resolution order here.
    """
    data_dir = getattr(settings, "data_dir", None)
    if data_dir is None:
        data_dir = getattr(getattr(settings, "paths", None), "data_dir", None)
    return "" if data_dir is None else str(data_dir)


def get_loader_registry(settings: EngineSettings) -> LoaderRegistry:
    """Get cached LoaderRegistry instance.

    Uses settings-based caching to avoid expensive loader discovery
    on every document import. The registry is cached per resolved
    user-plugin root (``data_dir``).

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

    cache_key = _cache_key(settings)

    if cache_key not in _registry_cache:
        logger.info(
            "loader_registry_singleton_created",
            data_dir=cache_key,
        )
        _registry_cache[cache_key] = LoaderRegistry(settings)

    return _registry_cache[cache_key]


def clear_loader_registry_cache() -> None:
    """Clear the registry cache.

    Useful for testing or when loaders are added/removed at runtime.
    Mutates in place — the dict is registered with the plugins factory,
    so rebinding would orphan the /admin/plugins/reload wiring.
    """
    _registry_cache.clear()


__all__ = ["clear_loader_registry_cache", "get_loader_registry"]
