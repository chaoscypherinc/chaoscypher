# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Plugin Registry Factory Utilities.

Provides utilities for creating cached factory functions for plugin registries.
Factories ensure that registry instances are reused efficiently, avoiding
expensive re-discovery on every access.

Caching Strategies:
    - By settings object identity (id(settings))
    - By (settings_id, database_name) tuple
    - Custom cache key functions

Example:
    from chaoscypher_core.plugins import BaseRegistry, create_registry_factory

    class MyRegistry(BaseRegistry[MyPlugin]):
        ...

    # Create cached factory
    get_my_registry = create_registry_factory(MyRegistry)

    # Use factory (caches by settings)
    registry1 = get_my_registry(settings)
    registry2 = get_my_registry(settings)
    assert registry1 is registry2  # Same instance
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.plugins.registry import BaseRegistry
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


# Module-level caches for different registry types, keyed by registry
# class name. Populated by create_registry_factory and by
# register_registry_cache; invalidate_all_caches clears every entry.
_registry_caches: dict[str, dict[Any, Any]] = {}


def register_registry_cache(name: str, cache: dict[Any, Any]) -> dict[Any, Any]:
    """Register a module-owned registry cache for central invalidation.

    Registry factories that manage their own cache dict (rather than going
    through create_registry_factory) call this at module import so that
    invalidate_all_caches — the backend of ``POST /admin/plugins/reload`` —
    actually clears them. The dict is registered by reference: callers must
    mutate it in place (``cache.clear()``), never rebind the module global.

    Args:
        name: Registry class name reported by invalidate_all_caches.
        cache: The cache dict, stored by reference.

    Returns:
        The same dict, so callers can register at assignment site.
    """
    _registry_caches[name] = cache
    return cache


def create_registry_factory[R: "BaseRegistry"](
    registry_class: type[R],
    cache_key_fn: Callable[..., Any] | None = None,
) -> Callable[..., R]:
    """Create a cached factory function for a registry class.

    The factory caches registry instances to avoid expensive re-discovery.
    Cache keys can be customized via cache_key_fn.

    Args:
        registry_class: The registry class to instantiate.
        cache_key_fn: Function to generate cache key from factory args.
            Default: Uses id(settings) if settings provided, else 0.

    Returns:
        Factory function that returns cached registry instances.

    Example:
        class LoaderRegistry(BaseRegistry):
            ...

        get_loader_registry = create_registry_factory(LoaderRegistry)

        # Both calls return same instance
        registry1 = get_loader_registry(settings)
        registry2 = get_loader_registry(settings)
        assert registry1 is registry2
    """
    # Initialize cache for this registry type
    if registry_class.__name__ not in _registry_caches:
        _registry_caches[registry_class.__name__] = {}

    cache = _registry_caches[registry_class.__name__]

    # Default cache key function
    if cache_key_fn is None:
        cache_key_fn = default_cache_key

    def factory(
        settings: EngineSettings | None = None,
        database_name: str = "default",
        **kwargs: Any,
    ) -> R:
        """Get cached registry instance.

        Args:
            settings: Application settings.
            database_name: Database name for per-database registries.
            **kwargs: Additional arguments passed to registry constructor.

        Returns:
            Cached registry instance.
        """
        key = cache_key_fn(settings=settings, database_name=database_name, **kwargs)

        if key not in cache:
            logger.info(
                "registry_factory_creating",
                registry_type=registry_class.__name__,
                cache_key=str(key),
            )
            cache[key] = registry_class(
                settings=settings,
                database_name=database_name,
                **kwargs,
            )

        return cast("R", cache[key])

    # Preserve original function name for debugging
    factory.__name__ = f"get_{registry_class.__name__.lower()}"
    factory.__doc__ = f"""Get cached {registry_class.__name__} instance.

    Args:
        settings: Application settings.
        database_name: Database name for per-database registries.

    Returns:
        Cached {registry_class.__name__} instance.
    """

    def invalidate_cache(
        settings: EngineSettings | None = None,
        database_name: str = "default",
        **kwargs: Any,
    ) -> None:
        """Drop cached registry entries so the next call re-discovers.

        Args:
            settings: When provided, only the entry for this settings
                identity (and database_name) is invalidated. When None
                AND database_name is the default AND no kwargs, the
                entire cache for this registry type is cleared.
            database_name: Database scope for the cache key.
            **kwargs: Forwarded to ``cache_key_fn``.
        """
        if settings is None and database_name == "default" and not kwargs:
            cache.clear()
            logger.info(
                "registry_factory_cache_cleared_all",
                registry_type=registry_class.__name__,
            )
            return

        key = cache_key_fn(settings=settings, database_name=database_name, **kwargs)
        removed = cache.pop(key, None)
        logger.info(
            "registry_factory_cache_cleared",
            registry_type=registry_class.__name__,
            cache_key=str(key),
            removed=removed is not None,
        )

    factory.invalidate_cache = invalidate_cache  # type: ignore[attr-defined]

    return factory


def default_cache_key(
    settings: EngineSettings | None = None,
    database_name: str = "default",
    **kwargs: Any,
) -> tuple[int, str]:
    """Default cache key function using settings ID and database name.

    Args:
        settings: Application settings.
        database_name: Database name.
        **kwargs: Ignored.

    Returns:
        Tuple of (settings_id, database_name).
    """
    settings_id = id(settings) if settings is not None else 0
    return (settings_id, database_name)


def invalidate_all_caches() -> dict[str, int]:
    """Clear every registry-type cache.

    Returns:
        Mapping of registry class name to the number of entries cleared.
    """
    result: dict[str, int] = {}
    for name, cache in _registry_caches.items():
        result[name] = len(cache)
        cache.clear()
    logger.info("registry_factory_all_caches_cleared", counts=result)
    return result


__all__ = [
    "create_registry_factory",
    "default_cache_key",
    "invalidate_all_caches",
    "register_registry_cache",
]
