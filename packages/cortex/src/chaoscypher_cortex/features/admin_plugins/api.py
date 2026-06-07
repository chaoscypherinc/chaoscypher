# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Admin plugin management REST API."""

from __future__ import annotations

from fastapi import APIRouter

from chaoscypher_cortex.features.admin_plugins.service import (
    reload_all_plugin_registries,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


router = APIRouter(prefix="/admin/plugins", tags=["admin", "plugins"])


@router.post("/reload")
async def reload_plugins(_: CurrentUsername) -> dict[str, object]:
    """Invalidate all plugin registry caches so the next call rediscovers.

    Requires admin authentication. Returns the list of registry classes
    whose cache had entries and the total number of entries cleared.
    """
    return reload_all_plugin_registries()


__all__ = ["router"]
