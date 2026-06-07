# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Core entity extraction logic.

Contains the extraction pipeline: AI-powered entity extraction from document
chunks, entity deduplication (exact and semantic), and vector embedding
generation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import structlog

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.engine.deduplication import EntityProcessor
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    AIEntityExtractor,
)
from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
    clean_descriptor_aliases,
    deduplicate_relationships,
    enforce_relationship_limits,
    validate_relationship_type_constraints,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_normalizer import (
    filter_structural_entities,
    normalize_entity_types,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
        FilteringConfig,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteringLog,
    )
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------ #
#  Entity extraction from hierarchical chunk groups
# ------------------------------------------------------------------ #


async def extract_entities_from_groups(
    *,
    hierarchical_groups: list[dict],
    settings: EngineSettings,
    embedding_service: Any,
    file_info: dict[str, Any] | None = None,
    get_domain_structural_filters: Any = lambda _: ([], []),
) -> dict[str, Any]:
    """Extract entities from hierarchical chunk groups using the AI pipeline.

    Uses domain detection for domain-specific extraction guidance and applies
    type normalization post-extraction.

    Args:
        hierarchical_groups: Hierarchical chunk groups from ChunkingService.
        settings: Settings instance (EngineSettings or backend Settings).
        embedding_service: Embedding provider for semantic deduplication.
            Required keyword-only - the default
            ``entity_deduplication_mode`` is "semantic", which silently
            degrades to exact-name dedup when this is None. Pass ``None``
            explicitly only when you intend to disable semantic dedup
            (e.g., a unit test that doesn't exercise the feature).
        file_info: Optional file metadata for domain detection.
        get_domain_structural_filters: Callable that accepts a domain name and
            returns ``(structural_types, generic_types)`` tuple.  Defaults to
            a no-op that skips structural filtering.

    Returns:
        Dictionary with entities, relationships, cached_embeddings,
        chunk_ids, domain, and domain_confidence.

    Raises:
        ValidationError: If ``hierarchical_groups`` is empty or not provided.

    """
    if file_info is None:
        file_info = {}

    if not hierarchical_groups:
        msg = "hierarchical_groups is required - ensure ChunkingService has processed the document"
        raise ValidationError(msg, field="hierarchical_groups")

    logger.info("using_hierarchical_groups", group_count=len(hierarchical_groups))

    # Extract combined content and track chunk IDs
    chunks = [group["combined_content"] for group in hierarchical_groups]
    chunk_ids = [group["small_chunk_ids"] for group in hierarchical_groups]

    # Step 1: Extract entities and relationships (with domain detection)
    ai_extractor = AIEntityExtractor(settings)
    extraction_result = await ai_extractor.extract_from_chunks(chunks, file_info)

    all_entities = extraction_result["entities"]
    all_relationships = extraction_result["relationships"]
    detected_domain = extraction_result.get("domain", "generic")
    domain_confidence = extraction_result.get("domain_confidence", 0.0)
    normalization_rules = extraction_result.get("normalization_rules", {})
    domain_extraction_limits = extraction_result.get("extraction_limits", {}) or {}
    domain_filtering_mode: str | None = extraction_result.get("filtering_mode")
    domain_type_aliases: dict[str, str] = extraction_result.get("type_aliases") or {}

    # Phase 5 (2026-05-18): canonicalize entity types via the domain's
    # type_aliases map BEFORE structural filtering and dedup. This way:
    #   * dedup sees canonical types and merges name variants that were
    #     split across alias types (e.g. ``Historical Figure: Pierre`` +
    #     ``Character: Pierre`` collapse to one node);
    #   * the post-dedup relationship-type validator sees the canonical
    #     types and doesn't trip on rules like
    #     ``interacts_with: source ∈ {Character}``.
    # The original type is preserved on each rewritten entity under
    # ``properties.entity_subtype`` so the refinement signal survives.
    if domain_type_aliases:
        from chaoscypher_core.services.sources.engine.extraction.utils.type_normalizer import (
            apply_type_aliases,
        )

        aliased = apply_type_aliases(all_entities, domain_type_aliases)
        if aliased:
            logger.info(
                "type_aliases_applied_to_extraction",
                domain=detected_domain,
                rewritten=aliased,
                alias_count=len(domain_type_aliases),
            )

    # Step 1b: Filter out structural entities (chapters, sections, etc.)
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteringLog as FilteringLogCls,
    )

    cross_chunk_log = FilteringLogCls()

    # Resolve filtering config for cross-chunk filters
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
        resolve_filtering_config,
    )

    _extraction_cfg = settings.extraction
    _domain_limits = domain_extraction_limits or {}
    _filtering_mode = domain_filtering_mode or getattr(
        _extraction_cfg, "extraction_filtering_mode", "balanced"
    )
    _cross_chunk_config = resolve_filtering_config(
        mode=str(_filtering_mode),
        domain_overrides=dict(_domain_limits) if _domain_limits else None,
    )
    _should_filter_structural = _cross_chunk_config.enable_structural_filter
    if _should_filter_structural:
        structural_types, generic_types = get_domain_structural_filters(detected_domain)
        all_entities, all_relationships, _ = filter_structural_entities(
            all_entities,
            all_relationships,
            structural_entity_types=structural_types,
            filtering_log=cross_chunk_log,
        )
    else:
        _, generic_types = get_domain_structural_filters(detected_domain)

    # Step 2: Deduplicate, remap, resolve names
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        DomainResolver,
    )

    deduplicated, remapped, cached_embeddings, dedup_log_dict = await run_deduplication(
        entities=all_entities,
        relationships=all_relationships,
        detected_domain=detected_domain,
        settings=settings,
        embedding_service=embedding_service,
        domain_extraction_limits=domain_extraction_limits,
        domain_resolver=DomainResolver(settings),
        filtering_config=_cross_chunk_config,
    )

    # Merge structural filter log with dedup log
    cross_chunk_dict = cross_chunk_log.to_dict()
    if dedup_log_dict and dedup_log_dict.get("stages"):
        cross_chunk_dict["stages"].extend(dedup_log_dict["stages"])
        cross_chunk_dict["total_removed"] += dedup_log_dict.get("total_removed", 0)

    # Step 2b: Cross-chunk relationship filtering (Phase 6 reorder).
    # Type-constraint validation and relationship-limit enforcement run
    # AFTER dedup so the filters see consolidated edges on canonical
    # entities. Running these per-chunk before dedup orphaned name-variant
    # entities -- their only edge died in their own chunk before dedup
    # could merge them with the canonical form. See the pipeline-order
    # banner above ``apply_cross_chunk_relationship_filters``.
    edge_type_constraints = extraction_result.get("edge_type_constraints") or None
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteringLog as _FilteringLog,
    )

    _filter_log = _FilteringLog()
    deduplicated, remapped = apply_cross_chunk_relationship_filters(
        entities=deduplicated,
        relationships=remapped,
        edge_type_constraints=edge_type_constraints,
        filtering_config=_cross_chunk_config,
        filtering_log=_filter_log,
    )
    _filter_log_dict = _filter_log.to_dict()
    if _filter_log_dict and _filter_log_dict.get("stages"):
        cross_chunk_dict["stages"].extend(_filter_log_dict["stages"])
        cross_chunk_dict["total_removed"] += _filter_log_dict.get("total_removed", 0)

    # Step 3: Apply domain-specific type normalization
    if normalization_rules:
        deduplicated = normalize_entity_types(
            deduplicated, normalization_rules, generic_types=generic_types
        )
        logger.info(
            "type_normalization_applied",
            domain=detected_domain,
            rule_count=len(normalization_rules),
        )

    logger.info(
        "entity_extraction_complete",
        final_entity_count=len(deduplicated),
        final_relationship_count=len(remapped),
        detected_domain=detected_domain,
        domain_confidence=domain_confidence,
    )

    return {
        "entities": deduplicated,
        "relationships": remapped,
        "cached_embeddings": cached_embeddings,
        "chunk_ids": chunk_ids,
        "domain": detected_domain,
        "domain_confidence": domain_confidence,
        "filtering_log": cross_chunk_dict if cross_chunk_dict.get("total_removed", 0) > 0 else None,
    }


# ------------------------------------------------------------------ #
#  Deduplication pipeline
# ------------------------------------------------------------------ #


def _resolve_symmetric_types(
    domain_name: str | None,
    get_domain_symmetric_relationships: Any | None,
) -> frozenset[str] | None:
    """Resolve symmetric relationship types from domain configuration.

    Symmetric types are relationships where ``(A, B)`` and ``(B, A)`` are
    semantically identical (e.g. ``spouse_of``, ``interacts_with``).  These
    are collapsed during deduplication so only the highest-confidence
    direction is kept.

    Args:
        domain_name: Name of the domain (may be None).
        get_domain_symmetric_relationships: Callable returning a list of
            symmetric type names for the given domain name, or None.

    Returns:
        Frozenset of symmetric type names (lowercased), or None.

    """
    if not domain_name or not get_domain_symmetric_relationships:
        return None
    try:
        types = get_domain_symmetric_relationships(domain_name)
        if not types:
            return None
        return frozenset(t.lower() for t in types)
    except Exception:
        logger.debug("symmetric_types_unavailable", domain=domain_name)
        return None


def _resolve_inverse_map(
    domain_name: str | None,
    get_domain_inverse_relationships: Any | None,
) -> dict[str, str] | None:
    """Resolve inverse relationship mapping from domain configuration.

    Inverse pairs (e.g. ``parent_of``/``child_of``) are collapsed during
    deduplication so only the canonical direction is kept.

    Args:
        domain_name: Name of the domain (may be None).
        get_domain_inverse_relationships: Callable returning a dict mapping
            edge types to their inverses for the given domain, or None.

    Returns:
        Dict mapping type -> inverse_type, or None.

    """
    if not domain_name or not get_domain_inverse_relationships:
        return None
    try:
        inv_map = get_domain_inverse_relationships(domain_name)
        if not inv_map:
            return None
        return cast("dict[str, str]", inv_map)
    except Exception:
        logger.debug("inverse_map_unavailable", domain=domain_name)
        return None


async def run_deduplication(
    *,
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    detected_domain: str | None,
    settings: EngineSettings,
    embedding_service: Any = None,
    domain_resolver: Any = None,
    domain_extraction_limits: dict[str, Any] | None = None,
    filtering_config: Any = None,
    adapter: Any = None,
    source_id: str | None = None,
    database_name: str | None = None,
    precomputed_embeddings: list[list[float]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Any], dict[str, Any]]:
    """Deduplicate entities, remap relationships, resolve names.

    Shared pipeline used by both standalone extraction and distributed
    worker finalization paths.

    Args:
        entities: Raw entities to deduplicate.
        relationships: Raw relationships to remap.
        detected_domain: Domain for title words and context.
        settings: Settings instance.
        embedding_service: Optional embedding provider for semantic dedup.
            When ``None``, semantic deduplication is skipped.
        domain_resolver: Object implementing domain lookup methods
            (``get_domain_title_words``, ``get_domain_type_compatibility``,
            ``get_domain_symmetric_relationships``,
            ``get_domain_inverse_relationships``). When ``None``, a
            :class:`DomainResolver` is auto-created from settings.
        domain_extraction_limits: Optional dict of domain-specific extraction
            limit overrides (e.g., ``semantic_dedup_threshold``). When
            present, takes precedence over ``filtering_config``.
        filtering_config: Optional pre-resolved FilteringConfig. When given
            and the key is absent from ``domain_extraction_limits``,
            ``filtering_config.semantic_dedup_threshold`` drives the
            similarity threshold for semantic dedup. Falls back to
            ``settings.extraction.semantic_dedup_threshold`` when neither
            source provides a value (legacy callers).
        adapter: Optional storage adapter forwarded to
            :meth:`EntityProcessor.deduplicate_entities_semantic` for
            quality counter writes on fallback. When ``None``, no counter
            is written.
        source_id: Source row ID forwarded for quality counter writes.
        database_name: Database name forwarded for quality counter writes.
        precomputed_embeddings: Caller-supplied embedding vectors aligned with
            ``entities`` to avoid re-embedding. Passed through to
            :meth:`EntityProcessor.deduplicate_entities_semantic`. When
            ``None``, the dedup service computes embeddings itself.

    Returns:
        Tuple of (deduplicated_entities, remapped_relationships,
        cached_embeddings, filtering_log_dict).

    """
    if domain_resolver is None:
        from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
            DomainResolver,
        )

        domain_resolver = DomainResolver(settings)

    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteringLog as FilteringLogCls,
    )

    filtering_log: FilteringLogCls = FilteringLogCls()
    title_words = domain_resolver.get_domain_title_words(detected_domain)
    entity_processor = EntityProcessor(
        title_words=title_words,
        max_description_length=settings.source_processing.entity_max_description_length,
        dedup_type_partition_cutoff=settings.extraction.dedup_type_partition_cutoff,
        dedup_no_overlap_boost=settings.extraction.dedup_no_overlap_boost,
        dedup_borderline_penalty=settings.extraction.dedup_borderline_penalty,
    )

    sp = settings.source_processing
    dedup_mode = sp.entity_deduplication_mode
    _domain_limits = domain_extraction_limits or {}
    _extraction_cfg = settings.extraction
    # Threshold precedence (highest first):
    #   1. domain extraction limit ``semantic_dedup_threshold``
    #   2. ``filtering_config.semantic_dedup_threshold`` (slider-driven)
    #   3. ``settings.extraction.semantic_dedup_threshold`` (legacy default)
    if "semantic_dedup_threshold" in _domain_limits:
        dedup_threshold = float(_domain_limits["semantic_dedup_threshold"])
    elif (
        filtering_config is not None
        and getattr(filtering_config, "semantic_dedup_threshold", None) is not None
    ):
        dedup_threshold = float(filtering_config.semantic_dedup_threshold)
    else:
        dedup_threshold = float(_extraction_cfg.semantic_dedup_threshold)
    require_type_compat = sp.dedup_require_type_compatibility
    type_compat_map = sp.dedup_type_compatibility_map

    # Merge domain-specific type compatibility with global settings map
    domain_type_compat = domain_resolver.get_domain_type_compatibility(detected_domain)
    if domain_type_compat:
        type_compat_map = {**type_compat_map, **domain_type_compat}

    cached_embeddings: list[Any] = []

    # Always run exact name dedup first — collapses same-name entities
    # regardless of embedding quality or description differences
    logger.info(
        "deduplication_exact",
        mode="name_and_type",
        entity_count_before=len(entities),
    )
    deduplicated, index_mapping = entity_processor.deduplicate_entities_with_mapping(
        entities,
        require_type_compatibility=require_type_compat,
        type_compatibility_map=type_compat_map,
        filtering_log=filtering_log,
    )
    remapped = entity_processor.remap_relationship_indices(relationships, index_mapping)

    logger.info(
        "exact_dedup_complete",
        entity_count_before=len(entities),
        entity_count_after=len(deduplicated),
    )

    # Then run semantic dedup on survivors — catches synonyms/variants
    # that exact matching misses (e.g., different spellings, abbreviations)
    if dedup_mode == "semantic" and embedding_service is not None:
        logger.info(
            "deduplication_semantic",
            threshold=dedup_threshold,
            entity_count_before=len(deduplicated),
        )
        # Remap precomputed embeddings (parallel to original ``entities``)
        # to the post-exact-dedup ``deduplicated`` list. For each surviving
        # new_idx, take the embedding of any old_idx that mapped to it
        # (merged duplicates share embedding by definition). If any survivor
        # has no mapped embedding (shouldn't happen for a valid mapping),
        # fall back to recompute by passing None.
        remapped_embeddings: list[list[float]] | None = None
        if precomputed_embeddings is not None and len(precomputed_embeddings) == len(entities):
            slots: list[list[float] | None] = [None] * len(deduplicated)
            for old_idx, new_idx in index_mapping.items():
                if new_idx is not None and slots[new_idx] is None:
                    slots[new_idx] = precomputed_embeddings[old_idx]
            if all(s is not None for s in slots):
                remapped_embeddings = [s for s in slots if s is not None]
        (
            deduplicated,
            semantic_mapping,
            cached_embeddings,
        ) = await entity_processor.deduplicate_entities_semantic(
            deduplicated,
            embedding_service,
            dedup_threshold,
            require_type_compatibility=require_type_compat,
            type_compatibility_map=type_compat_map,
            filtering_log=filtering_log,
            adapter=adapter,
            source_id=source_id,
            database_name=database_name,
            precomputed_embeddings=remapped_embeddings,
        )
        remapped = entity_processor.remap_relationship_indices(remapped, semantic_mapping)

    logger.info(
        "deduplication_complete",
        entity_count_before=len(entities),
        entity_count_after=len(deduplicated),
        relationship_count=len(remapped),
    )

    # Hierarchical name resolution
    deduplicated, remapped, _ = entity_processor.resolve_hierarchical_names(deduplicated, remapped)

    logger.info(
        "hierarchical_name_resolution_complete",
        entity_count=len(deduplicated),
        relationship_count=len(remapped),
    )

    # Final cleanup: deduplicate relationships and clean descriptor aliases
    symmetric_types = _resolve_symmetric_types(
        detected_domain, domain_resolver.get_domain_symmetric_relationships
    )
    inverse_map = _resolve_inverse_map(
        detected_domain, domain_resolver.get_domain_inverse_relationships
    )
    remapped = deduplicate_relationships(
        remapped,
        symmetric_types=symmetric_types,
        inverse_map=inverse_map,
        filtering_log=filtering_log,
    )
    removed_alias_count = clean_descriptor_aliases(
        deduplicated, title_words, filtering_log=filtering_log
    )
    if removed_alias_count > 0:
        logger.info("descriptor_aliases_cleaned", removed_count=removed_alias_count)

    return deduplicated, remapped, cached_embeddings, filtering_log.to_dict()


# ------------------------------------------------------------------ #
#  Cross-chunk relationship filtering (post-dedup)
# ------------------------------------------------------------------ #
#
# Pipeline order (Phase 6, May 2026):
#   1. LLM extracts entities + relationships per chunk (`extract_single_chunk`)
#   2. Per-chunk: bounds check, evidence filter (depend on chunk-local sentences)
#   3. Cross-chunk aggregation
#   4. ``run_deduplication`` — exact + semantic name dedup, relationship index
#      remap, hierarchical name resolution, relationship dedup
#   5. ``apply_cross_chunk_relationship_filters`` — type-constraint validation
#      + relationship-limit enforcement on consolidated, canonical entities
#   6. Orphan filter at commit time (commit/service.py:drop_orphan_entities)
#
# Why filters run AFTER dedup:
#   Type-constraint and degree-cap filters look at entity types and
#   relationship distribution across the whole graph. Running them
#   per-chunk on un-dedup'd entities means a name-variant entity (e.g.
#   "Princess Anna Mikháylovna Drubetskáya") loses its only edge before
#   semantic dedup can merge it with its canonical form ("Princess
#   Drubetskáya"). The variant ends up orphaned. Running filters AFTER
#   dedup means the filter sees consolidated edges on the canonical
#   entity, which has the right type and many edges — so type-constraint
#   passes and the degree cap weighs the correct distribution.


def apply_cross_chunk_relationship_filters(
    *,
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    edge_type_constraints: dict[str, dict[str, list[str]]] | None,
    filtering_config: FilteringConfig,
    filtering_log: FilteringLog | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply type-constraint and relationship-limit filters cross-chunk.

    Runs AFTER ``run_deduplication`` so the filters see consolidated
    relationships on canonical entities, not chunk-local fragments. See
    the section banner above for the full pipeline order and rationale.

    Args:
        entities: Deduplicated entities (output of ``run_deduplication``).
        relationships: Remapped relationships (output of ``run_deduplication``).
        edge_type_constraints: Domain edge-type constraints, or None.
        filtering_config: Resolved FilteringConfig controlling which filters
            run and with what thresholds.
        filtering_log: Optional log collector for pipeline diagnostics.

    Returns:
        Tuple of (entities, filtered_relationships). Entity list is
        unchanged — only relationships may be dropped.

    """
    filtered = relationships

    # Type-constraint validation: drop or direction-correct edges whose
    # source/target types don't match the edge template's type whitelist.
    if filtering_config.enable_type_constraints and edge_type_constraints:
        filtered, type_stats = validate_relationship_type_constraints(
            filtered,
            entities,
            edge_type_constraints,
            filtering_log=filtering_log,
            strict_edge_type_constraints=filtering_config.strict_edge_type_constraints,
            enable_direction_correction=filtering_config.enable_direction_correction,
        )
        logger.info(
            "cross_chunk_type_constraints_applied",
            relationship_count_before=len(relationships),
            relationship_count_after=len(filtered),
            **type_stats,
        )

    # Degree cap and total-count cap.
    if filtering_config.enable_relationship_limits:
        filtered, limit_stats = enforce_relationship_limits(
            filtered,
            entity_count=len(entities),
            max_relationship_ratio=filtering_config.max_relationship_ratio,
            max_entity_degree=filtering_config.max_entity_degree,
            max_same_source_type=filtering_config.max_same_source_type,
            entities=entities,
            filtering_log=filtering_log,
            protect_orphans=filtering_config.protect_orphans,
        )
        logger.info(
            "cross_chunk_relationship_limits_applied",
            relationship_count_after=len(filtered),
            **limit_stats,
        )

    return entities, filtered


# ------------------------------------------------------------------ #
#  Embedding generation
# ------------------------------------------------------------------ #


async def generate_embeddings(
    entities: list[dict[str, Any]],
    embedding_service: Any,
    settings: EngineSettings,
    cached_embeddings: list[list[float]] | None = None,
) -> dict[str, Any]:
    """Generate embeddings for entities.

    Args:
        entities: List of normalized entities.
        embedding_service: Embedding provider implementing EmbeddingProviderProtocol.
        settings: Settings instance.
        cached_embeddings: Optional cached embeddings from semantic dedup.

    Returns:
        Dictionary with count, model, dimensions, cached_count, embeddings.

    """
    if not embedding_service or not entities:
        return {"count": 0, "model": "none", "dimensions": 0, "cached_count": 0}

    entity_processor = EntityProcessor(
        max_description_length=settings.source_processing.entity_max_description_length,
        dedup_type_partition_cutoff=settings.extraction.dedup_type_partition_cutoff,
        dedup_no_overlap_boost=settings.extraction.dedup_no_overlap_boost,
        dedup_borderline_penalty=settings.extraction.dedup_borderline_penalty,
    )

    # Check if we can reuse cached embeddings
    if cached_embeddings and len(cached_embeddings) == len(entities):
        logger.info(
            "reusing_cached_embeddings",
            cached_count=len(cached_embeddings),
            source="semantic_deduplication",
        )
        embeddings = cached_embeddings
        cached_count = len(cached_embeddings)
    else:
        # Generate new embeddings
        if cached_embeddings:
            logger.info(
                "cached_embeddings_count_mismatch",
                cached_count=len(cached_embeddings),
                entity_count=len(entities),
                action="regenerating_all_embeddings",
            )

        logger.info("generating_embeddings", entity_count=len(entities))

        # Prepare texts for embedding
        texts = [entity_processor.entity_to_embedding_text(entity) for entity in entities]

        # Generate embeddings using embedding provider
        batch_result = await embedding_service.batch_embed(texts)
        embeddings = batch_result.embeddings

        cached_count = 0

    # Get embedding model info
    embedding_model = get_embedding_model_name(settings)
    embedding_dimensions = len(embeddings[0]) if embeddings else 0

    return {
        "count": len(embeddings),
        "model": embedding_model,
        "dimensions": embedding_dimensions,
        "cached_count": cached_count,
        "embeddings": embeddings,
    }


def get_embedding_model_name(settings: EngineSettings) -> str:
    """Get current embedding model name from settings.

    Args:
        settings: Settings instance with embedding configuration.

    Returns:
        String identifying the embedding model.

    """
    return settings.embedding.model
