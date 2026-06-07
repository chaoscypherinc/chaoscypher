# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared utilities for quality commands.

This module provides common functions used across quality CLI commands
to avoid code duplication.
"""

from typing import Any

import structlog


logger = structlog.get_logger(__name__)


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


def build_entity_chunk_mentions(entities: list[dict]) -> dict[int, int]:
    """Build mapping of entity index to chunk mention count.

    Args:
        entities: List of entity dictionaries from extraction results.

    Returns:
        Dictionary mapping entity index to number of chunk mentions.
    """
    entity_chunk_mentions: dict[int, int] = {}
    for idx, entity in enumerate(entities):
        chunks = entity.get("source_chunks", []) or entity.get("chunks", [])
        entity_chunk_mentions[idx] = len(chunks) if chunks else 1
    return entity_chunk_mentions
