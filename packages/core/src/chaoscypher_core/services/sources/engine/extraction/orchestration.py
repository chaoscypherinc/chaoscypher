# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared Extraction Orchestration Logic.

Pure business logic functions used by both the Cortex worker pipeline and the
CLI offline pipeline.  These functions are framework-agnostic — they accept
plain dicts/lists and concrete values, never framework-specific settings objects.

Functions:
    aggregate_chunk_results: Merge per-chunk entities/relationships into a global list
        and stamp each with the evidence its ``sent_ref`` cites.
    group_fallback_chunk_ids: Per group, the chunk unresolved evidence is cited on.
    build_group_text: The one builder of an extraction group's numbered text.
    detect_extraction_domain: Detect (or take as forced) the packs a source extracts with.
    format_extraction_templates: Pre-compute formatted template strings for LLM prompts.
    apply_depth_strategy: Filter hierarchical groups based on extraction depth.
    cache_quality_scores: Compute and return quality scores for caching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, get_args

import structlog

from chaoscypher_core.models import AnalysisDepth
from chaoscypher_core.services.sources.engine.deduplication import name_identity
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    ATTESTED_NAMES_KEY,
    GIVEN_ALIASES_KEY,
)
from chaoscypher_core.services.sources.engine.extraction.extraction_plan import domain_guidance
from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
    GROUP_TEXT_SEPARATOR,
    resolve_evidence,
    split_into_sentences,
)
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# A1. Chunk aggregation
# ---------------------------------------------------------------------------


def group_fallback_chunk_ids(
    completed_chunks: list[dict[str, Any]],
    *,
    small_chunk_ids_key: str = "small_chunk_ids",
) -> list[str | None]:
    """Per group, the chunk an item with no resolved evidence is cited on.

    The group's first small chunk that no earlier group holds: consecutive
    groups share their overlap chunks (the last chunks of group *i* open
    group *i + 1*), so the first chunk of every group after the first is the
    previous group's text, and defaulting to it piled every unresolved
    citation onto the overlap. Groups are ordered by ``chunk_index`` (the
    group position) when every group carries one, else by list position. A
    group made only of shared chunks falls back to its first chunk; a group
    with no chunks to ``None``.

    Args:
        completed_chunks: The groups' result dicts, as aggregated.
        small_chunk_ids_key: Key for each group's small-chunk ids.

    Returns:
        One chunk id (or ``None``) per group, aligned with ``completed_chunks``.
    """
    positions = [t.get("chunk_index") for t in completed_chunks]
    if all(isinstance(p, int) and not isinstance(p, bool) for p in positions):
        order = sorted(range(len(completed_chunks)), key=lambda i: (positions[i], i))
    else:
        order = list(range(len(completed_chunks)))
    seen: set[str] = set()
    out: list[str | None] = [None] * len(completed_chunks)
    for i in order:
        ids = [str(c) for c in completed_chunks[i].get(small_chunk_ids_key) or [] if c]
        out[i] = next((c for c in ids if c not in seen), ids[0] if ids else None)
        seen.update(ids)
    return out


def stamp_group_evidence(
    items: list[dict[str, Any]],
    *,
    sentences: list[str] | None,
    small_chunk_ids: list[str],
    chunks_by_id: Mapping[str, Mapping[str, Any]] | None,
    fallback_chunk_id: str | None,
    group_text: str = "",
) -> None:
    """Stamp ``evidence`` and ``cited_chunk_id`` on one group's items in place, and attested names.

    An entity (an item with a ``name``) also gets :data:`ATTESTED_NAMES_KEY`:
    its name and aliases written verbatim in the text it was read from (the
    group's text and its stored chunks), which the survivor ranking prefers
    to a spelling the extractor made up (``name_identity.pick_survivor``);
    and :data:`GIVEN_ALIASES_KEY`: the aliases that are evidence of who it is
    (``name_identity.evidence_aliases``: written in that text, and listed by
    the group's output as no entity of its own).

    Args:
        items: The group's entity or relationship dicts (each may carry ``sent_ref``).
        sentences: The group's numbered sentences.
        small_chunk_ids: The group's small-chunk ids in group order.
        chunks_by_id: Stored small chunks by id (``content``, optional
            ``chunk_metadata``); missing ids fall back to text-only evidence.
        fallback_chunk_id: The chunk an item whose evidence names no chunk
            is cited on (:func:`group_fallback_chunk_ids`).
        group_text: The text the group's model read.

    """
    group_chunks: list[Mapping[str, Any]] = [
        chunks_by_id[cid] for cid in small_chunk_ids if chunks_by_id and cid in chunks_by_id
    ]
    texts = [group_text, *(str(c.get("content") or "") for c in group_chunks)]
    labels = [str(item.get("name") or "") for item in items]
    for item in items:
        evidence = resolve_evidence(item.get("sent_ref"), sentences, group_chunks)
        item["evidence"] = evidence
        item["cited_chunk_id"] = next(
            (e["chunk_id"] for e in evidence if e.get("chunk_id")), fallback_chunk_id
        )
        if item.get("name"):
            names = [item["name"], *(a for a in item.get("aliases") or [] if isinstance(a, str))]
            # through the module: the registered guard (``name_guards``) replaces it here too
            item[ATTESTED_NAMES_KEY] = name_identity.attested_names(names, texts)
            own = name_identity.fold(str(item["name"]))
            others = [n for n in labels if n and name_identity.fold(n) != own]
            item[GIVEN_ALIASES_KEY] = name_identity.evidence_aliases(
                names[1:], item[ATTESTED_NAMES_KEY], others
            )


def aggregate_chunk_results(
    completed_chunks: list[dict[str, Any]],
    *,
    entities_key: str = "raw_entities",
    relationships_key: str = "raw_relationships",
    text_key: str = "input_text",
    sentences_key: str = "chunk_sentences",
    small_chunk_ids_key: str = "small_chunk_ids",
    chunks_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate per-chunk extraction results into a global entity/relationship list.

    Relationships returned by ``AIEntityExtractor.extract_single_chunk`` use
    **chunk-local integer indices** into the chunk's entity list.  This function
    remaps those indices so they reference positions in the combined entity list.

    It is also where citation evidence is resolved, once per extraction group
    while that group's numbered sentences are at hand: every entity and
    relationship gets ``evidence`` (see ``resolve_evidence``) and
    ``cited_chunk_id`` (the first evidence chunk, else the group's first small
    chunk no earlier group holds: :func:`group_fallback_chunk_ids`). The commit only reads these; ``chunk_index`` is the extraction
    group's position and is never a stored chunk index.

    Args:
        completed_chunks: List of chunk result dicts.  Each dict must contain
            *entities_key* (list of entity dicts) and *relationships_key*
            (list of relationship dicts with integer ``source``/``target``).
        entities_key: Key for the entity list inside each chunk dict.
        relationships_key: Key for the relationship list inside each chunk dict.
        text_key: Key for the chunk text.
        sentences_key: Key for the group's numbered sentences. When absent the
            sentences are re-split from *text_key* (the text that was numbered).
        small_chunk_ids_key: Key for the group's small-chunk ids.
        chunks_by_id: Stored small chunks by id, used to place each cited
            sentence in its chunk.

    Returns:
        Dictionary with keys:
        - ``entities``: Combined entity list (global indices).
        - ``relationships``: Combined relationship list (global indices).
        - ``chunk_texts``: List of per-chunk text strings.
        - ``dropped_relationships_invalid_index``: Count of relationships silently
          dropped because their ``source``/``target`` was out of bounds or a
          non-integer (bool) — used by callers to increment the
          ``AGGREGATOR_RELATIONSHIPS_DROPPED`` quality counter.
    """
    all_entities: list[dict[str, Any]] = []
    all_relationships: list[dict[str, Any]] = []
    chunk_texts: list[str] = []
    entity_offset = 0
    dropped_count = 0

    fallbacks = group_fallback_chunk_ids(completed_chunks, small_chunk_ids_key=small_chunk_ids_key)
    for task, fallback_chunk_id in zip(completed_chunks, fallbacks, strict=True):
        chunk_entities = task.get(entities_key) or []
        chunk_relationships = task.get(relationships_key) or []

        group_text = task.get(text_key) or ""
        chunk_texts.append(group_text)
        sentences = task.get(sentences_key)
        if sentences is None and group_text:
            sentences = split_into_sentences(group_text)
        evidence_args: dict[str, Any] = {
            "sentences": sentences,
            "small_chunk_ids": list(task.get(small_chunk_ids_key) or []),
            "chunks_by_id": chunks_by_id,
            "fallback_chunk_id": fallback_chunk_id,
            "group_text": group_text,
        }
        stamp_group_evidence(chunk_entities, **evidence_args)
        stamp_group_evidence(chunk_relationships, **evidence_args)

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
        "dropped_relationships_invalid_index": dropped_count,
    }


# ---------------------------------------------------------------------------
# A2. Domain detection
# ---------------------------------------------------------------------------


def _build_ranking(
    candidates: Sequence[tuple[Any, float]],
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


def _packs_guidance(registry: Any, packs: Sequence[str], domain: Any) -> tuple[str, str]:
    """``(entity, relationship)`` guidance of the floor plus ``packs``; one domain's for a registry without a floor."""
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack

    try:
        return DomainStack.from_stored(registry, packs).guidance()
    except Exception:  # a registry without a floor (a stand-in): the one domain's guidance
        return domain_guidance(domain)


def detect_extraction_domain(
    registry: Any,
    forced_domain: str | None,
    sample_text: str,
    filename: str,
    metadata: dict[str, Any] | None = None,
    *,
    forced_packs: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Detect (or take as forced) the packs a source extracts with, on the floor.

    Args:
        registry: ``DomainRegistry`` instance.
        forced_domain: The forced packs in the one-name form (a pack's name,
            or the packs' label ``historical+literary``; ``generic`` is the
            floor alone, None or ``__auto__`` detects). Ignored when
            ``forced_packs`` is given.
        sample_text: Representative text sample for auto-detection.
        filename: Original filename (used as a detection signal).
        metadata: Optional document metadata for auto-detection.
        forced_packs: The forced packs (``()`` is the floor alone, chosen);
            None with no ``forced_domain`` detects
            (:meth:`DomainRegistry.propose_packs`).

    Returns:
        Dictionary with keys:
        - ``packs``: The packs, sorted when forced, by score when proposed;
          ``()`` is the floor alone.
        - ``domain``: The first pack's domain instance, else the floor's
          (or ``None``).
        - ``detected_domain``: ``packs[0]``, else ``generic``.
        - ``confidence``: Detection confidence (1.0 when forced).
        - ``entity_guidance``: Entity guidance of the floor and the packs.
        - ``relationship_guidance``: Relationship guidance likewise.
        - ``ranking``: List of ``{domain: str, score: float}`` (highest first);
          always has at least one entry (``ranking[0]`` synthesized from the
          resolved domain when no candidate cleared detection).
        - ``low_confidence``: ``True`` when no pack cleared the evidence floor.
        - ``reason``: The proposal's reason (``forced`` when forced).
    """
    from chaoscypher_core.services.sources.engine.extraction.domains.packs import (
        first_domain_name,
        ordered_packs,
        packs_of_name,
    )

    forced = (
        ordered_packs(forced_packs) if forced_packs is not None else packs_of_name(forced_domain)
    )
    if forced is not None:
        name = first_domain_name(forced)
        domain = registry.get_domain(name)
        entity_guidance, relationship_guidance = _packs_guidance(registry, forced, domain)
        logger.info(
            "domain_forced",
            packs=list(forced),
            has_entity_guidance=bool(entity_guidance),
            has_relationship_guidance=bool(relationship_guidance),
        )
        auto_candidates = registry.rank_domains(sample_text, filename, metadata or {})
        ranking = _build_ranking(auto_candidates, fallback_name=name, fallback_score=1.0)
        return {
            "packs": forced,
            "domain": domain,
            "detected_domain": name,
            "confidence": 1.0,
            "entity_guidance": entity_guidance,
            "relationship_guidance": relationship_guidance,
            "ranking": ranking,
            "low_confidence": False,
            "reason": "forced",
        }

    # Auto-detect: every pack over the floor, at most two.
    proposal = registry.propose_packs(sample_text, filename, metadata or {})
    packs = tuple(proposal.packs)
    name = first_domain_name(packs)
    confidence = registry.proposal_confidence(proposal)
    domain = registry.get_domain(name)
    entity_guidance, relationship_guidance = _packs_guidance(registry, packs, domain)
    ranking = _build_ranking(proposal.ranking, fallback_name=name, fallback_score=confidence)

    logger.info(
        "domain_detected",
        packs=list(packs),
        detected_domain=name,
        confidence=confidence,
        reason=proposal.reason,
        has_entity_guidance=bool(entity_guidance),
        has_relationship_guidance=bool(relationship_guidance),
        sample_length=len(sample_text),
    )

    return {
        "packs": packs,
        "domain": domain,
        "detected_domain": name,
        "confidence": confidence,
        "entity_guidance": entity_guidance,
        "relationship_guidance": relationship_guidance,
        "ranking": ranking,
        "low_confidence": proposal.low_confidence,
        "reason": proposal.reason,
    }


# ---------------------------------------------------------------------------
# A3. Template formatting
# ---------------------------------------------------------------------------


def format_extraction_templates(
    stack: DomainStack,
    *,
    examples_enabled: bool = True,
    examples_max_chars: int = 800,
) -> dict[str, str]:
    """Pre-compute the formatted type lists and example strings of a stack for the LLM prompts.

    Args:
        stack: The floor and the source's packs (:class:`DomainStack`).
        examples_enabled: Whether to include the examples.
        examples_max_chars: The cap on each member's example section.

    Returns:
        Dictionary with keys:
        - ``node_templates``: The entity type lines (:meth:`DomainStack.node_lines`).
        - ``edge_templates``: The relationship type lines (:meth:`DomainStack.edge_lines`).
        - ``entity_examples``: Entity examples (empty string if disabled).
        - ``relationship_examples``: Relationship examples (empty string if disabled).
    """
    entity_examples, relationship_examples = (
        stack.examples(max_chars=examples_max_chars) if examples_enabled else ("", "")
    )
    return {
        "node_templates": "\n".join(stack.node_lines()),
        "edge_templates": "\n".join(stack.edge_lines()),
        "entity_examples": entity_examples,
        "relationship_examples": relationship_examples,
    }


# ---------------------------------------------------------------------------
# A4. Depth strategy
# ---------------------------------------------------------------------------


# ``chaoscypher_core.models.AnalysisDepth`` is the one depth vocabulary; the
# Cortex request models, the CLI choices and the MCP tool schemas read it too
# (seven hand copies drifted apart until 2026-10-12).
_VALID_DEPTH_VALUES: frozenset[str] = frozenset(get_args(AnalysisDepth))


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
            ``ValidationError`` (previously unrecognised values were
            silently treated as ``"full"``).
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
        # Compare against the *stripped* original: strip_chunk_content always
        # trims surrounding whitespace, so a chunk that no matcher touched but
        # merely had leading/trailing whitespace would otherwise look
        # "stripped" — and a short-but-legitimate chunk (definition, caption)
        # would then be wrongly excluded despite the documented intent above.
        was_stripped = bool(categories) or len(cleaned) < len(original_content.strip())
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


def build_group_text(
    small_chunks: list[dict[str, Any]],
    matchers: list[Any] | None = None,
) -> str:
    """Build the text of one extraction group: the text the model numbers.

    The single builder for both extraction paths (``build_extraction_groups``
    for the CLI/MCP/import planner and the queue's ``_extract_chunk_handler``,
    which re-fetches the raw rows), so ``S<n>`` means the same sentence on
    both. Each chunk's content is stripped by the content-exclusion
    *matchers* (pass none when the chunks were already stripped by
    ``filter_and_strip_chunks``), empty pieces are dropped, the rest are
    joined with ``GROUP_TEXT_SEPARATOR`` and cleaned with
    ``prepare_text_for_extraction``.

    Args:
        small_chunks: The group's small chunks in order, each with ``content``.
        matchers: Content-exclusion matchers to strip each chunk with.

    Returns:
        The group text sent to the model.

    """
    from chaoscypher_core.services.sources.engine.extraction.utils.text_preparation import (
        prepare_text_for_extraction,
    )

    pieces: list[str] = []
    for chunk in small_chunks:
        content = chunk.get("content") or ""
        if matchers:
            content, _categories = strip_chunk_content(content, matchers)
        if content:
            pieces.append(content)
    return prepare_text_for_extraction(GROUP_TEXT_SEPARATOR.join(pieces))


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

        combined_content = build_group_text(group_chunks)
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
        from chaoscypher_core.services.quality import QualityScorer, build_entity_chunk_mentions
        from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
            quality_config_of,
        )

        quality_config = quality_config_of(
            domain_name, settings=settings, database_name=database_name
        )

        # Canonical chunk-mention map (source_chunk_indices + legacy aliases).
        entity_chunk_mentions = build_entity_chunk_mentions(entities)

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
    "build_group_text",
    "cache_quality_scores",
    "detect_extraction_domain",
    "filter_and_strip_chunks",
    "format_extraction_templates",
    "group_fallback_chunk_ids",
    "resolve_content_exclusions",
    "strip_chunk_content",
]
