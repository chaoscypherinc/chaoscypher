# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared utilities for quality CLI commands.

This module provides common functions used across quality CLI commands
to avoid code duplication.
"""

from typing import Any

import structlog

from chaoscypher_core.services.quality import build_entity_chunk_mentions


logger = structlog.get_logger(__name__)

# ``SqliteAdapter.list_files`` declares ``limit: int = 100`` and applies it as
# a SQL LIMIT, so an omitted argument silently analyses only the newest 100
# sources. Every batch quality command wants the whole source set — pass an
# explicit bulk ceiling. Mirrors the same-named constant in the Cortex quality
# service (which the CLI cannot import across the package boundary).
SOURCE_FETCH_LIMIT = 100_000

__all__ = [
    "SOURCE_FETCH_LIMIT",
    "build_entity_chunk_mentions",
    "get_quality_config",
    "load_source_extraction",
]


def get_quality_config(domain: str | None, database_name: str) -> dict[str, Any]:
    """Load domain-specific quality configuration.

    Args:
        domain: Domain name to load config for, or None.
        database_name: Name of the database.

    Returns:
        Quality scoring configuration dict, or empty dict if not found.
    """
    if not domain:
        return {}

    try:
        from chaoscypher_core.services.sources.engine.extraction.domains import (
            get_domain_registry,
        )

        registry = get_domain_registry(database_name=database_name)
        analyzer = registry.get_domain(domain)
        if analyzer and hasattr(analyzer, "get_quality_scoring"):
            return analyzer.get_quality_scoring()  # type: ignore[no-any-return]
    except Exception:
        logger.debug("failed_to_load_quality_config", domain=domain)
    return {}


def load_source_extraction(
    adapter: Any, source_id: str, database_name: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load a source's extracted entities and relationships.

    Per-source extraction rows live in the dedicated ``source_entities`` /
    ``source_relationships`` tables (the old ``sources.extraction_results``
    JSON column no longer exists), so read them through the adapter's
    table-backed accessors — the same surface Cortex and Neuron score from.

    Args:
        adapter: Storage adapter exposing ``list_source_entities`` /
            ``list_source_relationships``.
        source_id: Source to load.
        database_name: Database scope.

    Returns:
        Tuple of ``(entities, relationships)`` in extraction order.
    """
    entities = adapter.list_source_entities(source_id, database_name)
    relationships = adapter.list_source_relationships(source_id, database_name)
    return entities, relationships
