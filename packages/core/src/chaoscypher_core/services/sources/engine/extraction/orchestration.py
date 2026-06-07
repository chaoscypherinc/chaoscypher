# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared Extraction Orchestration Logic.

Pure business logic functions used by both the Cortex worker pipeline and the
CLI offline pipeline.  These functions are framework-agnostic — they accept
plain dicts/lists and concrete values, never framework-specific settings objects.

Functions:
    aggregate_chunk_results: Merge per-chunk entities/relationships into a global list.
    detect_extraction_domain: Auto-detect or force-select a domain for extraction.
    format_extraction_templates: Pre-compute formatted template strings for LLM prompts.
    apply_depth_strategy: Filter hierarchical groups based on extraction depth.
    cache_quality_scores: Compute and return quality scores for caching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# A1. Chunk aggregation
# ---------------------------------------------------------------------------


def aggregate_chunk_results(
    completed_chunks: list[dict[str, Any]],
    *,
    entities_key: str = "raw_entities",
    relationships_key: str = "raw_relationships",
    text_key: str = "input_text",
    sentences_key: str = "chunk_sentences",
) -> dict[str, Any]:
    """Aggregate per-chunk extraction results into a global entity/relationship list.

    Relationships returned by ``AIEntityExtractor.extract_single_chunk`` use
    **chunk-local integer indices** into the chunk's entity list.  This function
    remaps those indices so they reference positions in the combined entity list.

    Args:
        completed_chunks: List of chunk result dicts.  Each dict must contain
            *entities_key* (list of entity dicts) and *relationships_key*
            (list of relationship dicts with integer ``source``/``target``).
        entities_key: Key for the entity list inside each chunk dict.
        relationships_key: Key for the relationship list inside each chunk dict.
        text_key: Key for the chunk text.
        sentences_key: Key for pre-split sentence lists per chunk.

    Returns:
        Dictionary with keys:
        - ``entities``: Combined entity list (global indices).
        - ``relationships``: Combined relationship list (global indices).
        - ``chunk_texts``: List of per-chunk text strings.
        - ``chunk_sentences``: List of per-chunk sentence lists (may be None per chunk).
        - ``dropped_relationships_invalid_index``: Count of relationships silently
          dropped because their ``source``/``target`` was out of bounds or a
          non-integer (bool) — used by callers to increment the
          ``AGGREGATOR_RELATIONSHIPS_DROPPED`` quality counter.
    """
    all_entities: list[dict[str, Any]] = []
    all_relationships: list[dict[str, Any]] = []
    chunk_texts: list[str] = []
    all_chunk_sentences: list[list[str] | None] = []
    entity_offset = 0
    dropped_count = 0

    for task in completed_chunks:
        chunk_entities = task.get(entities_key) or []
        chunk_relationships = task.get(relationships_key) or []

        chunk_texts.append(task.get(text_key) or "")
        all_chunk_sentences.append(task.get(sentences_key))

        all_entities.extend(chunk_entities)

        # Remap chunk-local indices → global indices, and drop any relationship
        # whose source/target falls outside the chunk's own entity list.  This
        # is a defensive guard against malformed LLM output: per-chunk index
        # validation is meant to catch these upstream (see
        # ``validate_relationships`` in ``entity_cleaner``), but a regression
        # or partial failure there would otherwise produce a bad-edge target
        # at commit time.  Bounds are checked against the chunk's own entity
        # count (pre-offset) since chunk-local indices are what the LLM emits.
        chunk_entity_count = len(chunk_entities)
        for rel in chunk_relationships:
            local_src = rel.get("source")
            local_tgt = rel.get("target")
            # ``bool`` is a subclass of ``int`` in Python, so an isinstance(..., int)
            # check alone would accept a stray ``True``/``False`` from malformed
            # JSON output.  Reject bools explicitly.
            if (
                not isinstance(local_src, int)
                or isinstance(local_src, bool)
                or not isinstance(local_tgt, int)
                or isinstance(local_tgt, bool)
                or not 0 <= local_src < chunk_entity_count
                or not 0 <= local_tgt < chunk_entity_count
            ):
                dropped_count += 1
                logger.warning(
                    "invalid_relationship_index_dropped",
                    task_id=task.get("id"),
                    chunk_index=task.get("chunk_index"),
                    source_idx=local_src,
                    target_idx=local_tgt,
                    chunk_entity_count=chunk_entity_count,
                    rel_type=rel.get("type"),
                )
                continue
            rel["source"] = local_src + entity_offset
            rel["target"] = local_tgt + entity_offset
            all_relationships.append(rel)

        entity_offset += chunk_entity_count

    return {
        "entities": all_entities,
        "relationships": all_relationships,
        "chunk_texts": chunk_texts,
        "chunk_sentences": all_chunk_sentences,
        "dropped_relationships_invalid_index": dropped_count,
    }


# ---------------------------------------------------------------------------
# A2. Domain detection
# ---------------------------------------------------------------------------


def _build_ranking(
    candidates: list[tuple[Any, float]],
    *,
    fallback_name: str,
    fallback_score: float,
) -> list[dict[str, Any]]:
    """Serialize ranked candidates to ``[{domain, score}]``, highest first.

    Guarantees a defined ``ranking[0]`` even when ``candidates`` is empty by
    synthesizing a single entry from the resolved winner/fallback, so every
    downstream surface can read ``ranking[0]`` unconditionally.
    """
    if candidates:
        return [{"domain": d.name, "score": score} for d, score in candidates]
    return [{"domain": fallback_name, "score": fallback_score}]


def detect_extraction_domain(
    registry: Any,
    forced_domain: str | None,
    sample_text: str,
    filename: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect (or force-select) the extraction domain.

    Args:
        registry: ``DomainRegistry`` instance.
        forced_domain: If non-None, use this domain unconditionally.
        sample_text: Representative text sample for auto-detection.
        filename: Original filename (used as a detection signal).
        metadata: Optional document metadata for auto-detection.

    Returns:
        Dictionary with keys:
        - ``domain``: Domain instance (or ``None``).
        - ``detected_domain``: Domain name string.
        - ``confidence``: Detection confidence (1.0 when forced).
        - ``entity_guidance``: Entity-specific guidance string.
        - ``relationship_guidance``: Relationship-specific guidance string.
        - ``ranking``: List of ``{domain: str, score: float}`` (highest first);
          always has at least one entry (``ranking[0]`` synthesized from the
          resolved domain when no candidate cleared detection).
        - ``low_confidence``: ``True`` when detection fell back to generic /
          the minimal fallback or did not land on the top raw candidate.
    """
    if forced_domain:
        domain = registry.get_domain(forced_domain)
        entity_guidance = ""
        relationship_guidance = ""
        if domain:
            # Use split guidance if available, else fall back to combined
            if hasattr(domain, "get_entity_guidance"):
                entity_guidance = domain.get_entity_guidance() or ""
            else:
                entity_guidance = domain.get_guidance() or ""
            if hasattr(domain, "get_relationship_guidance"):
                relationship_guidance = domain.get_relationship_guidance() or ""
        logger.info(
            "domain_forced",
            domain=forced_domain,
            has_entity_guidance=bool(entity_guidance),
            has_relationship_guidance=bool(relationship_guidance),
        )
        auto_candidates = registry.rank_domains(sample_text, filename, metadata or {})
        ranking = _build_ranking(
            auto_candidates,
            fallback_name=forced_domain,
            fallback_score=1.0,
        )
        return {
            "domain": domain,
            "detected_domain": forced_domain,
            "confidence": 1.0,
            "entity_guidance": entity_guidance,
            "relationship_guidance": relationship_guidance,
            "ranking": ranking,
            "low_confidence": False,
        }

    # Auto-detect
    detected_domain_obj, confidence = registry.get_best_domain(
        sample_text,
        filename,
        metadata or {},
    )
    detected_name = detected_domain_obj.name if detected_domain_obj else "generic"

    entity_guidance = ""
    relationship_guidance = ""
    if detected_domain_obj:
        if hasattr(detected_domain_obj, "get_entity_guidance"):
            entity_guidance = detected_domain_obj.get_entity_guidance() or ""
        else:
            entity_guidance = detected_domain_obj.get_guidance() or ""
        if hasattr(detected_domain_obj, "get_relationship_guidance"):
            relationship_guidance = detected_domain_obj.get_relationship_guidance() or ""

    auto_candidates = registry.rank_domains(sample_text, filename, metadata or {})
    ranking = _build_ranking(
        auto_candidates,
        fallback_name=detected_name,
        fallback_score=confidence,
    )
    # Low confidence when detection did not land on a clear, floor-clearing
    # winner: the resolved domain is the generic/minimal fallback, or it does
    # not match the top raw candidate (i.e. get_best_domain fell back).
    top_candidate = auto_candidates[0][0].name if auto_candidates else None
    low_confidence = detected_name in {"generic", "fallback"} or detected_name != top_candidate

    logger.info(
        "domain_detected",
        detected_domain=detected_name,
        confidence=confidence,
        has_entity_guidance=bool(entity_guidance),
        has_relationship_guidance=bool(relationship_guidance),
        sample_length=len(sample_text),
    )

    return {
        "domain": detected_domain_obj,
        "detected_domain": detected_name,
        "confidence": confidence,
        "entity_guidance": entity_guidance,
        "relationship_guidance": relationship_guidance,
        "ranking": ranking,
        "low_confidence": low_confidence,
    }


# ---------------------------------------------------------------------------
# A3. Template formatting
# ---------------------------------------------------------------------------


def format_extraction_templates(
    domain: Any,
    *,
    examples_enabled: bool = True,
    examples_max_chars: int = 800,
    allow_template_fallback: bool = True,
) -> dict[str, str]:
    """Pre-compute formatted template and example strings for LLM prompts.

    Args:
        domain: Domain instance (may be ``None`` for generic fallback).
        examples_enabled: Whether to include domain-specific examples.
        examples_max_chars: Maximum character count for each example section.
        allow_template_fallback: Phase 6 (2026-05-08). When False, an empty
            domain template list raises ValidationError instead of silently
            using generic built-in templates. Passed through to
            ``format_domain_node_templates`` and ``format_domain_edge_templates``.

    Returns:
        Dictionary with keys:
        - ``node_templates``: Formatted node template string.
        - ``edge_templates``: Formatted edge template string.
        - ``entity_examples``: Formatted entity examples (empty string if disabled).
        - ``relationship_examples``: Formatted relationship examples (empty string if disabled).
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.template_formatter import (
        format_domain_edge_templates,
        format_domain_node_templates,
        format_entity_examples,
        format_relationship_examples,
    )

    templates = domain.get_templates() if domain else {"node_templates": [], "edge_templates": []}

    node_templates = format_domain_node_templates(
        templates, allow_template_fallback=allow_template_fallback
    )
    edge_templates = format_domain_edge_templates(
        templates, allow_template_fallback=allow_template_fallback
    )

    entity_examples = ""
    relationship_examples = ""
    if examples_enabled and domain:
        domain_examples = domain.get_examples()
        if domain_examples:
            entity_examples = format_entity_examples(domain_examples, max_chars=examples_max_chars)
            relationship_examples = format_relationship_examples(
                domain_examples, max_chars=examples_max_chars
            )

    return {
        "node_templates": node_templates,
        "edge_templates": edge_templates,
        "entity_examples": entity_examples,
        "relationship_examples": relationship_examples,
    }


# ---------------------------------------------------------------------------
# A4. Depth strategy
# ---------------------------------------------------------------------------


_VALID_DEPTH_VALUES: frozenset[str] = frozenset({"quick", "full"})


def apply_depth_strategy(
    groups: list[dict[str, Any]],
    depth: str,
    *,
    quick_sample_size: int = 5,
) -> list[dict[str, Any]]:
    """Filter hierarchical groups based on extraction depth.

    Uses even-distribution sampling (every Nth group) for ``quick`` depth
    to ensure representative coverage across the document.

    Args:
        groups: Full list of hierarchical groups.
        depth: One of ``"quick"`` or ``"full"``. Any other value raises
            ``ValidationError`` (Phase 6, 2026-05-08 — previously unrecognised
            values were silently treated as ``"full"``).
        quick_sample_size: Maximum groups for quick depth.

    Returns:
        Filtered list of groups to process.

    Raises:
        ValidationError: When ``depth`` is not one of the canonical values.
    """
    from chaoscypher_core.exceptions import ValidationError as _ValidationError

    if depth not in _VALID_DEPTH_VALUES:
        msg = f"Invalid extraction depth {depth!r}. Valid values: {sorted(_VALID_DEPTH_VALUES)}."
        raise _ValidationError(msg, field="depth")

    total = len(groups)
    if total == 0:
        return []

    if depth == "quick":
        sample_size = min(quick_sample_size, total)
    else:
        # "full" → use all groups
        return groups

    step = max(1, total // sample_size)
    selected_indices = list(range(0, total, step))[:sample_size]
    return [groups[i] for i in selected_indices]


# ---------------------------------------------------------------------------
# A5. Content filtering and dynamic group building
# ---------------------------------------------------------------------------


@dataclass
class FilterStats:
    """Statistics from content filtering applied to a set of chunks.

    Attributes:
        total_chunks: Number of chunks before filtering.
        excluded_chunks: Number of chunks fully excluded.
        categories_matched: Mapping of category name to match count.
        avg_content_stripped_ratio: Average ratio of content stripped across all chunks.
        regex_timeouts: Total number of match calls on user-supplied patterns
            that hit the ``USER_REGEX_TIMEOUT`` deadline during this filter
            pass.  Each hit means the pattern returned the safe default
            (``False`` / ``[]``) instead of the real answer — a silent filter
            bypass that callers should surface via ``USER_REGEX_TIMEOUT_HITS``.
    """

    total_chunks: int = 0
    excluded_chunks: int = 0
    categories_matched: dict[str, int] = field(default_factory=dict)
    avg_content_stripped_ratio: float = 0.0
    regex_timeouts: int = 0


def strip_chunk_content(
    content: str,
    matchers: list[Any],
) -> tuple[str, list[str]]:
    """Strip content matching exclusion patterns.

    Applies matchers sequentially.  Count-mode matchers that match set the
    cleaned content to empty (the entire chunk is noise).  Line-ratio matchers
    strip individual matching lines while preserving the rest.

    Args:
        content: The original chunk content.
        matchers: List of ``CategoryMatcher`` instances to apply.

    Returns:
        Tuple of (cleaned_content, list_of_matched_category_names).
    """
    cleaned = content
    matched_categories: list[str] = []

    for matcher in matchers:
        if not cleaned:
            break
        matched, cleaned = matcher.match_and_strip(cleaned)
        if matched:
            matched_categories.append(matcher.name)

    return cleaned.strip(), matched_categories


def filter_and_strip_chunks(
    chunks: list[dict[str, Any]],
    matchers: list[Any],
    min_content_length: int = 100,
) -> tuple[list[dict[str, Any]], FilterStats]:
    """Filter and strip chunks using content exclusion matchers.

    Iterates over each chunk, applies all matchers to strip or exclude content,
    then discards chunks whose cleaned content falls below
    *min_content_length* **if a filter actually stripped content** from
    them. Chunks that no filter touched are kept regardless of length,
    since short legitimate content (definitions, captions) shouldn't
    be filtered by accident.
    Original chunk dicts are not mutated — kept chunks are shallow copies with
    the ``content`` key replaced by the cleaned text.

    Args:
        chunks: List of chunk dicts (must have ``id``, ``chunk_index``, ``content``).
        matchers: Compiled ``CategoryMatcher`` instances.
        min_content_length: Minimum cleaned content length to keep a chunk.

    Returns:
        Tuple of (kept_chunks_with_cleaned_content, FilterStats).
    """
    stats = FilterStats(total_chunks=len(chunks))

    if not matchers:
        return list(chunks), stats

    kept: list[dict[str, Any]] = []
    total_stripped_ratio = 0.0

    for chunk in chunks:
        original_content = chunk["content"]
        cleaned, categories = strip_chunk_content(original_content, matchers)

        # Track matched categories
        for cat in categories:
            stats.categories_matched[cat] = stats.categories_matched.get(cat, 0) + 1

        # Track stripped ratio
        original_len = len(original_content)
        stripped_ratio = 1.0 - len(cleaned) / original_len if original_len > 0 else 0.0
        total_stripped_ratio += stripped_ratio

        # Apply min_content_length only when a filter actually touched this
        # chunk. A short chunk that no filter stripped is legitimate content
        # (pithy definitions, short captions) and must not be discarded.
        was_stripped = bool(categories) or len(cleaned) < original_len
        if was_stripped and len(cleaned) < min_content_length:
            stats.excluded_chunks += 1
            continue

        # Shallow copy with cleaned content
        kept_chunk = {**chunk, "content": cleaned}
        kept.append(kept_chunk)

    if stats.total_chunks > 0:
        stats.avg_content_stripped_ratio = total_stripped_ratio / stats.total_chunks

    # Collect regex timeout hits from user-supplied patterns.  Built-in
    # matchers use stdlib ``re.Pattern`` (no timeout mechanism); only
    # ``SafeUserRegex`` instances track ``timeout_count``.  Import is
    # deferred and guarded by TYPE_CHECKING above to avoid a circular-import
    # risk at module load time — the isinstance check uses a runtime import.
    from chaoscypher_core.services.sources.engine.extraction.safe_user_regex import (
        SafeUserRegex,
    )

    stats.regex_timeouts = sum(
        m.pattern.timeout_count
        for m in matchers
        if hasattr(m, "pattern") and isinstance(m.pattern, SafeUserRegex)
    )

    return kept, stats


def build_extraction_groups(
    chunks: list[dict[str, Any]],
    target_tokens: int = 900,
    overlap: int = 1,
) -> list[dict[str, Any]]:
    """Build extraction groups by packing chunks to a token budget.

    Adds chunks to a group until the next chunk would exceed *target_tokens*.
    At least one chunk is included per group even if it alone exceeds the
    target.  Overlap controls context continuity: the last *overlap* chunks
    of group *i* become the first chunks of group *i + 1*.

    Token estimate: ``len(content) // 4`` (consistent with existing codebase).

    Args:
        chunks: Filtered/stripped chunk dicts with ``id`` and ``content``.
        target_tokens: Maximum token budget per group.
        overlap: Number of chunks shared between consecutive groups.

    Returns:
        List of group dicts with keys ``id``, ``group_index``,
        ``small_chunk_ids``, ``combined_content``, ``char_start``, ``char_end``.
    """
    if not chunks:
        return []

    groups: list[dict[str, Any]] = []
    i = 0
    group_index = 0

    while i < len(chunks):
        group_chunks: list[dict[str, Any]] = []
        group_tokens = 0

        j = i
        while j < len(chunks):
            chunk_tokens = len(chunks[j]["content"]) // 4
            if group_tokens + chunk_tokens > target_tokens and group_chunks:
                break
            group_chunks.append(chunks[j])
            group_tokens += chunk_tokens
            j += 1

        combined_content = "\n\n".join(c["content"] for c in group_chunks)
        groups.append(
            {
                "id": generate_id(),
                "group_index": group_index,
                "small_chunk_ids": [c["id"] for c in group_chunks],
                "combined_content": combined_content,
                "char_start": 0,
                "char_end": len(combined_content),
            }
        )
        group_index += 1

        advance = max(1, len(group_chunks) - overlap)
        i += advance

    return groups


def resolve_content_exclusions(
    domain: Any,
) -> list[Any]:
    """Resolve a domain's content exclusions into compiled matchers.

    Combines built-in categories referenced by name with any domain-specific
    custom patterns.  Returns an empty list when the domain is ``None`` or
    does not define content exclusions.

    Args:
        domain: A ``ConfigurableDomain`` instance (or ``None``).

    Returns:
        List of ``CategoryMatcher`` instances ready for use with
        :func:`strip_chunk_content` and :func:`filter_and_strip_chunks`.
    """
    from chaoscypher_core.services.sources.engine.extraction.content_categories import (
        compile_custom_patterns,
        resolve_categories,
    )

    if domain is None or not hasattr(domain, "get_content_exclusions"):
        return []

    config = domain.get_content_exclusions()
    if not config:
        return []

    matchers: list[Any] = []

    category_names = config.get("categories", [])
    if category_names:
        try:
            matchers.extend(resolve_categories(category_names))
        except KeyError as exc:
            # Defense-in-depth: a validated domain should never reach here, but
            # an unknown category name must not halt extraction. Log the full
            # exception (includes the unknown and available sets on the typed
            # subclass) and fall back to no category exclusions.
            domain_name = getattr(domain, "name", "unknown")
            logger.warning(
                "unknown_content_category",
                domain=domain_name,
                error=str(exc),
            )

    custom_patterns = config.get("custom_patterns", [])
    if custom_patterns:
        matchers.extend(compile_custom_patterns(custom_patterns))

    return matchers


# ---------------------------------------------------------------------------
# A6. Quality score caching
# ---------------------------------------------------------------------------


def cache_quality_scores(
    adapter: SqliteAdapter,
    source_id: str,
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    domain_name: str | None,
    database_name: str,
    chunk_count: int = 0,
    settings: EngineSettings | None = None,
) -> dict[str, Any] | None:
    """Compute quality scores and persist them to the source file record.

    Scores are computed once at extraction time so they don't need to be
    recalculated on every page load.  On failure the extraction is **not**
    aborted — scores can be recalculated later on demand.

    Args:
        adapter: Storage adapter (must implement ``update_file``).
        source_id: Source file ID.
        entities: Deduplicated/matched entity list.
        relationships: Remapped relationship list.
        domain_name: Domain name for domain-specific scoring config.
        database_name: Database name for registry lookup.
        chunk_count: Total number of chunks in the source document (for coverage score).
        settings: Engine settings forwarded to ``get_domain_registry`` so the
            correct user-plugin root is used.  Defaults to ``None`` (module
            default).

    Returns:
        Cached scores dict on success, or ``None`` on failure.
    """
    try:
        from chaoscypher_core.services.quality import QualityScorer
        from chaoscypher_core.services.sources.engine.extraction.domains import (
            get_domain_registry,
        )

        quality_config: dict[str, Any] = {}
        if domain_name:
            registry = get_domain_registry(settings=settings, database_name=database_name)
            domain_analyzer = registry.get_domain(domain_name)
            if domain_analyzer and hasattr(domain_analyzer, "get_quality_scoring"):
                quality_config = domain_analyzer.get_quality_scoring()

        # Build entity chunk mentions from extraction data
        entity_chunk_mentions: dict[int, int] = {}
        for idx, entity in enumerate(entities):
            chunks = entity.get("source_chunk_indices", []) or entity.get("source_chunks", [])
            entity_chunk_mentions[idx] = len(chunks) if chunks else 1

        scorer = QualityScorer(quality_config)
        cached_scores = scorer.get_cacheable_scores(
            source_id=source_id,
            entities=entities,
            relationships=relationships,
            entity_chunk_mentions=entity_chunk_mentions,
            chunk_count=chunk_count,
        )
        adapter.update_file(source_id, database_name=database_name, updates=cached_scores)
        logger.info(
            "quality_scores_cached",
            source_id=source_id,
            quality_grade=cached_scores["cached_quality_grade"],
            quality_label=cached_scores["cached_quality_label"],
            scoring_version=cached_scores["cached_scores_version"],
        )
        return cached_scores

    except Exception as err:
        logger.warning(
            "quality_score_caching_failed",
            source_id=source_id,
            error_type=type(err).__name__,
            error_message=str(err),
        )
        return None


__all__ = [
    "FilterStats",
    "aggregate_chunk_results",
    "apply_depth_strategy",
    "build_extraction_groups",
    "cache_quality_scores",
    "detect_extraction_domain",
    "filter_and_strip_chunks",
    "format_extraction_templates",
    "resolve_content_exclusions",
    "strip_chunk_content",
]
