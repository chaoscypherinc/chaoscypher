# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Data transfer objects for the cache API (``/api/v1/cache``).

The update DTO takes its bounds and descriptions from Core's
:class:`~chaoscypher_core.settings.CacheStoreSettings`, so the request
cannot accept a value the settings section would reject.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from chaoscypher_core.settings import CacheStoreSettings


_STORE_FIELDS = CacheStoreSettings.model_fields
_MIN_MAX_BYTES: int = next(
    m.ge for m in _STORE_FIELDS["max_bytes"].metadata if getattr(m, "ge", None) is not None
)
"""The smallest cap ``CacheStoreSettings.max_bytes`` accepts (its own ``ge`` bound)."""


class CallSiteStatsResponse(BaseModel):
    """One call site's history in one cache."""

    hits: int = Field(description="Answers served from the store")
    misses: int = Field(description="Calls that went to the model and were offered to the store")
    bypasses: int = Field(description="Calls the policy kept away from the store")
    latency_saved_ms: int = Field(description="First-call model time of every hit, summed")


class CacheStatsResponse(BaseModel):
    """One cache's settings, size and history."""

    name: str = Field(description="The cache: 'completions' or 'embeddings'")
    enabled: bool = Field(description=_STORE_FIELDS["enabled"].description)
    require_hit: bool = Field(description=_STORE_FIELDS["require_hit"].description)
    entries: int = Field(description="How many entries the cache holds now")
    bytes: int = Field(description="Their total stored size in bytes")
    max_bytes: int = Field(description=_STORE_FIELDS["max_bytes"].description)
    hits: int = Field(description="Answers served from the store")
    misses: int = Field(description="Calls that went to the model and were offered to the store")
    bypasses: int = Field(description="Calls the policy kept away from the store")
    hit_rate: float = Field(description="hits / (hits + misses); 0 before the first cacheable call")
    latency_saved_ms: int = Field(description="First-call model time of every hit, summed")
    by_call_site: dict[str, CallSiteStatsResponse] = Field(
        description="The same history split by the stage that asked"
    )


class CacheOverviewResponse(BaseModel):
    """Every product cache, in display order, and where their files live."""

    dir: str = Field(description="The directory holding the cache files")
    caches: list[CacheStatsResponse] = Field(description="Completions, then embeddings")


class CacheClearResponse(BaseModel):
    """What clearing one cache removed (its stats history is kept)."""

    name: str = Field(description="The cache that was cleared")
    entries_removed: int = Field(description="How many entries were deleted")


class CacheStoreUpdate(BaseModel):
    """A partial update of one cache's settings; omitted fields keep their value."""

    enabled: bool | None = Field(default=None, description=_STORE_FIELDS["enabled"].description)
    max_bytes: int | None = Field(
        default=None, ge=_MIN_MAX_BYTES, description=_STORE_FIELDS["max_bytes"].description
    )

    model_config = ConfigDict(extra="forbid")


class CacheUpdateRequest(BaseModel):
    """Body of ``PATCH /api/v1/cache``: the cache settings to change, per cache."""

    completions: CacheStoreUpdate | None = Field(
        default=None, description="Changes to the completion cache"
    )
    embeddings: CacheStoreUpdate | None = Field(
        default=None, description="Changes to the embedding cache"
    )

    model_config = ConfigDict(extra="forbid")


__all__ = [
    "CacheClearResponse",
    "CacheOverviewResponse",
    "CacheStatsResponse",
    "CacheStoreUpdate",
    "CacheUpdateRequest",
    "CallSiteStatsResponse",
]
