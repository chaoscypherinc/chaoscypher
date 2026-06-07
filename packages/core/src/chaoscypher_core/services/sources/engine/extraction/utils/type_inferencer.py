# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Domain type inference utilities.

Standalone functions for detecting and resolving content domains from
text chunks and file metadata.  Used by the AIEntityExtractor to select
domain-specific extraction guidance and templates.

Functions:
- detect_domain: Auto-detect or force-select a content domain
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.sources.engine.extraction.domains import (
    create_domain_sample_text,
    get_domain_registry,
)


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)


def detect_domain(
    chunks: list[str],
    file_info: dict[str, Any],
    settings: EngineSettings,
) -> tuple[Any, float]:
    """Detect the content domain from chunks and file info.

    If ``file_info`` contains a ``forced_domain`` key, the corresponding
    domain is returned directly with confidence 1.0.  Otherwise, the
    domain registry auto-detects the best domain from sample text,
    filename, and metadata.

    Args:
        chunks: List of text chunks.
        file_info: File metadata (may include ``forced_domain``,
            ``filename``, ``metadata``, ``doc_type``, ``database_name``).
        settings: Settings instance for domain registry initialization.

    Returns:
        Tuple of (domain_instance, confidence).

    """
    database_name = file_info.get("database_name", "default")
    registry = get_domain_registry(settings, database_name=database_name)

    # Check for forced domain
    forced_domain_name = file_info.get("forced_domain")
    if forced_domain_name:
        domain = registry.get_domain(forced_domain_name)
        if domain:
            logger.info("using_forced_domain", domain=forced_domain_name)
            return domain, 1.0

    # Auto-detect
    sample_text = create_domain_sample_text(chunks)
    filename = file_info.get("filename", "")
    metadata = file_info.get("metadata", {})

    if "doc_type" in file_info:
        metadata["doc_type"] = file_info["doc_type"]

    return registry.get_best_domain(sample_text, filename, metadata)
