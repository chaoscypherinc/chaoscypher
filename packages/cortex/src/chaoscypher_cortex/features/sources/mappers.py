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
    """Add extraction_domain_icon and extraction_pack_icons to each source dict in-place.

    Args:
        sources: List of source dicts to enrich (modified in-place).
        domain_icons: Mapping of domain name → icon name.

    """
    from chaoscypher_core.services.sources.engine.extraction.domains.packs import stored_packs

    for source in sources:
        domain = source.get("extraction_domain")
        packs = stored_packs(source, list_key="extraction_packs", name_key="extraction_domain")
        if packs:
            # One icon per pack, in the order the source stores them; the
            # single-domain field keeps the first pack's (a label has none).
            source["extraction_pack_icons"] = {p: domain_icons.get(p) for p in packs}
        if domain:
            source["extraction_domain_icon"] = domain_icons.get(domain) or (
                domain_icons.get(packs[0]) if packs else None
            )


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

    Stale = a stored hash exists AND the live hash of the packs the source was
    extracted with (``extraction_packs``, else its ``extraction_domain`` label;
    ``stack_content_hash``: one pack's own hash, one over several) exists AND
    they differ. Missing stored hash or missing live plugin → not stale.
    """
    from chaoscypher_core.services.sources.engine.extraction.domains.fingerprint import (
        stack_content_hash,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains.packs import stored_packs

    for source in sources:
        stored = source.get("domain_content_hash")
        # The packs it was extracted with (``extraction_packs``, else the label).
        packs = stored_packs(source, list_key="extraction_packs", name_key="extraction_domain")
        live = stack_content_hash(fingerprint_map, packs) if packs is not None else None
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
    """Load the quality_scoring config of the source's stack (``quality_config_of``).

    Args:
        domain: The source's ``extraction_domain`` (a pack's name or the packs' label).
        database_name: Current database name.

    Returns:
        Quality scoring configuration dict, or empty dict if not found.

    """
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        quality_config_of,
    )

    return quality_config_of(domain, database_name=database_name)


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
    from chaoscypher_core.services.quality import QualityScorer, build_entity_chunk_mentions

    domain = file_info.get("extraction_domain")
    quality_config = get_quality_config_for_domain(domain, database_name)
    scorer = QualityScorer(quality_config)

    # Canonical chunk-mention map (handles the table rows'
    # ``source_chunk_indices`` key as well as the legacy aliases).
    mentions = build_entity_chunk_mentions(entities)
    for idx, entity in enumerate(entities):
        score = scorer.score_entity(entity, chunk_mentions=mentions.get(idx, 1))
        entity["quality_score"] = round(score.total_score, 1)
