# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source data mappers and transformers.

Helper functions for enriching and transforming source data for API responses.
Extracted from api.py for VSA compliance — business logic belongs in
service/mapper layers, not API endpoints.
"""

from datetime import datetime
from typing import Any

import structlog


logger = structlog.get_logger(__name__)


def calculate_duration(started_at: str | None, completed_at: str | None) -> float | None:
    """Calculate duration in seconds between two ISO timestamps.

    Args:
        started_at: ISO 8601 start timestamp.
        completed_at: ISO 8601 end timestamp.

    Returns:
        Duration in seconds, or None if timestamps are missing/invalid.

    """
    if not started_at or not completed_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        return (end - start).total_seconds()
    except (ValueError, TypeError):  # fmt: skip
        return None


def build_domain_icon_map(database_name: str) -> dict[str, str | None]:
    """Build a mapping of domain name → icon name from the domain registry.

    Args:
        database_name: Current database name for domain config lookup.

    Returns:
        Dict mapping domain names to their icon names.

    """
    try:
        from chaoscypher_core.services.sources.engine.extraction.domains import (
            get_domain_registry,
        )

        registry = get_domain_registry(database_name=database_name)
        return {d["name"]: d.get("icon") for d in registry.list_domain_info()}
    except Exception as e:
        logger.warning("failed_to_load_domain_icons", error=str(e))
        return {}


def enrich_domain_icons(
    sources: list[dict[str, Any]],
    domain_icons: dict[str, str | None],
) -> None:
    """Add extraction_domain_icon to each source dict in-place.

    Args:
        sources: List of source dicts to enrich (modified in-place).
        domain_icons: Mapping of domain name → icon name.

    """
    for source in sources:
        domain = source.get("extraction_domain")
        if domain:
            source["extraction_domain_icon"] = domain_icons.get(domain)


def build_domain_fingerprint_map(database_name: str) -> dict[str, str]:
    """Build ``{domain_name: content_hash}`` from the live domain registry.

    Mirrors ``build_domain_icon_map``. Uses the no-settings registry so the
    hash matches what extraction-finalize stored. Empty dict on any failure.
    """
    try:
        from chaoscypher_core.services.sources.engine.extraction.domains import (
            get_domain_registry,
        )

        registry = get_domain_registry(database_name=database_name)
        return {
            d["name"]: d["content_hash"]
            for d in registry.list_domain_info()
            if d.get("content_hash")
        }
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("failed_to_load_domain_fingerprints", error=str(e))
        return {}


def enrich_domain_changed(
    sources: list[dict[str, Any]],
    fingerprint_map: dict[str, str],
) -> None:
    """Set ``domain_changed_since_extraction`` on each source dict in-place.

    Stale = a stored hash exists AND the domain's live hash exists AND they
    differ. Missing stored hash or missing live plugin → not stale.
    """
    for source in sources:
        stored = source.get("domain_content_hash")
        live = fingerprint_map.get(source.get("extraction_domain") or "")
        source["domain_changed_since_extraction"] = bool(stored and live and stored != live)


def add_duration_fields(source: dict[str, Any]) -> dict[str, Any]:
    """Add calculated duration fields to a source dict.

    Args:
        source: Source dict to enrich (modified in-place).

    Returns:
        The enriched source dict.

    """
    source["indexing_duration_seconds"] = calculate_duration(
        source.get("indexing_started_at"), source.get("indexing_completed_at")
    )
    source["extraction_duration_seconds"] = calculate_duration(
        source.get("extraction_started_at"), source.get("extraction_completed_at")
    )
    source["commit_duration_seconds"] = calculate_duration(
        source.get("commit_started_at"), source.get("commit_completed_at")
    )
    return source


def get_quality_config_for_domain(domain: str | None, database_name: str) -> dict[str, Any]:
    """Load quality_scoring config for a domain.

    Args:
        domain: Domain name to load config for.
        database_name: Current database name.

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
    except Exception as e:
        logger.warning(
            "failed_to_load_domain_quality_config",
            domain=domain,
            error=str(e),
        )
    return {}


def attach_quality_scores(
    entities: list[dict[str, Any]],
    file_info: dict[str, Any],
    database_name: str,
) -> None:
    """Compute and attach quality_score to each entity dict in-place.

    Uses the core QualityScorer to evaluate each entity individually.

    Args:
        entities: List of entity dicts to score (modified in-place).
        file_info: Source file dict containing extraction_domain.
        database_name: Current database name for domain config lookup.

    """
    from chaoscypher_core.services.quality import QualityScorer

    domain = file_info.get("extraction_domain")
    quality_config = get_quality_config_for_domain(domain, database_name)
    scorer = QualityScorer(quality_config)

    for entity in entities:
        chunks = entity.get("source_chunks", []) or entity.get("chunks", [])
        chunk_mentions = len(chunks) if chunks else 1
        score = scorer.score_entity(entity, chunk_mentions=chunk_mentions)
        entity["quality_score"] = round(score.total_score, 1)
