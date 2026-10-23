# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Domain type inference utilities.

Standalone functions for detecting and resolving content domains from
text chunks and file metadata.  Used by the AIEntityExtractor to select
domain-specific extraction guidance and templates.

Functions:
- detect_domain: Detect (or take as forced) the packs a text extracts with
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

import structlog

from chaoscypher_core.services.sources.engine.extraction.domains import (
    create_domain_sample_text,
    get_domain_registry,
)


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)


class DetectedDomain(NamedTuple):
    """What :func:`detect_domain` returns: the first pack's domain object, its confidence, the packs."""

    domain: Any
    confidence: float
    packs: tuple[str, ...]


def detect_domain(
    chunks: list[str],
    file_info: dict[str, Any],
    settings: EngineSettings,
) -> DetectedDomain:
    """Detect the packs from chunks and file info (or take the forced ones).

    The forced packs come from ``file_info`` (``forced_packs``, else the
    one-name ``forced_domain``: a pack, the packs' label, ``generic``);
    without them the registry proposes the packs from sample text, filename
    and metadata (``detect_extraction_domain``, the queue's rule).

    Args:
        chunks: List of text chunks.
        file_info: File metadata (may include ``forced_packs`` /
            ``forced_domain``, ``filename``, ``metadata``, ``doc_type``,
            ``database_name``).
        settings: Settings instance for domain registry initialization.

    Returns:
        :class:`DetectedDomain`: the first pack's (or the floor's) domain
        object, the confidence (1.0 when forced) and the packs (``()`` the
        floor alone).

    """
    from chaoscypher_core.services.sources.engine.extraction.domains.packs import (
        detected_packs_of,
        forced_packs_of,
    )
    from chaoscypher_core.services.sources.engine.extraction.orchestration import (
        detect_extraction_domain,
    )

    database_name = file_info.get("database_name", "default")
    registry = get_domain_registry(settings, database_name=database_name)

    metadata = dict(file_info.get("metadata", {}) or {})
    if "doc_type" in file_info:
        metadata["doc_type"] = file_info["doc_type"]

    result = detect_extraction_domain(
        registry,
        None,
        create_domain_sample_text(chunks),
        file_info.get("filename", ""),
        metadata,
        forced_packs=forced_packs_of(file_info),
    )
    return DetectedDomain(
        domain=result["domain"],
        confidence=float(result["confidence"]),
        packs=detected_packs_of(result),
    )
