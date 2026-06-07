# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Plugin registry reload service.

Invalidates every cached registry so the next factory call re-runs
``_discover()`` from scratch. Because each registry is cached with a
``(settings_id, database_name)`` key, clearing the whole cache is the
coarse-but-correct behavior -- after reload, the next request pays one
discovery pass and moves on.
"""

from __future__ import annotations

from typing import Any

import structlog

from chaoscypher_core.plugins.factory import invalidate_all_caches


logger = structlog.get_logger(__name__)


def reload_all_plugin_registries() -> dict[str, Any]:
    """Clear all cached plugin registries.

    Returns:
        Dict with ``invalidated`` (list of registry class names whose
        cache was non-empty) and ``total`` (count of cache entries
        cleared across all registries).
    """
    counts = invalidate_all_caches()
    invalidated = [name for name, n in counts.items() if n > 0]
    total = sum(counts.values())
    logger.info(
        "admin_plugins_reload_complete",
        invalidated=invalidated,
        total=total,
    )
    return {"invalidated": invalidated, "total": total}


__all__ = ["reload_all_plugin_registries"]
