# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cache feature: the LLM response and embedding caches over HTTP.

Shows each cache's settings, size and hit history (per call site), clears a
cache's entries while keeping its history, and changes the ``cache``
settings section (enabled flags, size caps) in ``settings.yaml``. The
stores themselves are Core's (:mod:`chaoscypher_core.cache`).

Example:
    from chaoscypher_core.app_config import get_config_manager, get_settings
    from chaoscypher_cortex.features.cache import CacheService

    overview = CacheService(get_settings(), get_config_manager()).overview()
"""

from chaoscypher_cortex.features.cache.api import router
from chaoscypher_cortex.features.cache.models import (
    CacheClearResponse,
    CacheOverviewResponse,
    CacheStatsResponse,
    CacheStoreUpdate,
    CacheUpdateRequest,
    CallSiteStatsResponse,
)
from chaoscypher_cortex.features.cache.service import CacheService


__all__ = [
    "CacheClearResponse",
    "CacheOverviewResponse",
    "CacheService",
    "CacheStatsResponse",
    "CacheStoreUpdate",
    "CacheUpdateRequest",
    "CallSiteStatsResponse",
    "router",
]
