# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Entity Processor for Import Orchestrator.

Orchestrates entity deduplication, merging, and relationship remapping for
the source import pipeline. Delegates embedding generation to
``embedding_generator`` and similarity comparison to ``similarity_matcher``.

SRP: Single responsibility for entity processing orchestration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import structlog


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteringLog,
    )

from chaoscypher_core.services.quality.counters import QualityCounter, increment_quality_counter
from chaoscypher_core.services.sources.engine.deduplication.embedding_generator import (
    MAX_EMBEDDING_TEXT_LENGTH,
    entity_to_embedding_text,
    generate_entity_embeddings,
    l2_normalize_embeddings,
)
from chaoscypher_core.services.sources.engine.deduplication.similarity_matcher import (
    are_types_compatible,
    calculate_entity_similarity,
    extract_significant_words,
    normalize_compatibility_map,
    normalize_name_key,
    should_merge_names,
)


logger = structlog.get_logger(__name__)

# Minimal universal title words for callers without domain context
_DEFAULT_TITLE_WORDS: frozenset[str] = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "miss",
        "dr",
        "sir",
        "dame",
        "madam",
        "the",
        "of",
        "de",
        "von",
        "van",
    }
)


class EntityProcessor:
    """Processes entities for import operations.

    Responsibilities:
    - Deduplicate entities (exact and semantic modes)
    - Extract embedding text from entities
    - Prepare entities for storage

    Args:
        title_words: Domain-specific title/honorific words to filter
            during deduplication. Defaults to a minimal universal set.
        max_description_length: Max characters for merged descriptions to
            prevent runaway growth during deduplication. Defaults to 8000.
        dedup_type_partition_cutoff: Minimum entity count to trigger
            type-partitioned comparison. Defaults to 50.
        dedup_no_overlap_boost: Extra similarity required when names share
            no significant words. Defaults to 0.08.
        dedup_borderline_penalty: Confidence penalty for borderline merges
            (within 0.10 of threshold). Defaults to 0.05.
    """

    # Re-export embedding text limit from embedding_generator for convenience
    MAX_EMBEDDING_TEXT_LENGTH = MAX_EMBEDDING_TEXT_LENGTH

    def __init__(
        self,
        title_words: frozenset[str] | None = None,
        max_description_length: int = 8000,
        dedup_type_partition_cutoff: int = 50,
        dedup_no_overlap_boost: float = 0.08,
        dedup_borderline_penalty: float = 0.05,
    ) -> None:
        """Initialize entity processor.

        Args:
            title_words: Domain-specific title/honorific words to filter
                during deduplication. Defaults to a minimal universal set.
            max_description_length: Max characters for merged entity
                descriptions. Callers pass settings.source_processing.entity_max_description_length.
            dedup_type_partition_cutoff: Entity count above which type-partitioned
                semantic comparison is used instead of the full O(n²) matrix.
                Callers pass settings.extraction.dedup_type_partition_cutoff.
            dedup_no_overlap_boost: Extra similarity required for a merge when
                the two entity names share no significant words.
                Callers pass settings.extraction.dedup_no_overlap_boost.
            dedup_borderline_penalty: Confidence penalty applied to entities
                merged within 0.10 of the similarity threshold.
                Callers pass settings.extraction.dedup_borderline_penalty.
        """
        self._title_words = title_words if title_words is not None else _DEFAULT_TITLE_WORDS
        self._max_description_length = max_description_length
        self._dedup_type_partition_cutoff = dedup_type_partition_cutoff
        self._dedup_no_overlap_boost = dedup_no_overlap_boost
        self._dedup_borderline_penalty = dedup_borderline_penalty

    async def deduplicate_entities_semantic(
        self,
        entities: list[dict],
        embedding_service: Any,
        similarity_threshold: float = 0.95,
        require_type_compatibility: bool = False,
        type_compatibility_map: dict[str, list[str]] | None = None,
        filtering_log: FilteringLog | None = None,
        *,
        adapter: Any = None,
        source_id: str | None = None,
        database_name: str | None = None,
        precomputed_embeddings: list[list[float]] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[int, int | None], list[list[float]]]:
        """Remove duplicate entities using semantic similarity (embeddings).

        This is more sophisticated than exact matching - it detects entities
        that refer to the same thing even with different wording.

        Args:
            entities: List of entity dictionaries.
            embedding_service: Embedding provider implementing EmbeddingProviderProtocol.
            similarity_threshold: Cosine similarity threshold (0.0-1.0).
            require_type_compatibility: When True, skip merging entities
                with incompatible types.
            type_compatibility_map: Custom type compatibility groups.
            filtering_log: Optional log collector for pipeline diagnostics.
            adapter: Optional storage adapter for quality counter writes.
                When ``None``, counter increments are skipped.
            source_id: Source row ID for quality counter writes. When
                ``None``, counter increments are skipped.
            database_name: Database name for quality counter writes.
                Defaults to ``"default"`` when ``None``.
            precomputed_embeddings: Caller-supplied embedding vectors aligned
                with ``entities``. When provided, the embedding step is
                skipped; when ``None``, the service embeds entities via
                ``embedding_service``.

        Returns:
            Tuple of (unique_entities, index_mapping, unique_embeddings):
                - unique_entities: List of deduplicated entities.
                - index_mapping: Dict mapping old_index -> new_index (or None
                  if removed).
                - unique_embeddings: Embeddings for unique entities (to avoid
                  regenerating).

        """
        if not entities:
            return [], {}, []

        try:
            # Cache hit: precomputed embeddings parallel to entities means
            # the chunk handler (or finalize backfill) already paid the
            # embedding cost. Skip the batch_embed call entirely.
            if precomputed_embeddings is not None and len(precomputed_embeddings) == len(entities):
                logger.info(
                    "embeddings_reused_from_cache",
                    entity_count=len(entities),
                    source_id=source_id,
                )
                embeddings = precomputed_embeddings
                embeddings_normalized = l2_normalize_embeddings(embeddings)
            else:
                # Generate embeddings for all entities
                entity_texts = [entity_to_embedding_text(entity) for entity in entities]
                embeddings, embeddings_normalized = await generate_entity_embeddings(
                    entity_texts, embedding_service
                )

            # Find duplicates using similarity matrix
            unique_entities, unique_embeddings, index_mapping = self._find_semantic_duplicates(
                entities,
                embeddings,
                embeddings_normalized,
                similarity_threshold,
                require_type_compatibility=require_type_compatibility,
                type_compatibility_map=type_compatibility_map,
            )

            logger.info("returning_embeddings_for_reuse", embedding_count=len(unique_embeddings))

            # Persist embeddings into entity dicts so they travel with
            # the entities through the pipeline (hierarchical merge,
            # normalization, commit) without relying on a separate list
            # that can fall out of sync after index-changing steps.
            for i, entity in enumerate(unique_entities):
                if i < len(unique_embeddings) and unique_embeddings[i]:
                    entity["embedding"] = unique_embeddings[i]

            # Record semantic dedup removals
            removed_count = len(entities) - len(unique_entities)
            if filtering_log is not None and removed_count > 0:
                from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
                    FilteredItem,
                )

                removed_items: list[FilteredItem] = []
                merged_into: dict[int, list[int]] = {}
                for old_idx, new_idx in index_mapping.items():
                    if new_idx is not None and old_idx != new_idx:
                        merged_into.setdefault(new_idx, []).append(old_idx)

                for new_idx, old_indices in merged_into.items():
                    target_name = unique_entities[new_idx].get("name", "?")
                    removed_items.extend(
                        FilteredItem(
                            item_type="entity",
                            name=entities[old_idx].get("name", "?"),
                            entity_type=entities[old_idx].get("type", "?"),
                            reason=f"Semantic match — merged into '{target_name}'",
                        )
                        for old_idx in old_indices
                    )

                filtering_log.add_stage(
                    "semantic_entity_dedup",
                    input_count=len(entities),
                    removed_count=removed_count,
                    items=removed_items,
                )

            return unique_entities, index_mapping, unique_embeddings

        except Exception as e:
            logger.warning("semantic_deduplication_failed_fallback", error_message=str(e))
            if adapter is not None and source_id is not None:
                await increment_quality_counter(
                    adapter=adapter,
                    source_id=source_id,
                    database_name=database_name or "default",
                    counter=QualityCounter.SEMANTIC_DEDUP_FALLBACKS,
                )
            # Fallback to exact matching (no embeddings available)
            unique_entities_res, index_mapping_res = self.deduplicate_entities_with_mapping(
                entities,
                require_type_compatibility=require_type_compatibility,
                type_compatibility_map=type_compatibility_map,
                filtering_log=filtering_log,
            )
            return unique_entities_res, index_mapping_res, []  # Empty embeddings list

    def _find_semantic_duplicates(
        self,
        entities: list[dict],
        embeddings: list[list[float]],
        embeddings_normalized: np.ndarray[Any, np.dtype[np.floating[Any]]],
        similarity_threshold: float,
        require_type_compatibility: bool = False,
        type_compatibility_map: dict[str, list[str]] | None = None,
    ) -> tuple[list[dict[str, Any]], list[list[float]], dict[int, int | None]]:
        """Identify and merge semantic duplicates using precomputed embeddings.

        When type compatibility is required, partitions entities by compatible
        type groups and only computes similarity within each group. This reduces
        comparisons by 80-90% for typical documents with many entity types.

        Args:
            entities: Original entity list.
            embeddings: Raw embedding vectors (parallel to entities).
            embeddings_normalized: L2-normalized embedding matrix.
            similarity_threshold: Minimum adjusted similarity to merge.
            require_type_compatibility: When True, skip merging entities
                with incompatible types.
            type_compatibility_map: Custom type compatibility groups.

        Returns:
            Tuple of (unique_entities, unique_embeddings, index_mapping).

        """
        # Build type-compatible comparison groups to avoid O(n^2) full matrix
        if require_type_compatibility and len(entities) >= self._dedup_type_partition_cutoff:
            comparison_groups = self._build_type_groups(entities, type_compatibility_map)
            logger.info(
                "semantic_dedup_partitioned",
                entity_count=len(entities),
                group_count=len(comparison_groups),
                group_sizes=[len(g) for g in comparison_groups],
            )
        else:
            # Single group containing all indices
            comparison_groups = [list(range(len(entities)))]

        # Pre-normalize compatibility map once for the inner loop
        normalized_compat = normalize_compatibility_map(type_compatibility_map)

        # Pre-normalize all entity names/aliases ONCE to avoid redundant
        # normalize_name_key() calls in the O(n^2) comparison loop.
        precomputed_names: list[tuple[str, frozenset[str]]] = []
        for entity in entities:
            name_key = normalize_name_key(entity.get("name") or entity.get("label", ""))
            alias_keys = frozenset(normalize_name_key(a) for a in entity.get("aliases", []) if a)
            precomputed_names.append((name_key, alias_keys))

        unique_entities: list[dict[str, Any]] = []
        unique_embeddings: list[list[float]] = []
        index_mapping: dict[int, int | None] = {}
        seen_indices: set[int] = set()
        max_similarities: list[float] = []

        for group_indices in comparison_groups:
            for i in group_indices:
                if i in seen_indices:
                    continue

                entity = entities[i]

                # This entity will be kept
                new_idx = len(unique_entities)
                unique_entities.append(entity)
                unique_embeddings.append(embeddings[i])
                index_mapping[i] = new_idx
                seen_indices.add(i)

                # Only compare against entities in the same type group
                candidates = [j for j in group_indices if j > i and j not in seen_indices]
                if not candidates:
                    continue

                # Compute similarities only against candidates in this group
                candidate_embeddings = embeddings_normalized[candidates]
                similarities_subset = np.dot(embeddings_normalized[i], candidate_embeddings.T)

                # Track max similarity
                if len(similarities_subset) > 0:
                    max_similarities.append(float(np.max(similarities_subset)))

                for pos, j in enumerate(candidates):
                    if j in seen_indices:
                        continue

                    base_sim = float(similarities_subset[pos])

                    # Calculate adjusted similarity with precomputed name keys
                    adjusted_similarity = calculate_entity_similarity(
                        entity,
                        entities[j],
                        base_sim,
                        precomputed_a=precomputed_names[i],
                        precomputed_b=precomputed_names[j],
                    )

                    # Log near-threshold similarities for debugging
                    if adjusted_similarity >= 0.85 or base_sim >= 0.85:
                        logger.debug(
                            "similarity_check",
                            entity1_name=entity.get("name"),
                            entity2_name=entities[j].get("name"),
                            base_similarity=round(base_sim, 3),
                            adjusted_similarity=round(adjusted_similarity, 3),
                            threshold=similarity_threshold,
                        )

                    if adjusted_similarity >= similarity_threshold:
                        # Require stronger embedding match when names don't
                        # overlap at all — prevents merging semantically
                        # similar but distinct entities (e.g., two Italian
                        # cities whose embeddings are close).
                        no_name_bonus = adjusted_similarity <= base_sim
                        if no_name_bonus:
                            boosted_threshold = similarity_threshold + self._dedup_no_overlap_boost
                            if adjusted_similarity < boosted_threshold:
                                logger.debug(
                                    "semantic_merge_skipped_no_name_overlap",
                                    entity1_name=entity.get("name"),
                                    entity2_name=entities[j].get("name"),
                                    similarity=round(adjusted_similarity, 3),
                                    boosted_threshold=round(boosted_threshold, 3),
                                )
                                continue

                        # Type compatibility check (redundant when partitioned,
                        # but kept for correctness when not partitioned)
                        if require_type_compatibility and not are_types_compatible(
                            entity.get("type", ""),
                            entities[j].get("type", ""),
                            _normalized_map=normalized_compat,
                        ):
                            logger.debug(
                                "semantic_merge_skipped_type_incompatible",
                                entity1_name=entity.get("name"),
                                entity2_name=entities[j].get("name"),
                                type1=entity.get("type"),
                                type2=entities[j].get("type"),
                                similarity=round(adjusted_similarity, 3),
                            )
                            continue
                        # Mark as duplicate - map to the first occurrence
                        index_mapping[j] = new_idx
                        seen_indices.add(j)
                        # Merge entities: combine aliases, keep best data
                        # Apply confidence penalty for borderline merges
                        confidence_penalty = (
                            self._dedup_borderline_penalty
                            if adjusted_similarity - similarity_threshold < 0.10
                            else 0.0
                        )
                        merged = self.merge_entities(
                            entity,
                            entities[j],
                            confidence_penalty=confidence_penalty,
                        )
                        unique_entities[new_idx] = merged
                        logger.info(
                            "semantic_duplicate_removed",
                            duplicate_entity=entities[j].get("name"),
                            kept_entity=entity.get("name"),
                            base_similarity=round(base_sim, 3),
                            adjusted_similarity=round(adjusted_similarity, 3),
                            merged_aliases=merged.get("aliases", []),
                        )

        removed_count = len(entities) - len(unique_entities)
        self._log_deduplication_summary(
            max_similarities, removed_count, len(unique_entities), similarity_threshold
        )

        return unique_entities, unique_embeddings, index_mapping

    @staticmethod
    def _build_type_groups(
        entities: list[dict[str, Any]],
        type_compatibility_map: dict[str, list[str]] | None = None,
    ) -> list[list[int]]:
        """Partition entity indices into groups of compatible types.

        Entities with compatible types (same type, generic type, or in the same
        compatibility group) are placed together. Each entity appears in exactly
        one group.

        Args:
            entities: Entity list.
            type_compatibility_map: Custom type compatibility groups.

        Returns:
            List of index lists, one per compatible type group.

        """
        # Assign each entity to a canonical type key
        type_to_indices: dict[str, list[int]] = {}
        for idx, entity in enumerate(entities):
            entity_type = (entity.get("type") or "unknown").strip().lower()
            type_to_indices.setdefault(entity_type, []).append(idx)

        # Merge groups that are compatible according to the map
        if type_compatibility_map:
            # Build a union-find of type names
            canonical: dict[str, str] = {}
            for group_types in type_compatibility_map.values():
                group_lower = [t.lower() for t in group_types]
                root = group_lower[0]
                for t in group_lower:
                    canonical[t] = root

            merged_groups: dict[str, list[int]] = {}
            for entity_type, indices in type_to_indices.items():
                root = canonical.get(entity_type, entity_type)
                merged_groups.setdefault(root, []).extend(indices)
            return list(merged_groups.values())

        return list(type_to_indices.values())

    @staticmethod
    def _log_deduplication_summary(
        max_similarities: list[float],
        removed_count: int,
        kept_count: int,
        similarity_threshold: float,
    ) -> None:
        """Log summary statistics for semantic deduplication run.

        Args:
            max_similarities: Per-entity max similarity to nearest neighbour.
            removed_count: Number of entities removed as duplicates.
            kept_count: Number of unique entities retained.
            similarity_threshold: Threshold used for this run.

        """
        if max_similarities:
            avg_max_sim = np.mean(max_similarities)
            max_sim_overall = max(max_similarities)
            logger.info(
                "semantic_deduplication_summary",
                threshold=similarity_threshold,
                removed_count=removed_count,
                kept_count=kept_count,
                max_similarity_found=round(max_sim_overall, 3),
                avg_max_similarity=round(avg_max_sim, 3),
            )
            if removed_count == 0 and max_sim_overall < similarity_threshold:
                suggested_threshold = round(max(0.70, max_sim_overall - 0.05), 2)
                logger.info(
                    "semantic_deduplication_threshold_suggestion",
                    suggested_threshold=suggested_threshold,
                    current_threshold=similarity_threshold,
                    reason="no_duplicates_removed",
                )
        elif removed_count > 0:
            logger.info(
                "semantic_deduplication_completed",
                removed_count=removed_count,
                threshold=similarity_threshold,
            )

    def deduplicate_entities_with_mapping(
        self,
        entities: list[dict],
        require_type_compatibility: bool = False,
        type_compatibility_map: dict[str, list[str]] | None = None,
        filtering_log: FilteringLog | None = None,
    ) -> tuple[list[dict[str, Any]], dict[int, int | None]]:
        """Remove duplicate entities and return index mapping for relationship remapping.

        Deduplication is NAME-ONLY (case-insensitive, trimmed) by default. When
        ``require_type_compatibility`` is True, same-name entities with
        incompatible types are kept separate (e.g. "Paris" the Person vs
        "Paris" the Location).

        When duplicates are found, entities are FULLY MERGED using merge_entities()
        to preserve all metadata (properties, descriptions, aliases, etc.) from
        all occurrences across different chunks.

        Args:
            entities: List of entity dictionaries.
            require_type_compatibility: When True, only merge same-name
                entities whose types are compatible.
            type_compatibility_map: Custom type compatibility groups.
            filtering_log: Optional log collector for pipeline diagnostics.

        Returns:
            Tuple of (unique_entities, index_mapping):
                - unique_entities: List of deduplicated entities with merged
                  metadata.
                - index_mapping: Dict mapping old_index -> new_index (or None
                  if removed).

        """
        # Pre-normalize compatibility map once for the loop
        normalized_compat = normalize_compatibility_map(type_compatibility_map)

        seen: dict[str, int] = {}  # name_key or alias_key -> unique_entities index
        seen_name_keys: set[str] = set()  # tracks which keys are entity names (not aliases)
        unique_entities: list[dict[str, Any]] = []
        index_mapping: dict[int, int | None] = {}  # old_index -> new_index
        merge_count = 0

        for old_idx, entity in enumerate(entities):
            # Create unique key from name (type-agnostic)
            name = entity.get("name") or entity.get("label", "")
            name_key = normalize_name_key(name) if name else ""

            # Collect normalized alias keys for this entity
            entity_aliases = entity.get("aliases") or []
            alias_keys = []
            for alias in entity_aliases:
                if alias:
                    ak = normalize_name_key(alias)
                    if ak and ak != name_key:
                        alias_keys.append(ak)

            # Check for match: name in seen (catches name→name and name→alias),
            # then aliases against name keys only (catches alias→name, NOT alias→alias)
            match_idx: int | None = None
            if name_key and name_key in seen:
                match_idx = seen[name_key]
            else:
                # Check if any of this entity's aliases match an existing entity's NAME
                for ak in alias_keys:
                    if ak in seen_name_keys:
                        match_idx = seen[ak]
                        logger.info(
                            "alias_matched_existing_entity",
                            new_entity=name,
                            matched_alias=ak,
                            matched_entity=unique_entities[match_idx].get("name"),
                        )
                        break

            if match_idx is not None:
                # Type compatibility check (same logic as before)
                if require_type_compatibility:
                    existing_type = unique_entities[match_idx].get("type", "")
                    new_type = entity.get("type", "")
                    if not are_types_compatible(
                        existing_type, new_type, _normalized_map=normalized_compat
                    ):
                        type_key = f"{name_key}::{new_type.strip().lower()}"
                        if type_key not in seen:
                            new_idx = len(unique_entities)
                            unique_entities.append(entity.copy())
                            seen[type_key] = new_idx
                            index_mapping[old_idx] = new_idx
                            # Register aliases for the new separate entity
                            for ak in alias_keys:
                                if ak not in seen:
                                    seen[ak] = new_idx
                            logger.info(
                                "type_incompatible_entity_kept_separate",
                                name=name,
                                existing_type=existing_type,
                                new_type=new_type,
                            )
                            continue
                        merge_idx = seen[type_key]
                        unique_entities[merge_idx] = self.merge_entities(
                            unique_entities[merge_idx], entity
                        )
                        index_mapping[old_idx] = merge_idx
                        merge_count += 1
                        continue

                # Duplicate found: merge into the matched entity
                index_mapping[old_idx] = match_idx
                first_entity = unique_entities[match_idx]
                merged = self.merge_entities(first_entity, entity)
                unique_entities[match_idx] = merged
                merge_count += 1

                # Register any new aliases from the merged entity
                merged_aliases = merged.get("aliases") or []
                merged_name_key = normalize_name_key(merged.get("name") or "")
                for alias in merged_aliases:
                    if alias:
                        ak = normalize_name_key(alias)
                        if ak and ak != merged_name_key and ak not in seen:
                            seen[ak] = match_idx

            elif name_key:
                # First occurrence: register name and aliases
                new_idx = len(unique_entities)
                unique_entities.append(entity.copy())
                seen[name_key] = new_idx
                seen_name_keys.add(name_key)
                index_mapping[old_idx] = new_idx

                # Register alias keys (skip if they collide with a different entity's key)
                for ak in alias_keys:
                    if ak not in seen:
                        seen[ak] = new_idx
            else:
                # Empty name: mark as removed (no mapping)
                index_mapping[old_idx] = None

        removed_count = len(entities) - len(unique_entities)
        if removed_count > 0:
            logger.info(
                "exact_duplicates_removed_with_mapping",
                removed_count=removed_count,
                merge_count=merge_count,
            )

        if filtering_log is not None and removed_count > 0:
            from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
                FilteredItem,
            )

            # Build reverse mapping: new_idx -> list of old_idx that merged into it
            merged_into: dict[int, list[int]] = {}
            for old_idx, new_idx_or_none in index_mapping.items():
                if new_idx_or_none is not None and old_idx != new_idx_or_none:
                    merged_into.setdefault(new_idx_or_none, []).append(old_idx)

            removed_items: list[FilteredItem] = []
            for new_idx, old_indices in merged_into.items():
                target_name = unique_entities[new_idx].get("name", "?")
                for old_idx in old_indices:
                    src_name = entities[old_idx].get("name", "?")
                    removed_items.append(
                        FilteredItem(
                            item_type="entity",
                            name=src_name,
                            entity_type=entities[old_idx].get("type", "?"),
                            reason=f"Exact name match — merged into '{target_name}'",
                        )
                    )

            filtering_log.add_stage(
                "exact_entity_dedup",
                input_count=len(entities),
                removed_count=removed_count,
                items=removed_items,
            )

        return unique_entities, index_mapping

    @staticmethod
    def remap_relationship_indices(
        relationships: list[dict[str, Any]], index_mapping: dict[int, int | None]
    ) -> list[dict[str, Any]]:
        """Remap relationship indices after entity deduplication.

        Args:
            relationships: List of relationships with source/target indices.
            index_mapping: Mapping from old entity indices to new indices.

        Returns:
            List of relationships with remapped indices (invalid ones filtered out).

        """
        remapped = []

        for rel in relationships:
            old_source_val = rel.get("source")
            old_target_val = rel.get("target")

            # Ensure they are ints — guard against string residue in source/target
            try:
                old_source = int(old_source_val) if old_source_val is not None else None
            except (ValueError, TypeError):  # fmt: skip
                logger.debug(
                    "relationship_skipped_invalid_source",
                    source_value=old_source_val,
                )
                continue
            try:
                old_target = int(old_target_val) if old_target_val is not None else None
            except (ValueError, TypeError):  # fmt: skip
                logger.debug(
                    "relationship_skipped_invalid_target",
                    target_value=old_target_val,
                )
                continue

            # Get new indices
            new_source = index_mapping.get(old_source) if old_source is not None else None
            new_target = index_mapping.get(old_target) if old_target is not None else None

            # Skip if either entity was removed during deduplication
            if new_source is None or new_target is None:
                logger.debug(
                    "relationship_skipped_entity_removed",
                    old_source=old_source,
                    old_target=old_target,
                )
                continue

            # Skip self-loops
            if new_source == new_target:
                logger.debug(
                    "relationship_skipped_self_loop",
                    new_source=new_source,
                    new_target=new_target,
                    old_source=old_source,
                    old_target=old_target,
                )
                continue

            # Create remapped relationship
            remapped_rel = rel.copy()
            remapped_rel["source"] = new_source
            remapped_rel["target"] = new_target
            remapped.append(remapped_rel)

        removed_count = len(relationships) - len(remapped)
        if removed_count > 0:
            logger.info(
                "relationships_filtered_after_deduplication",
                filtered_count=removed_count,
                reason="entities_removed_or_self_loops",
            )

        return remapped

    @staticmethod
    def entity_to_embedding_text(entity: dict[str, Any], max_length: int | None = None) -> str:
        """Convert entity to text suitable for embeddings.

        Delegates to ``embedding_generator.entity_to_embedding_text``.

        Args:
            entity: Entity dictionary.
            max_length: Maximum character length (default: MAX_EMBEDDING_TEXT_LENGTH).

        Returns:
            Text representation of entity (name | aliases | truncated description).

        """
        return entity_to_embedding_text(entity, max_length)

    @staticmethod
    def calculate_entity_similarity(
        entity_a: dict[str, Any],
        entity_b: dict[str, Any],
        embedding_similarity: float,
    ) -> float:
        """Calculate total entity similarity with alias matching bonus.

        Delegates to ``similarity_matcher.calculate_entity_similarity``.

        Args:
            entity_a: First entity dictionary.
            entity_b: Second entity dictionary.
            embedding_similarity: Base cosine similarity from embeddings.

        Returns:
            Adjusted similarity score (capped at 1.0).

        """
        return calculate_entity_similarity(entity_a, entity_b, embedding_similarity)

    def merge_entities(
        self,
        kept: dict[str, Any],
        duplicate: dict[str, Any],
        confidence_penalty: float = 0.0,
    ) -> dict[str, Any]:
        """Merge two entities, combining ALL metadata from both.

        Merge strategy:
        - Aliases: Union of all aliases, plus duplicate's name if different
        - Properties: Deep merge, keeping more informative values for conflicts
        - Description: Combine if substantially different, otherwise keep longer
        - Confidence: Keep higher value, minus optional penalty
        - Type: Prefer type from higher-confidence entity
        - source_chunk_indices: Accumulate all chunk indices for provenance

        Args:
            kept: Entity to keep (base entity).
            duplicate: Duplicate entity to merge from.
            confidence_penalty: Confidence reduction to apply (e.g. for
                borderline merges). Subtracted from the max confidence.

        Returns:
            Merged entity with combined metadata from both sources.

        """
        merged = kept.copy()

        # === PROVENANCE: Track discarded loser-side values (Phase 6, 2026-05-08) ===
        # ``merged_property_history`` accumulates the values that were present on
        # the duplicate (loser) entity but were NOT promoted to the merged result.
        # Consumers (e.g. graph UI, debug dumps) can inspect this dict to understand
        # what was dropped during dedup without re-running extraction.
        # Shape: {"confidence": [0.72], "aliases_skipped": ["Dr", "Prof"],
        #         "properties": {"title": ["Senior Manager"]}}
        provenance: dict[str, list] = {}

        # === ALIASES: Union of all aliases ===
        kept_aliases = set(kept.get("aliases", []) or [])
        dup_aliases = set(duplicate.get("aliases", []) or [])
        dup_name = duplicate.get("name") or duplicate.get("label", "")
        kept_name = (kept.get("name") or kept.get("label", "")).lower()

        # Add duplicate's name as alias if different from kept name
        # BUT skip single-word title/honorific names (too generic for alias matching)
        if dup_name and dup_name.lower() != kept_name:
            dup_name_lower = dup_name.strip().lower()
            if dup_name_lower in self._title_words:
                logger.debug(
                    "title_word_alias_skipped",
                    kept_name=kept_name,
                    skipped_alias=dup_name,
                )
                # Phase 6: record skipped title-word alias in provenance
                provenance.setdefault("aliases_skipped", []).append(dup_name)
            else:
                dup_aliases.add(dup_name)

        merged["aliases"] = list(kept_aliases | dup_aliases)

        # === DESCRIPTORS: Union of descriptive phrases ===
        kept_descriptors = set(kept.get("descriptors", []) or [])
        dup_descriptors = set(duplicate.get("descriptors", []) or [])
        merged_descriptors = kept_descriptors | dup_descriptors
        if merged_descriptors:
            merged["descriptors"] = list(merged_descriptors)

        # === PROPERTIES: Deep merge (critical for preserving extracted attributes) ===
        kept_props = kept.get("properties", {}) or {}
        dup_props = duplicate.get("properties", {}) or {}

        if kept_props or dup_props:
            kept_conf = kept.get("confidence", 0) or 0
            dup_conf = duplicate.get("confidence", 0) or 0
            merged_props = EntityProcessor._merge_properties(
                kept_props,
                dup_props,
                kept_confidence=kept_conf,
                dup_confidence=dup_conf,
            )
            merged["properties"] = merged_props

            # Log property merge details for debugging
            if dup_props:
                new_keys = set(dup_props.keys()) - set(kept_props.keys())
                if new_keys:
                    logger.debug(
                        "properties_merged_new_keys",
                        entity=kept_name,
                        new_keys=list(new_keys),
                    )

        # === CONFIDENCE: Keep higher, apply optional penalty ===
        kept_conf = kept.get("confidence", 0) or 0
        dup_conf = duplicate.get("confidence", 0) or 0
        merged["confidence"] = max(0.1, max(kept_conf, dup_conf) - confidence_penalty)
        # Phase 6: record the loser's confidence in provenance so callers can
        # see how confident both extractions were before the merge decision.
        loser_conf = min(kept_conf, dup_conf)
        if loser_conf > 0:
            provenance.setdefault("confidence", []).append(loser_conf)

        # === DESCRIPTION: Combine if substantially different ===
        kept_desc = kept.get("description", "") or ""
        dup_desc = duplicate.get("description", "") or ""
        merged["description"] = self._merge_descriptions(kept_desc, dup_desc)

        # === TYPE: Prefer from higher-confidence entity ===
        if dup_conf > kept_conf and duplicate.get("type"):
            old_type = kept.get("type")
            new_type = duplicate["type"]
            if old_type != new_type:
                merged["type"] = new_type
                logger.debug(
                    "type_updated_from_higher_confidence",
                    entity=kept_name,
                    old_type=old_type,
                    new_type=new_type,
                    confidence_diff=round(dup_conf - kept_conf, 3),
                )

        # === SOURCE CHUNKS: Accumulate all chunk indices for provenance ===
        kept_chunks = list(kept.get("source_chunk_indices", []) or [])
        if not kept_chunks and kept.get("chunk_index") is not None:
            kept_chunks = [kept["chunk_index"]]

        dup_chunks = list(duplicate.get("source_chunk_indices", []) or [])
        if not dup_chunks and duplicate.get("chunk_index") is not None:
            dup_chunks = [duplicate["chunk_index"]]

        all_chunks = list(set(kept_chunks + dup_chunks))
        if all_chunks:
            # Coerce to int — LLMs may emit chunk indices as strings
            int_chunks = []
            for c in all_chunks:
                try:
                    int_chunks.append(int(c))
                except (ValueError, TypeError):  # fmt: skip
                    continue
            if int_chunks:
                merged["source_chunk_indices"] = sorted(int_chunks)
                if merged.get("chunk_index") is None:
                    merged["chunk_index"] = min(int_chunks)

        # Phase 6 (2026-05-08): write provenance.  Accumulate with any history
        # already present (entity may have been merged multiple times).
        if provenance:
            existing_history: dict[str, list] = dict(merged.get("merged_property_history") or {})
            for key, values in provenance.items():
                existing_history.setdefault(key, []).extend(values)
            merged["merged_property_history"] = existing_history

        logger.debug(
            "entities_merged",
            kept_name=kept_name,
            duplicate_name=dup_name,
            merged_aliases_count=len(merged.get("aliases", [])),
            merged_properties_count=len(merged.get("properties", {})),
            source_chunks=merged.get("source_chunk_indices", []),
        )

        return merged

    @staticmethod
    def _merge_properties(
        kept_props: dict,
        dup_props: dict,
        kept_confidence: float = 0.0,
        dup_confidence: float = 0.0,
    ) -> dict:
        """Deep merge two property dictionaries, preserving all extracted attributes.

        Strategy:
        - All unique keys from both entities are included
        - For string conflicts: prefer longer value, but for short values (< 20 chars)
          prefer the value from the higher-confidence entity
        - Lists are merged (deduplicated); nested dicts are recursively merged

        Args:
            kept_props: Properties from kept entity (higher priority for conflicts).
            dup_props: Properties from duplicate entity.
            kept_confidence: Confidence score of the kept entity.
            dup_confidence: Confidence score of the duplicate entity.

        Returns:
            Merged properties dictionary with data from both sources.

        """
        # Start with duplicate's properties (lower priority)
        merged = dict(dup_props)

        # Merge in kept's properties (higher priority for conflicts)
        for key, kept_value in kept_props.items():
            if key not in merged:
                # New key from kept - just add it
                merged[key] = kept_value
            else:
                dup_value = merged[key]

                # Same value - no conflict
                if kept_value == dup_value:
                    continue

                # Both are strings - context-aware resolution
                if isinstance(kept_value, str) and isinstance(dup_value, str):
                    shorter_len = min(len(kept_value), len(dup_value))
                    if shorter_len < 20:
                        # Short values: prefer from higher-confidence entity
                        merged[key] = kept_value if kept_confidence >= dup_confidence else dup_value
                    else:
                        # Long values: keep the longer/more informative one
                        merged[key] = kept_value if len(kept_value) >= len(dup_value) else dup_value

                # Both are lists - merge them (deduplicated)
                elif isinstance(kept_value, list) and isinstance(dup_value, list):
                    seen = set()
                    merged_list = []
                    for item in kept_value + dup_value:
                        # Create hashable key for deduplication
                        item_key = (
                            str(item) if not isinstance(item, (str, int, float, bool)) else item
                        )
                        if item_key not in seen:
                            seen.add(item_key)
                            merged_list.append(item)
                    merged[key] = merged_list

                # Both are dicts - recursive merge
                elif isinstance(kept_value, dict) and isinstance(dup_value, dict):
                    merged[key] = EntityProcessor._merge_properties(
                        kept_value,
                        dup_value,
                        kept_confidence=kept_confidence,
                        dup_confidence=dup_confidence,
                    )

                # Type mismatch or other - prefer kept's value (higher priority)
                else:
                    merged[key] = kept_value

        return merged

    def _merge_descriptions(self, desc1: str, desc2: str) -> str:  # noqa: PLR0911
        """Merge two descriptions, combining if they contain different information.

        Strategy:
        - If one is empty, return the other
        - If one contains the other, return the longer one
        - If high word overlap (>50%), keep the longer one (avoid redundancy)
        - If low overlap, concatenate them (preserve unique insights)
        - Cap total length at ``self._max_description_length``

        Args:
            desc1: First description (from kept entity).
            desc2: Second description (from duplicate entity).

        Returns:
            Merged description preserving unique information from both.

        """
        max_len = self._max_description_length
        if not desc1:
            return desc2[:max_len] if desc2 else ""
        if not desc2:
            return desc1[:max_len]

        # Quick check: if one contains the other, keep the longer
        if desc1 in desc2:
            return desc2[:max_len]
        if desc2 in desc1:
            return desc1[:max_len]

        # If desc1 is already at max length, don't add more
        if len(desc1) >= max_len:
            logger.debug(
                "description_at_max_length",
                current_length=len(desc1),
                max_length=max_len,
            )
            return desc1[:max_len]

        # Calculate word overlap to determine if descriptions are similar
        words1 = set(desc1.lower().split())
        words2 = set(desc2.lower().split())

        if not words1 or not words2:
            result = desc1 if len(desc1) >= len(desc2) else desc2
            return result[:max_len]

        overlap = len(words1 & words2)
        max_words = max(len(words1), len(words2))
        overlap_ratio = overlap / max_words

        # High overlap (>50%): descriptions are similar, keep longer to avoid redundancy
        if overlap_ratio > 0.5:
            result = desc1 if len(desc1) >= len(desc2) else desc2
            return result[:max_len]

        # Low overlap: descriptions contain different information, combine them
        # Ensure proper sentence ending before concatenation
        combined = desc1.rstrip()
        if combined and combined[-1] not in ".!?":
            combined += "."
        combined += " " + desc2.strip()

        # Truncate if exceeds max length
        if len(combined) > max_len:
            truncated = combined[: max_len - 3]
            # Try to break at sentence boundary
            last_period = truncated.rfind(". ")
            if last_period > max_len // 2:
                truncated = truncated[: last_period + 1]
            else:
                truncated = truncated.rstrip() + "..."
            logger.debug(
                "description_truncated",
                original_length=len(combined),
                truncated_length=len(truncated),
            )
            return truncated

        logger.debug(
            "descriptions_combined",
            overlap_ratio=round(overlap_ratio, 2),
            combined_length=len(combined),
        )

        return combined

    def resolve_hierarchical_names(
        self,
        entities: list[dict],
        relationships: list[dict],
    ) -> tuple[list[dict], list[dict], dict[int, int | None]]:
        """Merge entities where one name is contained within another of the same type.

        Handles progressive name resolution across chunks:
        - "Count" + "Count Bob" + "Count Bob Smith" -> "Count Bob Smith"
        - "Anna" + "Anna Pavlovna" -> "Anna Pavlovna"
        - "Prince Andrew" + "Andrew Bolkonsky" -> merged (shared "Andrew")

        Only merges entities of the SAME TYPE to avoid false positives.

        Args:
            entities: List of entity dictionaries.
            relationships: List of relationship dictionaries with source/target
                indices.

        Returns:
            Tuple of (merged_entities, updated_relationships, index_mapping):
                - merged_entities: Entities after hierarchical merging.
                - updated_relationships: Relationships with remapped indices.
                - index_mapping: Dict mapping old_index -> new_index.

        """
        if not entities:
            return [], relationships, {}

        # Group entities by type
        type_groups: dict[str, list[tuple[int, dict]]] = {}
        for idx, entity in enumerate(entities):
            entity_type = entity.get("type", "Unknown")
            if entity_type not in type_groups:
                type_groups[entity_type] = []
            type_groups[entity_type].append((idx, entity))

        # Track which entities to merge: canonical_idx -> list of indices to merge into it
        merge_map: dict[int, list[int]] = {}
        merged_into: dict[int, int] = {}  # idx -> canonical_idx

        for entity_type, indexed_entities in type_groups.items():
            if len(indexed_entities) < 2:
                continue

            # Find merge candidates within this type group
            for i, (idx_a, entity_a) in enumerate(indexed_entities):
                if idx_a in merged_into:
                    continue

                name_a = (entity_a.get("name") or entity_a.get("label", "")).strip()
                if not name_a:
                    continue

                words_a = extract_significant_words(name_a, self._title_words)
                if not words_a:
                    continue

                for _j, (idx_b, entity_b) in enumerate(indexed_entities[i + 1 :], start=i + 1):
                    if idx_b in merged_into:
                        continue

                    name_b = (entity_b.get("name") or entity_b.get("label", "")).strip()
                    if not name_b:
                        continue

                    words_b = extract_significant_words(name_b, self._title_words)
                    if not words_b:
                        continue

                    # Check for merge candidates
                    merge_result, canonical_idx = should_merge_names(
                        idx_a,
                        name_a,
                        words_a,
                        idx_b,
                        name_b,
                        words_b,
                    )

                    if merge_result:
                        non_canonical_idx = idx_b if canonical_idx == idx_a else idx_a

                        # Check if canonical already has a merge group
                        if canonical_idx in merge_map:
                            merge_map[canonical_idx].append(non_canonical_idx)
                        elif canonical_idx in merged_into:
                            # Canonical was already merged into something else
                            actual_canonical = merged_into[canonical_idx]
                            merge_map[actual_canonical].append(non_canonical_idx)
                            canonical_idx = actual_canonical
                        else:
                            merge_map[canonical_idx] = [non_canonical_idx]

                        merged_into[non_canonical_idx] = canonical_idx

                        logger.info(
                            "hierarchical_merge_candidate",
                            canonical_name=entities[canonical_idx].get("name"),
                            merged_name=entities[non_canonical_idx].get("name"),
                            entity_type=entity_type,
                        )

        if not merged_into:
            # No merges needed
            return entities, relationships, {i: i for i in range(len(entities))}

        # Resolve every entity to the ultimate canonical of its merge chain.
        # `merged_into` maps a merged-away index to the canonical it was folded
        # into; a node absent from `merged_into` is a survivor (root). Following
        # the chain to a fixpoint is essential: an entity can be canonical for a
        # shorter name yet later merged into a more complete one (e.g. "Smith" ->
        # "Bob Smith" -> "Bob Smith Jr"). Resolving by root guarantees every such
        # intermediate node still lands in `index_mapping`, so neither it nor the
        # relationships touching it are silently dropped.
        def _resolve_root(idx: int) -> int:
            seen: set[int] = set()
            while idx in merged_into and idx not in seen:
                seen.add(idx)
                idx = merged_into[idx]
            return idx

        roots = [_resolve_root(i) for i in range(len(entities))]
        root_members: dict[int, list[int]] = {}
        for old_idx, root in enumerate(roots):
            root_members.setdefault(root, []).append(old_idx)

        # Build survivors in original first-appearance order; every input index
        # maps to a real surviving entity (no None gaps, no dropped indices).
        merged_entities: list[dict] = []
        index_mapping: dict[int, int | None] = {}
        root_to_new_idx: dict[int, int] = {}

        for old_idx, root in enumerate(roots):
            if root not in root_to_new_idx:
                root_to_new_idx[root] = len(merged_entities)
                # Fold every other member of the group into the canonical root.
                merged = entities[root].copy()
                for member_idx in root_members[root]:
                    if member_idx != root:
                        merged = self.merge_entities(merged, entities[member_idx])
                merged_entities.append(merged)
            index_mapping[old_idx] = root_to_new_idx[root]

        # Remap relationships
        remapped_relationships = EntityProcessor.remap_relationship_indices(
            relationships, index_mapping
        )

        logger.info(
            "hierarchical_name_resolution_complete",
            original_count=len(entities),
            merged_count=len(merged_entities),
            merges_performed=len(merged_into),
        )

        return merged_entities, remapped_relationships, index_mapping

    @staticmethod
    def ensure_json_serializable(obj: Any) -> Any:
        """Ensure object is JSON serializable.

        Args:
            obj: Object to convert.

        Returns:
            JSON-serializable version of object.

        """
        if isinstance(obj, dict):
            return {k: EntityProcessor.ensure_json_serializable(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [EntityProcessor.ensure_json_serializable(item) for item in obj]
        if isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        # Convert other types to string
        return str(obj)
