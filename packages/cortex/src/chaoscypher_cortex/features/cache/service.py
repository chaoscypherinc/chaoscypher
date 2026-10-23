# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The cache feature's service: read, clear and reconfigure the product caches.

A thin layer over Core's :mod:`chaoscypher_core.cache.wiring` (the stores,
one file per cache) and :class:`~chaoscypher_core.app_config.ConfigManager`
(the ``cache`` section of ``settings.yaml``). Each cache's entries and stats
live in the namespace named after it (``completions``, ``embeddings``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.app_config.engine_factory import build_engine_settings
from chaoscypher_core.cache.wiring import CACHE_NAMES, open_store
from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_cortex.features.cache.models import (
    CacheClearResponse,
    CacheOverviewResponse,
    CacheStatsResponse,
    CacheUpdateRequest,
    CallSiteStatsResponse,
)


if TYPE_CHECKING:
    from chaoscypher_core.app_config import ConfigManager, Settings
    from chaoscypher_core.cache.store import CacheStats
    from chaoscypher_core.cache.wiring import CacheName
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


def hit_rate(stats: CacheStats) -> float:
    """Hits over cacheable calls (hits + misses); bypasses never reached the store."""
    asked = stats.hits + stats.misses
    return stats.hits / asked if asked else 0.0


class CacheService:
    """Stats, clear and settings updates for the completion and embedding caches."""

    def __init__(self, settings: Settings, config_manager: ConfigManager) -> None:
        """Serve the caches these ``settings`` describe; persist updates through ``config_manager``."""
        self._settings = settings
        self._config_manager = config_manager

    def overview(self) -> CacheOverviewResponse:
        """Every cache's settings, size and history, and the directory of their files."""
        engine = build_engine_settings(self._settings)
        return CacheOverviewResponse(
            dir=str(engine.cache.resolved_dir(engine.paths.data_dir)),
            caches=[self._stats(engine, name) for name in CACHE_NAMES],
        )

    def clear(self, name: str) -> CacheClearResponse:
        """Delete every entry of cache ``name``; its stats history is kept.

        Raises:
            NotFoundError: ``name`` is not one of the product caches.
        """
        cache = self._cache_name(name)
        removed = open_store(build_engine_settings(self._settings), cache).clear(cache)
        logger.info("llm_cache_cleared", cache=cache, entries_removed=removed)
        return CacheClearResponse(name=cache, entries_removed=removed)

    async def update(self, patch: CacheUpdateRequest) -> CacheOverviewResponse:
        """Persist ``patch`` to the ``cache`` section of ``settings.yaml`` and return the overview.

        Providers built afterwards read the new section: the Cortex process
        drops its cached providers and the workers are told to reload.
        """
        changes: dict[str, Any] = patch.model_dump(exclude_none=True)
        if changes:
            self._settings = self._config_manager.update_settings({"cache": changes})
            await self._reload_providers()
            logger.info("llm_cache_settings_updated", changes=changes)
        return self.overview()

    @staticmethod
    async def _reload_providers() -> None:
        """Rebuild this process's LLM and embedding providers and notify the workers."""
        from chaoscypher_core.llm_queue.queue_factory import reload_llm_queue_service
        from chaoscypher_core.repo_factories.embedding_factory import (
            invalidate_embedding_service,
        )
        from chaoscypher_cortex.shared.worker_notify import notify_workers_of

        reload_llm_queue_service()
        invalidate_embedding_service()
        await notify_workers_of({"cache"}, "v1:cache_settings_updated")

    @staticmethod
    def _cache_name(name: str) -> CacheName:
        """``name`` as a product cache name.

        Raises:
            NotFoundError: ``name`` is not one of the product caches.
        """
        for cache in CACHE_NAMES:
            if cache == name:
                return cache
        raise NotFoundError("Cache", name)

    @staticmethod
    def _stats(engine: EngineSettings, name: CacheName) -> CacheStatsResponse:
        """One cache's settings section and its store's stats."""
        section = getattr(engine.cache, name)
        stats = open_store(engine, name).stats(name, section.max_bytes)
        return CacheStatsResponse(
            name=name,
            enabled=section.enabled,
            require_hit=section.require_hit,
            entries=stats.entries,
            bytes=stats.bytes,
            max_bytes=stats.max_bytes,
            hits=stats.hits,
            misses=stats.misses,
            bypasses=stats.bypasses,
            hit_rate=hit_rate(stats),
            latency_saved_ms=stats.latency_saved_ms,
            by_call_site={
                site: CallSiteStatsResponse.model_validate(numbers)
                for site, numbers in stats.by_call_site.items()
            },
        )


__all__ = ["CacheService", "hit_rate"]
