# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Entity Processor for Import Orchestrator.

Orchestrates entity deduplication, merging, and relationship remapping for
the source import pipeline. Delegates embedding generation to
``embedding_generator`` and similarity comparison to ``similarity_matcher``.

SRP: Single responsibility for entity processing orchestration.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteringLog,
    )

from chaoscypher_core.services.quality.counters import QualityCounter, increment_quality_counter
from chaoscypher_core.services.sources.engine.deduplication import name_identity
from chaoscypher_core.services.sources.engine.deduplication.embedding_generator import (
    MAX_EMBEDDING_TEXT_LENGTH,
    entity_to_embedding_text,
    generate_entity_embeddings,
    l2_normalize_embeddings,
)
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    ATTESTED_NAMES_KEY,
    DESCRIPTOR_REFERENCE_KEY,
    EDGE_FLAG_REASON_KEYS,
    GIVEN_ALIASES_KEY,
    JUSTIFICATIONS_KEY,
    MERGED_NAMES_KEY,
    NODE_DESCRIPTION_KEYS,
    PIPELINE_EDGE_FLAG_KEYS,
    UNANIMOUS_FLAG_KEYS,
    UNPLACED_TYPE_KEY,
    NameConventions,
    NameIndex,
    as_conventions,
    edge_justifications,
    is_unevidenced_record,
    name_record,
    pipeline_flag,
    plan_name_pairs,
    record_attested_names,
    record_given_aliases,
    record_merged_names,
    record_order_key,
    survivor_candidate,
)
from chaoscypher_core.services.sources.engine.deduplication.similarity_matcher import (
    are_types_compatible,
    calculate_entity_similarity,
    core_type_key,
    normalize_name_key,
)


logger = structlog.get_logger(__name__)


def given_aliases(entity: dict[str, Any]) -> list[str]:
    """The aliases given ``entity`` (or a node's properties) under its own name.

    See :data:`GIVEN_ALIASES_KEY` and ``name_identity.record_given_aliases``
    (the one reading): without the key, every alias is given.
    """
    return record_given_aliases(entity)


def ordered_union[T](*groups: Iterable[T] | None) -> list[T]:
    """Every item of ``groups`` once, in first-seen order.

    The merge's union of aliases and descriptors: a ``set`` union's order
    follows the string hash seed, and these lists reach the stored node and
    the "Also known as:" embedding text, so two identical extractions gave
    different vectors (16 of v3's 97 entities between two seeds, audit W4-4).
    """
    return list(dict.fromkeys(item for group in groups for item in group or ()))


def _subtypes_of(properties: Any) -> list[str]:
    """The subtypes a record's properties carry: ``entity_subtype``, then ``entity_subtypes``."""
    from chaoscypher_core.services.sources.engine.extraction.domains.core_types import (
        subtype_key,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
        SUBTYPE_PROPERTY,
        SUBTYPES_PROPERTY,
    )

    if not isinstance(properties, dict):
        return []
    many = properties.get(SUBTYPES_PROPERTY)
    written = [properties.get(SUBTYPE_PROPERTY), *(many if isinstance(many, list) else ())]
    return [subtype_key(str(s)) for s in written if s and str(s).strip()]


def merge_subtypes(merged: dict[str, Any], kept: Any, duplicate: Any) -> None:
    """Give ``merged`` (the properties of ``duplicate`` merged into ``kept``'s) both records' subtypes, in place.

    A merge keeps the core type (spec 2026-10-07 § 6): ``entity_subtype``
    is the kept record's (the survivor's), else the duplicate's; when the
    two carry different subtypes, every one is kept, sorted, under
    ``entity_subtypes`` (``Person``/``character`` and ``Person``/``author``
    under one name are one Person with both). Equal subtypes, or none,
    leave the properties as they are. The one subtype policy of the
    pre-commit merge (:meth:`EntityProcessor.merge_entities`, which passes
    only the sides of the merged core type) and the resolver's
    (:func:`merge_node_properties`).
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
        SUBTYPE_PROPERTY,
        SUBTYPES_PROPERTY,
    )

    sides = [_subtypes_of(kept), _subtypes_of(duplicate)]
    if not any(sides) and not _subtypes_of(merged):
        return
    merged.pop(SUBTYPE_PROPERTY, None)
    merged.pop(SUBTYPES_PROPERTY, None)
    every = sorted({s for side in sides for s in side})
    primary = next((side[0] for side in sides if side), None)
    if primary:
        merged[SUBTYPE_PROPERTY] = primary
    if len(every) > 1:
        merged[SUBTYPES_PROPERTY] = every


def publish_given_aliases(entities: list[dict[str, Any]]) -> None:
    """Write :data:`GIVEN_ALIASES_KEY` onto every entity, in place, for the commit to store.

    The given aliases an entity still carries (a cleaner may have removed
    some from ``aliases`` since), in their given order.
    """
    for entity in entities:
        kept = {normalize_name_key(a) for a in entity.get("aliases") or [] if a}
        entity[GIVEN_ALIASES_KEY] = [
            a for a in given_aliases(entity) if normalize_name_key(a) in kept
        ]


def publish_text_referents(entities: list[dict[str, Any]], conventions: NameConventions) -> None:
    """Give each bare-title mention the one name its own sentences write with its title, in place.

    ``the Emperor`` cited on "... said the Emperor Alexander ..." is given
    ``Emperor Alexander`` as an alias (given and attested: its own text writes
    it), so the name rule reads it as any alias (``NameIndex.referent``). The
    decision is ``name_identity.text_referent_names``; its sentences are the
    mention's own ``evidence`` (stamped at aggregation from the group the
    model read). Run before the exact deduplication, on single mentions.
    """
    for entity in entities:
        label = str(entity.get("name") or "")
        if name_identity.reference_key(label, conventions, entity.get("type")) is None:
            continue
        texts = [
            str(ev.get("text") or "") for ev in entity.get("evidence") or [] if isinstance(ev, dict)
        ]
        # through the module: the registered guard (``name_guards``) replaces it here too
        names = name_identity.text_referent_names(label, texts, conventions)
        aliases = list(entity.get("aliases") or [])
        known = {normalize_name_key(a) for a in aliases if a}
        for name in names:
            if normalize_name_key(name) in known:
                continue
            given = given_aliases(entity)
            aliases.append(name)
            entity["aliases"] = aliases
            if GIVEN_ALIASES_KEY in entity:
                entity[GIVEN_ALIASES_KEY] = [*given, name]
            entity[ATTESTED_NAMES_KEY] = sorted({*record_attested_names(entity), name})


def publish_descriptor_references(
    entities: list[dict[str, Any]], conventions: NameConventions
) -> None:
    """Flag every entity labelled by a reference (``DESCRIPTOR_REFERENCE_KEY``), in place.

    From the final label (``name_identity.labelled_by_reference``): a reference
    no name identified keeps a node of its own, flagged for a later fold; a
    named entity carries no flag, whatever it absorbed. Every type is read
    since 2026-10-07 (``the army``, ``the government``, ``the village``: a
    group, place or event written without a proper name), typed by the
    rescue's core type. Run when deduplication ends.
    """
    for entity in entities:
        props = entity.get("properties")
        if name_identity.labelled_by_reference(
            str(entity.get("name") or ""), conventions, entity.get("type")
        ):
            entity["properties"] = {**(props or {}), DESCRIPTOR_REFERENCE_KEY: True}
        elif isinstance(props, dict) and DESCRIPTOR_REFERENCE_KEY in props:
            entity["properties"] = {k: v for k, v in props.items() if k != DESCRIPTOR_REFERENCE_KEY}


def _unplaced(entity: Mapping[str, Any]) -> bool:
    """Whether ``entity`` was kept typed Concept because no core type answered to its written type."""
    return pipeline_flag(entity.get("properties"), UNPLACED_TYPE_KEY)


def merge_unanimous_flags(
    merged: dict[str, Any],
    kept: Mapping[str, Any],
    duplicate: Mapping[str, Any],
    keys: Sequence[str] = UNANIMOUS_FLAG_KEYS,
) -> None:
    """Each flag in ``keys`` survives a merge only when both records carry it, in place.

    The one flag-merge rule, for nodes and edges: a flag says what the
    pipeline doubted about one copy, and a copy it did not doubt answers for
    the merged one. Nodes (``UNANIMOUS_FLAG_KEYS``, the default): the orphan
    flag (the resolver moves every edge of the dropped node onto the kept
    one, ``resolution.apply``, so the merged node is edgeless only when both
    were), and the plausibility, unplaced-type, unevidenced and
    excluded-name flags (a name another mention wrote plausibly, a type
    another mention placed, a mention its sentence names, or a name no
    exclusion lists, is no longer in doubt). Edges (``PIPELINE_EDGE_FLAG_KEYS``, :func:`merge_edge_properties`):
    one copy a sentence states makes the fact evidenced, one copy within the
    caps makes it uncapped. A value of the text's under a flag's key
    (``"yes"``) is no flag and is kept in the flag's place
    (``name_identity.pipeline_flag``). The node flags' companions (the score,
    the written type) stay as data.
    """
    for key in keys:
        if merged.get(key) is not True or (
            pipeline_flag(kept, key) and pipeline_flag(duplicate, key)
        ):
            continue
        text_value = next(
            (p[key] for p in (kept, duplicate) if p.get(key) not in (None, True)),
            None,
        )
        if text_value is None:
            del merged[key]
        else:
            merged[key] = text_value


def merge_node_properties(kept: dict[str, Any], duplicate: dict[str, Any]) -> dict[str, Any]:
    """Fold ``duplicate``'s properties into ``kept``'s under the deduplication policy.

    The one property-merge policy: the pre-commit deduplication
    (:meth:`EntityProcessor.merge_entities`) and the post-commit identity
    resolver (``services.graph.resolution``) both use it, so a merged node
    looks the same whichever pass merged it.
    """
    processor = EntityProcessor()
    merged = processor._merge_properties(kept, duplicate)  # noqa: SLF001
    # A description has its own policy (the longer, or both when they differ), under every key
    # a stored node keeps one; the generic rule would keep the longer and lose the other.
    for key in NODE_DESCRIPTION_KEYS:
        kept_desc, dup_desc = str(kept.get(key) or ""), str(duplicate.get(key) or "")
        if kept_desc or dup_desc:
            merged[key] = processor._merge_descriptions(kept_desc, dup_desc)  # noqa: SLF001
    merge_subtypes(merged, kept, duplicate)
    return merged


EDGE_EVIDENCE_FIELDS: tuple[str, ...] = ("justification", "sent_ref", "chunk_index")
"""The edge properties that say where its fact is written: they travel together between copies."""


def merge_edge_properties(
    kept: Mapping[str, Any],
    duplicate: Mapping[str, Any],
    *,
    keep_justifications: bool = True,
) -> dict[str, Any]:
    """One edge from two copies of one fact: the edge counterpart of :func:`merge_node_properties`.

    Keep, flag, never delete (2026-10-07). The merged edge is ``kept``'s
    properties, with three rules:

    - every edge flag (``PIPELINE_EDGE_FLAG_KEYS``: capped, unevidenced,
      unmatched label, type mismatch) stays only when both copies carry it
      (:func:`merge_unanimous_flags`, the nodes' rule): one copy a sentence
      states makes the fact evidenced, one within the caps makes it uncapped;
      a flag's reason (``EDGE_FLAG_REASON_KEYS``: ``evidence_reason``,
      ``type_mismatch_reason``) goes with its flag, while the label the model
      wrote (``written_label``) is data and stays;
    - when ``kept`` is the unevidenced copy and ``duplicate`` the evidenced
      one, the evidenced copy's sentence (:data:`EDGE_EVIDENCE_FIELDS`:
      justification, ``sent_ref``, ``chunk_index``) leads;
    - with ``keep_justifications`` the other copy's justification is kept in
      ``JUSTIFICATIONS_KEY`` (the edge's own first, each text once), so no
      evidence sentence is lost when the copy is dropped (the identity
      resolver's merge). The commit passes False: every copy of a fact keeps
      its justification on its own relationship citation there.

    Used by the commit (the copies of a fact the stable edge id stores once:
    ``RelationshipCommitHandler``) and the identity resolver (an edge a
    merge moves onto a node that already has it: ``resolution.apply``).
    """
    merged = dict(kept)
    kept_flagged, duplicate_flagged = is_unevidenced_record(kept), is_unevidenced_record(duplicate)
    lead: Mapping[str, Any] = duplicate if kept_flagged and not duplicate_flagged else kept
    if lead is duplicate:
        for key in EDGE_EVIDENCE_FIELDS:
            if key in duplicate:
                merged[key] = duplicate[key]
            else:
                merged.pop(key, None)
    merge_unanimous_flags(merged, kept, duplicate, PIPELINE_EDGE_FLAG_KEYS)
    for flag, reason in EDGE_FLAG_REASON_KEYS.items():
        if not pipeline_flag(merged, flag):
            merged.pop(reason, None)
    if keep_justifications:
        other = kept if lead is duplicate else duplicate
        texts = list(dict.fromkeys([*edge_justifications(lead), *edge_justifications(other)]))
        if len(texts) > 1:
            merged[JUSTIFICATIONS_KEY] = texts
    return merged


class EntityProcessor:
    """Processes entities for import operations.

    Responsibilities:
    - Deduplicate entities (exact and semantic modes)
    - Extract embedding text from entities
    - Prepare entities for storage

    Args:
        title_words: Domain-specific title/honorific words to filter
            during deduplication, the convenience form of
            ``name_conventions`` (every other table universal).
        name_conventions: The source domain's naming tables
            (``domain_resolver.domain_name_conventions``); the universal
            ones when neither this nor ``title_words`` is given.
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
        *,
        name_conventions: NameConventions | None = None,
    ) -> None:
        """Initialize entity processor.

        Args:
            title_words: Domain-specific title/honorific words to filter
                during deduplication (the convenience form of
                ``name_conventions``). Defaults to a minimal universal set.
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
            name_conventions: The source domain's naming tables (title words,
                gendered titles, generation words, name forms); wins over
                ``title_words``, which may not be given with it.
        """
        self._names = as_conventions(name_conventions, title_words)
        self._title_words = self._names.titles
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
                of different core types (:func:`are_types_compatible`).
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
                    counts={"merges": removed_count, "groups": len(merged_into)},
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
    ) -> tuple[list[dict[str, Any]], list[list[float]], dict[int, int | None]]:
        """Identify and merge semantic duplicates using precomputed embeddings.

        When type compatibility is required, partitions entities by core type
        and only computes similarity within each group. This reduces
        comparisons by 80-90% for typical documents with many entity types.

        Args:
            entities: Original entity list.
            embeddings: Raw embedding vectors (parallel to entities).
            embeddings_normalized: L2-normalized embedding matrix.
            similarity_threshold: Minimum adjusted similarity to merge.
            require_type_compatibility: When True, skip merging entities
                of different core types.

        Returns:
            Tuple of (unique_entities, unique_embeddings, index_mapping).

        """
        # Build type-compatible comparison groups to avoid O(n^2) full matrix
        if require_type_compatibility and len(entities) >= self._dedup_type_partition_cutoff:
            comparison_groups = self._build_type_groups(entities)
            logger.info(
                "semantic_dedup_partitioned",
                entity_count=len(entities),
                group_count=len(comparison_groups),
                group_sizes=[len(g) for g in comparison_groups],
            )
        else:
            # Single group containing all indices
            comparison_groups = [list(range(len(entities)))]

        # Pre-normalize all entity names/aliases ONCE to avoid redundant
        # normalize_name_key() calls in the O(n^2) comparison loop.
        precomputed_names: list[tuple[str, frozenset[str]]] = []
        for entity in entities:
            name_key = normalize_name_key(entity.get("name") or entity.get("label", ""))
            alias_keys = frozenset(normalize_name_key(a) for a in given_aliases(entity) if a)
            precomputed_names.append((name_key, alias_keys))

        # The name rule's guards over every entity, as the exact step reads them: a
        # pair (or a running cluster and a candidate) the names do not allow to merge
        # (``NameIndex.allows_merge``) is never merged, however close the vectors
        # (audit W4-3: father and son, a count and a countess, merged on a shared
        # alias at a cosine of 0.86; wave 5: the withholding guards too).
        names_index = NameIndex(
            [name_record(e) for e in entities], conventions=self._names, typed=False
        )
        cluster: dict[int, list[int]] = {}
        penalty: dict[int, float] = {}

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
                cluster[new_idx] = [i]

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

                    # Calculate adjusted similarity with precomputed name keys; a name
                    # in the other's given aliases earns its bonus only as alias evidence.
                    name_i, aliases_i = precomputed_names[i]
                    name_j, aliases_j = precomputed_names[j]
                    adjusted_similarity = calculate_entity_similarity(
                        entity,
                        entities[j],
                        base_sim,
                        precomputed_a=precomputed_names[i],
                        precomputed_b=precomputed_names[j],
                        alias_evidence=(
                            name_i in aliases_j and names_index.alias_evidence_between(j, i),
                            name_j in aliases_i and names_index.alias_evidence_between(i, j),
                        ),
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
                            entity.get("type", ""), entities[j].get("type", "")
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
                        # The one merge predicate the exact steps call too: the
                        # group check and the guards a join needs (audit wave 5:
                        # at a cosine of 0.98 a father merged into his son).
                        if not names_index.allows_merge(cluster[new_idx], [j]):
                            logger.debug(
                                "semantic_merge_skipped_names_apart",
                                entity1_name=entity.get("name"),
                                entity2_name=entities[j].get("name"),
                                base_similarity=round(base_sim, 3),
                            )
                            continue
                        # Mark as duplicate - map to the first occurrence
                        index_mapping[j] = new_idx
                        seen_indices.add(j)
                        cluster[new_idx].append(j)
                        # Merge entities: combine aliases, keep best data
                        # Apply confidence penalty for borderline merges
                        confidence_penalty = (
                            self._dedup_borderline_penalty
                            if adjusted_similarity - similarity_threshold < 0.10
                            else 0.0
                        )
                        penalty[j] = confidence_penalty
                        # Fold into the running merge, not the original: a
                        # three-way cluster keeps every member's aliases,
                        # chunks and evidence.
                        merged = self.merge_entities(
                            unique_entities[new_idx],
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

        # Each cluster keeps the label the one survivor ranking picks, never the first
        # mention's (``name_identity.pick_survivor``, the exact and hierarchical steps'
        # and the resolver's): fold every other member into it, in cluster order.
        for new_idx, members in cluster.items():
            if len(members) < 2:
                continue
            survivor = members[self._survivor(entities, members)]
            if survivor == members[0]:
                continue
            merged = entities[survivor].copy()
            for m in members:
                if m != survivor:
                    # the pair's penalty is symmetric: the head joins at the survivor's
                    cost = penalty.get(m if m != members[0] else survivor, 0.0)
                    merged = self.merge_entities(merged, entities[m], confidence_penalty=cost)
            unique_entities[new_idx] = merged
            unique_embeddings[new_idx] = embeddings[survivor]

        removed_count = len(entities) - len(unique_entities)
        self._log_deduplication_summary(
            max_similarities, removed_count, len(unique_entities), similarity_threshold
        )

        return unique_entities, unique_embeddings, index_mapping

    def _survivor(self, entities: list[dict[str, Any]], members: list[int]) -> int:
        """The position in ``members`` of the one that keeps the label (``name_identity.pick_survivor``).

        Each member's support is the mentions behind it (``record_support``:
        one for a mention, its extraction groups for a merged entity), so the
        exact, semantic and hierarchical steps rank by the same count and
        ``pick_survivor`` sums it over every member written under one name.
        Until 2026-10-07 support was how many members wrote the same label,
        one for every member after the exact step. A tie left after every
        rank goes to the label itself, only to make the order total.
        """
        # Read through the module, so the registered guard (``name_guards``) replaces it here too.
        from chaoscypher_core.services.sources.engine.deduplication import name_identity

        return name_identity.pick_survivor(
            [survivor_candidate(entities[m]) for m in members], self._names
        )

    @staticmethod
    def _build_type_groups(entities: list[dict[str, Any]]) -> list[list[int]]:
        """Partition entity indices by core type (:func:`core_type_key`), each entity in one group.

        The core type is the group (spec 2026-10-07 § 6): until 2026-10-07 a
        domain's ``compatibility_group`` map joined type names here.

        Args:
            entities: Entity list.

        Returns:
            List of index lists, one per core type, in first-seen order.

        """
        type_to_indices: dict[str, list[int]] = {}
        for idx, entity in enumerate(entities):
            type_to_indices.setdefault(core_type_key(entity.get("type")), []).append(idx)
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
        filtering_log: FilteringLog | None = None,
    ) -> tuple[list[dict[str, Any]], dict[int, int | None]]:
        """Remove duplicate entities and return index mapping for relationship remapping.

        Deduplication is NAME-ONLY (case-insensitive, trimmed) by default. When
        ``require_type_compatibility`` is True, same-name entities of
        different core types are kept separate (e.g. "Paris" the Person vs
        "Paris" the Location); two subtypes of one core type are one entity
        (``Character`` and ``Author``, both Person).

        When duplicates are found, entities are FULLY MERGED using merge_entities()
        to preserve all metadata (properties, descriptions, aliases, etc.) from
        all occurrences across different chunks.

        Args:
            entities: List of entity dictionaries.
            require_type_compatibility: When True, only merge same-name
                entities of one core type (:func:`are_types_compatible`).
            filtering_log: Optional log collector for pipeline diagnostics.

        Returns:
            Tuple of (unique_entities, index_mapping):
                - unique_entities: List of deduplicated entities with merged
                  metadata.
                - index_mapping: Dict mapping old_index -> new_index (or None
                  if removed).

        """
        entities = list(entities)  # an anonymous reference's evidence is narrowed on a copy

        # Two steps, each a grouping of the whole input so that the order of
        # the mentions never decides it (audit wave 5: a name joined by alias
        # evidence was never registered as a key, so the same names in another
        # order gave another grouping, 110 to 119 groups over 20 shuffles of
        # v3). First, identical names are one entity (under the type switch,
        # per compatible type). Then an alias given under one name that is
        # another group's name joins the two when it is the name rule's alias
        # evidence over every entity of the input (``NameIndex.alias_evidence``:
        # "Kurágin" given to Anatole and to Vasíli keys neither, "the prince" is
        # never a key) and the names allow the merge (``NameIndex.allows_merge``,
        # the one predicate every merge step calls). Alias-to-alias is not
        # evidence; an alias picked up by absorbing a differently-named entity
        # is never a key (``given_aliases``).
        #
        # A mention written under a reference ("the count", "Emperor": ``reference_key``)
        # is not keyed by its spelling. One a name of its own identifies (``referent``) is
        # keyed alone and joins that name by its alias; an anonymous one is keyed by
        # what it refers by, so "Emperor" and "the Emperor" are one, and an identified
        # "the Emperor" never takes the anonymous ones with it (2026-10-07). A label of
        # another type that names no one is keyed the same way (the type switch decides;
        # the v4 generic arm wrote one "the count" with an empty type column).
        # The referent is read typed (a person is named by a person's name: an event called
        # "Name day of Nataly" is no holder of the name "the countess" was given).
        records = [name_record(e) for e in entities]
        names_index = NameIndex(records, conventions=self._names, typed=False)
        referents: NameIndex | None = None
        keys = [normalize_name_key(str(e.get("name") or e.get("label") or "")) for e in entities]
        for idx in range(len(entities)):
            if not keys[idx]:
                continue
            reference = names_index.reference(idx)
            if reference is not None:
                referents = referents or NameIndex(records, conventions=self._names)
                if referents.referent(idx) is not None:
                    keys[idx] = f"\x00{idx}"
                    continue
                # An anonymous mention's names identified no one: they are no evidence once
                # it joins the others written alike, or one mention's alias would carry
                # them all into a name (the v4 generic "the countess", given "Mamma").
                given = given_aliases(entities[idx])
                kept = [a for a in given if name_identity.reference_key(a, self._names)]
                if kept != given:
                    entities[idx] = {**entities[idx], GIVEN_ALIASES_KEY: kept}
            nameless = reference or name_identity.reference_key(
                str(entities[idx].get("name") or entities[idx].get("label") or ""), self._names
            )
            if nameless:
                keys[idx] = nameless
        parent = list(range(len(entities)))

        def _root(x: int) -> int:
            """Union-find root of ``x`` with path halving."""
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def _union(a: int, b: int) -> None:
            """Join the groups of ``a`` and ``b`` (the lower index roots the group)."""
            ra, rb = _root(a), _root(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)

        def _members(x: int) -> list[int]:
            """Every input entity in ``x``'s group, in input order."""
            r = _root(x)
            return [i for i in range(len(entities)) if keys[i] and _root(i) == r]

        def _compatible(a: int, b: int) -> bool:
            """``a`` and ``b`` may be one entity under the type switch."""
            return not require_type_compatibility or are_types_compatible(
                entities[a].get("type", ""), entities[b].get("type", "")
            )

        by_key: dict[str, list[int]] = {}
        for idx, key in enumerate(keys):
            if key:
                by_key.setdefault(key, []).append(idx)
        for members in by_key.values():
            # A mention whose written type no core type answered to (``unplaced_type``,
            # kept as a Concept: keep, flag, never delete) is one with the same name's
            # typed mentions when they are of one core type: its Concept is no type.
            typed = {
                core_type_key(entities[m].get("type"))
                for m in members
                if not _unplaced(entities[m])
            }
            wild = len(typed) <= 1
            for pos, a in enumerate(members):
                for b in members[pos + 1 :]:
                    if _compatible(a, b) or (
                        wild and (_unplaced(entities[a]) or _unplaced(entities[b]))
                    ):
                        _union(a, b)
                    else:
                        logger.info(
                            "type_incompatible_entity_kept_separate",
                            name=entities[b].get("name"),
                            existing_type=entities[a].get("type", ""),
                            new_type=entities[b].get("type", ""),
                        )

        links: list[tuple[tuple[Any, ...], int, str, int]] = []
        for giver, entity in enumerate(entities):
            if not keys[giver]:
                continue
            for alias in given_aliases(entity):
                alias_key = normalize_name_key(alias) if alias else ""
                if not alias_key or alias_key == keys[giver]:
                    continue
                for named in by_key.get(alias_key, ()):
                    order = (
                        record_order_key(names_index.records[giver]),
                        alias_key,
                        record_order_key(names_index.records[named]),
                    )
                    links.append((order, giver, alias, named))
        for _order, giver, alias, named in sorted(links, key=lambda link: link[0]):
            if _root(giver) == _root(named) or not _compatible(giver, named):
                continue
            if names_index.alias_evidence(giver, alias, named) and names_index.allows_merge(
                _members(giver), _members(named)
            ):
                logger.info(
                    "alias_matched_existing_entity",
                    new_entity=entities[giver].get("name"),
                    matched_alias=normalize_name_key(alias),
                    matched_entity=entities[named].get("name"),
                )
                _union(giver, named)

        groups: dict[int, list[int]] = {}
        for idx in range(len(entities)):
            if keys[idx]:
                groups.setdefault(_root(idx), []).append(idx)

        unique_entities: list[dict[str, Any]] = []
        index_mapping: dict[int, int | None] = dict.fromkeys(range(len(entities)))
        merge_count = 0
        for root in sorted(groups):
            members = groups[root]
            # The one survivor ranking (``name_identity.pick_survivor``): never the order.
            survivor = members[self._survivor(entities, members)]
            merged = entities[survivor].copy()
            for idx in members:
                if idx != survivor:
                    merged = self.merge_entities(merged, entities[idx])
                    merge_count += 1
            new_idx = len(unique_entities)
            unique_entities.append(merged)
            for idx in members:
                index_mapping[idx] = new_idx

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
                counts={"merges": removed_count, "groups": len(merged_into)},
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
        - Type: the kept entity's core type; another core type only from a
          higher-confidence duplicate (which only an untyped merge allows)
        - Subtypes: the kept entity's ``entity_subtype``; two different
          subtypes of the core type are both kept, sorted, in
          ``entity_subtypes`` (:func:`merge_subtypes`)
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
        dup_aliases = list(duplicate.get("aliases", []) or [])
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
                dup_aliases.append(dup_name)

        merged["aliases"] = ordered_union(kept.get("aliases"), dup_aliases)
        # The names folded in: never alias evidence, but the name rule's guards read them,
        # so a later pass never joins this entity to a name one of them is kept apart from.
        # A one-word bare title ("Emperor") is one of them (keep, flag, never delete,
        # 2026-10-07): too generic to be an alias key, but a name the entity was written by.
        folded_in = [dup_name] if dup_name and dup_name.lower() != kept_name else []
        merged_names = ordered_union(
            record_merged_names(kept), folded_in, record_merged_names(duplicate)
        )
        if merged_names:
            merged[MERGED_NAMES_KEY] = merged_names
        attested = sorted(record_attested_names(kept) | record_attested_names(duplicate))
        if attested:
            merged[ATTESTED_NAMES_KEY] = attested

        # Given aliases: the kept entity's, plus the duplicate's only when it
        # carries the same name (aliases given under that name). A merged-in
        # different name and its aliases are aliases now, but not evidence.
        kept_given = given_aliases(kept)
        if normalize_name_key(dup_name or "") == normalize_name_key(kept_name):
            known = {normalize_name_key(a) for a in kept_given}
            for alias in given_aliases(duplicate):
                key = normalize_name_key(alias)
                if key and key not in known:
                    known.add(key)
                    kept_given.append(alias)
        merged[GIVEN_ALIASES_KEY] = kept_given

        # === DESCRIPTORS: Union of descriptive phrases ===
        merged_descriptors = ordered_union(kept.get("descriptors"), duplicate.get("descriptors"))
        if merged_descriptors:
            merged["descriptors"] = merged_descriptors

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
                new_keys = [k for k in dup_props if k not in kept_props]
                if new_keys:
                    logger.debug(
                        "properties_merged_new_keys",
                        entity=kept_name,
                        new_keys=new_keys,
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

        # === TYPE: the core type stays; another only from a more confident duplicate ===
        # Lazy: the extraction package imports this module at load time.
        from chaoscypher_core.services.sources.engine.extraction.utils.constants import (
            UNKNOWN_ENTITY_TYPE,
        )

        # The parser's placeholder for an empty type column is no type: a stated one wins
        # (2026-10-07: the v4 generic "the count" holding the Archives fact was UNKNOWN, and
        # the four mentions merged under it were no person, so no reference).
        # An unplaced mention's Concept (``unplaced_type``: no core type answered to its
        # written type) is no type either: a stated one wins, and it never replaces one.
        placeholder = kept.get("type") in (None, "", UNKNOWN_ENTITY_TYPE) or _unplaced(kept)
        stated = duplicate.get("type") not in (None, "", UNKNOWN_ENTITY_TYPE) and not _unplaced(
            duplicate
        )
        if placeholder and stated:
            merged["type"] = duplicate["type"]
        elif (
            dup_conf > kept_conf
            and stated
            and not are_types_compatible(kept.get("type") or "", duplicate["type"])
        ):
            # Two core types merge only without the type switch; a subtype of the
            # kept core type never replaces it (a merge keeps the core type).
            merged["type"] = duplicate["type"]
            logger.debug(
                "type_updated_from_higher_confidence",
                entity=kept_name,
                old_type=kept.get("type"),
                new_type=duplicate["type"],
                confidence_diff=round(dup_conf - kept_conf, 3),
            )
        # === SUBTYPES: the kept one, and both when two of the merged core type differ ===
        core = core_type_key(merged.get("type"))
        same_core = [
            (side.get("properties") or {}) if core_type_key(side.get("type")) == core else {}
            for side in (kept, duplicate)
        ]
        if merged.get("properties") or any(same_core):
            subtyped = dict(merged.get("properties") or {})
            merge_subtypes(subtyped, *same_core)
            if subtyped or "properties" in merged:
                merged["properties"] = subtyped

        # === SOURCE CHUNKS: Accumulate all chunk indices for provenance ===
        kept_chunks = list(kept.get("source_chunk_indices", []) or [])
        if not kept_chunks and kept.get("chunk_index") is not None:
            kept_chunks = [kept["chunk_index"]]

        dup_chunks = list(duplicate.get("source_chunk_indices", []) or [])
        if not dup_chunks and duplicate.get("chunk_index") is not None:
            dup_chunks = [duplicate["chunk_index"]]

        all_chunks = ordered_union(kept_chunks, dup_chunks)
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

        # === EVIDENCE: Union of cited sentences (citation provenance) ===
        # The kept entity's ``cited_chunk_id`` stays; its evidence list grows
        # by every sentence the merged-in duplicate was cited by.
        if kept.get("evidence") or duplicate.get("evidence"):
            # Lazy: the extraction package imports this module at load time.
            from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
                merge_evidence,
            )

            merged["evidence"] = merge_evidence(kept.get("evidence"), duplicate.get("evidence"))
            if not merged.get("cited_chunk_id") and duplicate.get("cited_chunk_id"):
                merged["cited_chunk_id"] = duplicate["cited_chunk_id"]

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
        # Start with duplicate's properties (lower priority); the reference flag is the kept
        # record's own (a named survivor that absorbed "the count" is no reference).
        merged = {k: v for k, v in dup_props.items() if k != DESCRIPTOR_REFERENCE_KEY}

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

        merge_unanimous_flags(merged, kept_props, dup_props)
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
        filtering_log: FilteringLog | None = None,
    ) -> tuple[list[dict], list[dict], dict[int, int | None]]:
        """Merge entities of one core type whose names are one entity by the shared name rule.

        The rule is :func:`~chaoscypher_core.services.sources.engine.deduplication.name_identity.plan_name_pairs`,
        the one the post-commit identity resolver uses, with this processor's
        title words: same naming words, an alias given under one name that
        is the other, or a name that is the other with words left out and
        nothing added (``Mlle Schérer`` into ``Anna Pávlovna Schérer``;
        ``Anna`` + ``Anna Pavlovna`` + ``Anna Pavlovna Scherer`` into the
        last). A short name that fits two different fuller names (``Count
        Bezúkhov`` beside ``Pierre Bezúkhov`` and ``Cyril Vladímirovich
        Bezúkhov``) stays apart, and a shared word with words added on both
        sides is never a merge. Each group folds into its fullest name.

        Args:
            entities: List of entity dictionaries.
            relationships: List of relationship dictionaries with source/target
                indices.
            filtering_log: Optional log collector: a ``hierarchical_name_merge``
                stage with the merges and groups made.

        Returns:
            Tuple of (merged_entities, updated_relationships, index_mapping):
                - merged_entities: Entities after hierarchical merging.
                - updated_relationships: Relationships with remapped indices.
                - index_mapping: Dict mapping old_index -> new_index.

        """
        if not entities:
            return [], relationships, {}

        # Group entities by core type: the name rule joins names of one core type only.
        type_groups: dict[str, list[int]] = {}
        for idx, entity in enumerate(entities):
            type_groups.setdefault(core_type_key(entity.get("type", "Unknown")), []).append(idx)

        def _label(idx: int) -> str:
            """The entity's name as compared."""
            return (entities[idx].get("name") or entities[idx].get("label") or "").strip()

        # Union every pair the shared name rule joins (alias given under the
        # name, or a name that is the other with words left out and nothing
        # added -- withheld when the short name fits two different fuller
        # names); each group folds into its best-ranked member.
        parent = list(range(len(entities)))

        def _find(x: int) -> int:
            """Union-find root of ``x`` with path halving."""
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for entity_type, members in type_groups.items():
            if len(members) < 2:
                continue
            records = [name_record(entities[idx]) for idx in members]
            for i, j, evidence in plan_name_pairs(records, conventions=self._names):
                a, b = members[i], members[j]
                ra, rb = _find(a), _find(b)
                if ra != rb:
                    parent[rb] = ra
                logger.info(
                    "hierarchical_merge_candidate",
                    name_a=_label(a),
                    name_b=_label(b),
                    entity_type=entity_type,
                    evidence=evidence.kind,
                    why=evidence.why,
                )

        groups: dict[int, list[int]] = {}
        for idx in range(len(entities)):
            groups.setdefault(_find(idx), []).append(idx)
        merged_into: dict[int, int] = {}  # idx -> canonical_idx
        for members in groups.values():
            if len(members) < 2:
                continue
            canonical = members[self._survivor(entities, members)]
            for idx in members:
                if idx != canonical:
                    merged_into[idx] = canonical

        if not merged_into:
            # No merges needed
            return entities, relationships, {i: i for i in range(len(entities))}

        # Resolve every entity to the ultimate canonical of its merge chain.
        # `merged_into` maps a merged-away index to the canonical it was folded
        # into; a node absent from `merged_into` is a survivor (root). Following
        # the chain to a fixpoint is essential: an entity can be canonical for a
        # shorter name yet later merged into a more complete one (e.g. "Smith" ->
        # "Bob Smith" -> "Bob Alan Smith"). Resolving by root guarantees every such
        # intermediate node still lands in `index_mapping`, so neither it nor the
        # relationships touching it are silently dropped.
        def _resolve_root(idx: int) -> int:
            """Follow ``merged_into`` to the final canonical index (cycle-safe)."""
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
        if filtering_log is not None:
            from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
                FilteredItem,
            )

            filtering_log.add_stage(
                "hierarchical_name_merge",
                input_count=len(entities),
                removed_count=len(merged_into),
                items=[
                    FilteredItem(
                        item_type="entity",
                        name=_label(idx),
                        entity_type=str(entities[idx].get("type") or "?"),
                        reason=f"Name rule — merged into '{_label(canonical)}'",
                    )
                    for idx, canonical in merged_into.items()
                ],
                counts={
                    "merges": len(merged_into),
                    "groups": sum(1 for m in root_members.values() if len(m) > 1),
                },
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
