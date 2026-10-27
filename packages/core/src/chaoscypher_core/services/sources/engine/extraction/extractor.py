# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Core entity extraction logic.

Contains the extraction pipeline: AI-powered entity extraction from document
chunks, entity deduplication (exact and semantic), and vector embedding
generation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

import structlog

from chaoscypher_core.exceptions import ExtractionIncomplete, ValidationError
from chaoscypher_core.services.sources.engine.deduplication import EntityProcessor
from chaoscypher_core.services.sources.engine.deduplication.service import (
    publish_descriptor_references,
    publish_given_aliases,
    publish_text_referents,
)
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    AIEntityExtractor,
)
from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
    clean_descriptor_aliases,
    deduplicate_relationships,
    enforce_relationship_limits,
    move_relation_descriptors,
    validate_relationship_type_constraints,
)
from chaoscypher_core.services.sources.engine.extraction.utils.post_extraction import (
    apply_structural_and_normalization,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
        FilteringConfig,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteringLog,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.offered_types import (
        OfferedType,
    )
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------ #
#  Entity extraction from hierarchical chunk groups
# ------------------------------------------------------------------ #


def _stamp_evidence(
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    hierarchical_groups: list[dict],
    small_chunks: Sequence[Mapping[str, Any]],
) -> None:
    """Stamp each item's ``evidence`` and ``cited_chunk_id`` from its group, in place.

    The one stamping the queue path's aggregation does
    (:func:`~chaoscypher_core.services.sources.engine.extraction.orchestration.stamp_group_evidence`),
    applied to the items ``extract_from_chunks`` tagged with their group's
    ``chunk_index``. Until 2026-10-20 the SDK path skipped it, so every one of
    its citations was dropped at commit (``citations_skipped_no_chunk_index``).
    """
    from chaoscypher_core.services.sources.engine.extraction.orchestration import (
        group_fallback_chunk_ids,
        stamp_group_evidence,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        split_into_sentences,
    )

    by_group: dict[Any, list[dict[str, Any]]] = {}
    for item in (*entities, *relationships):
        by_group.setdefault(item.get("chunk_index"), []).append(item)
    fallbacks = group_fallback_chunk_ids(hierarchical_groups)
    chunks_by_id = {str(c["id"]): c for c in small_chunks if c.get("id")}
    for index, group in enumerate(hierarchical_groups):
        items = by_group.get(index)
        if not items:
            continue
        text = str(group.get("combined_content") or "")
        stamp_group_evidence(
            items,
            sentences=split_into_sentences(text),
            small_chunk_ids=[str(c) for c in group.get("small_chunk_ids") or []],
            chunks_by_id=chunks_by_id or None,
            fallback_chunk_id=fallbacks[index],
            group_text=text,
        )


async def extract_entities_from_groups(
    *,
    hierarchical_groups: list[dict],
    settings: EngineSettings,
    embedding_service: Any,
    file_info: dict[str, Any] | None = None,
    seed: Sequence[Mapping[str, Any]] = (),
    offered_types: Sequence[OfferedType] = (),
    small_chunks: Sequence[Mapping[str, Any]] = (),
    strict: bool = False,
) -> dict[str, Any]:
    """Extract entities from hierarchical chunk groups using the AI pipeline.

    Uses domain detection for domain-specific extraction guidance and applies
    the shared post-extraction helper
    (``apply_structural_and_normalization``) AFTER dedup, in lockstep with
    the service (``finalize_distributed_extraction``) and worker
    (``_finalize_extraction_inner``) paths — see CANONICAL_PATTERNS §
    Post-extraction filters.

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
        seed: Records the KNOWN ENTITIES list starts with (the memory path).
        offered_types: Templates offered as known types (a scoped memory's, D26);
            empty for documents, whose prompt and templates do not move.
        small_chunks: The stored small chunks the groups are made of (``id``,
            ``content``, ``chunk_metadata``): each extracted item is stamped with
            its evidence and cited chunk, as the queue path's aggregation stamps
            them, so the commit can store its citations. Empty: text-only evidence.
        strict: Raise when the model failed on any group, instead of going on
            with what the other groups gave (a memory's link: a failing model is
            a failed link, not a memory with no facts).

    Returns:
        Dictionary with entities, relationships, cached_embeddings,
        chunk_ids, domain, and domain_confidence.

    Raises:
        ValidationError: If ``hierarchical_groups`` is empty or not provided.
        ExtractionIncomplete: With ``strict``, when the model failed on a group.

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
    extraction_result = await ai_extractor.extract_from_chunks(
        chunks, file_info, seed=seed, offered_types=offered_types
    )
    failed_groups = int(extraction_result.get("chunk_failures") or 0)
    if strict and failed_groups:
        raise ExtractionIncomplete(failed_groups, len(chunks))
    _stamp_evidence(
        extraction_result["entities"],
        extraction_result["relationships"],
        hierarchical_groups,
        small_chunks,
    )

    all_entities = extraction_result["entities"]
    all_relationships = extraction_result["relationships"]
    detected_domain = extraction_result.get("domain", "generic")
    domain_confidence = extraction_result.get("domain_confidence", 0.0)
    normalization_rules = extraction_result.get("normalization_rules", {})
    domain_extraction_limits = extraction_result.get("extraction_limits", {}) or {}
    domain_filtering_mode: str | None = extraction_result.get("filtering_mode")

    # Canonicalize entity types BEFORE structural filtering and dedup: the
    # plan's type vocabulary (the packs' aliases, exact key first, their
    # subtypes and the mapping table) gives every old template name its core
    # type, the subtype kept under ``properties.entity_subtype``, so dedup
    # merges ``Historical Figure: Pierre`` and ``Character: Pierre`` (both
    # Person/character) and the post-dedup relationship-type validator compares
    # core types. The per-chunk rescue already typed what this plan extracted;
    # this is the rewrite the finalize paths run too
    # (``post_extraction.apply_domain_type_aliases``).
    from chaoscypher_core.services.sources.engine.extraction.utils.type_normalizer import (
        canonicalize_entity_types,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
        TypeVocabulary,
    )

    type_vocabulary = TypeVocabulary.from_config(extraction_result.get("type_vocabulary"))
    aliased = canonicalize_entity_types(all_entities, type_vocabulary)
    if aliased:
        logger.info(
            "type_aliases_applied_to_extraction",
            domain=detected_domain,
            rewritten=aliased,
            alias_count=len(type_vocabulary.aliases),
        )

    # The extraction plan's FilteringConfig drives the post-dedup cross-chunk
    # filters, the dedup threshold and the structural-and-normalization
    # helper below -- the same resolution the per-chunk extractor used.
    _cross_chunk_config = extraction_result.get("filtering_config")
    if _cross_chunk_config is None:
        from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
            plan_filtering_config,
        )

        _cross_chunk_config = plan_filtering_config(
            str(domain_filtering_mode or settings.extraction.extraction_filtering_mode),
            domain_extraction_limits or None,
        )

    # Step 2: Deduplicate, remap, resolve names
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        DomainResolver,
    )

    domain_resolver = DomainResolver(settings)
    deduplicated, remapped, cached_embeddings, dedup_log_dict = await run_deduplication(
        entities=all_entities,
        relationships=all_relationships,
        detected_domain=detected_domain,
        settings=settings,
        embedding_service=embedding_service,
        domain_resolver=domain_resolver,
        filtering_config=_cross_chunk_config,
    )

    cross_chunk_dict: dict[str, Any] = (
        dedup_log_dict if dedup_log_dict else {"stages": [], "total_removed": 0}
    )

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

    # Step 3: Filter structural entities + apply domain-specific type
    # normalization POST-dedup via the shared helper, in lockstep with the
    # service (``finalize_distributed_extraction``) and worker
    # (``_finalize_extraction_inner``) paths. Running the structural filter
    # after dedup means a structural entity produced by merging name
    # variants is still filtered; running it before (the old ordering)
    # missed those. Domain resolution mirrors the finalizer's use of the
    # private resolver — the standalone path composes the same chain.
    resolved_domain_obj = domain_resolver._resolve_domain(detected_domain)  # noqa: SLF001 - standalone path composes the same domain resolution chain as the service/worker finalizers
    deduplicated, remapped, structural_filtered = apply_structural_and_normalization(
        deduplicated,
        remapped,
        domain=resolved_domain_obj,
        filtering_config=_cross_chunk_config,
        normalization_rules=normalization_rules,
    )
    if structural_filtered > 0:
        logger.info(
            "structural_entities_filtered_in_standalone",
            removed=structural_filtered,
            remaining=len(deduplicated),
        )
    if normalization_rules:
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
        domain_name: Name of the domain (None is the floor alone: its
            symmetric labels hold for every source).
        get_domain_symmetric_relationships: Callable returning a list of
            symmetric type names for the given domain name, or None.

    Returns:
        Frozenset of symmetric type names (lowercased), or None.

    """
    if not get_domain_symmetric_relationships:
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
        domain_name: Name of the domain (None is the floor alone: its
            inverse pairs hold for every source).
        get_domain_inverse_relationships: Callable returning a dict mapping
            edge types to their inverses for the given domain, or None.

    Returns:
        Dict mapping type -> inverse_type, or None.

    """
    if not get_domain_inverse_relationships:
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
            (``get_domain_name_conventions``,
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
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        domain_name_conventions,
    )

    name_conventions = domain_name_conventions(detected_domain, domain_resolver)
    title_words = name_conventions.titles
    entity_processor = EntityProcessor(
        name_conventions=name_conventions,
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
    # Same-name entities merge only within one core type (``are_types_compatible``).
    require_type_compat = sp.dedup_require_type_compatibility

    cached_embeddings: list[Any] = []

    # An alias naming another person's relation ("Natásha's mother") is a
    # descriptor, never a name: it moves to ``descriptors`` before the name
    # rule reads aliases (the exact dedup's alias keys, the hierarchical
    # pass), not after. On copies: the caller's entities stay as given.
    entities = [dict(e) for e in entities]
    move_relation_descriptors(entities, name_conventions, filtering_log=filtering_log)
    # A bare title whose own sentences write it with a name ("the Emperor Alexander") is
    # given that name, so the name rule reads who it is from its own text (per mention).
    publish_text_referents(entities, name_conventions)

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
    deduplicated, remapped, _ = entity_processor.resolve_hierarchical_names(
        deduplicated, remapped, filtering_log=filtering_log
    )

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
    moved_alias_count = clean_descriptor_aliases(
        deduplicated, title_words, filtering_log=filtering_log, conventions=name_conventions
    )
    if moved_alias_count > 0:
        logger.info("descriptor_aliases_cleaned", moved_count=moved_alias_count)
    # Which aliases were given under each name (not acquired by a merge): the
    # commit stores them so the post-commit resolver reads the same evidence.
    publish_given_aliases(deduplicated)
    # A node still labelled by a reference ("the count") is flagged for a later fold.
    publish_descriptor_references(deduplicated, name_conventions)

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
#   6. Orphan rule at commit time (commit/service.py:flag_orphan_entities): an
#      edgeless entity that carries evidence is kept flagged ``orphan``
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
