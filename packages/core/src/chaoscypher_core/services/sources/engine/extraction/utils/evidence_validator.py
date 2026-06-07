# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Evidence validation for extraction output.

Validates that extracted entities and relationships are supported by
sentence-level evidence from the source text. Filters out unsupported
triples to ensure extraction quality.

Supports four validation modes via ``evidence_validation_mode`` setting:
- **strict**: Full name/alias substring match required (original behavior).
- **standard** (default): Any significant word (>=4 chars) from name/aliases
  matches. Relationships need only ONE entity name match + rel type keyword.
- **narrative**: Accepts relationships with just one entity name (no keyword
  required) or even zero names if the relationship type keyword appears in
  text. Designed for pronoun-heavy narrative prose.
- **relaxed**: Valid ``sent_ref`` in bounds is sufficient; no name matching.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.quality.counters import (
    QualityCounter,
    increment_quality_counter,
)
from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
    get_referenced_sentences,
    parse_sent_ref,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteringLog,
    )


logger = structlog.get_logger(__name__)

# Minimum word length to count as "significant" in standard mode
_MIN_SIGNIFICANT_WORD_LENGTH = 4

# Punctuation to strip from word boundaries during significant word extraction
_WORD_STRIP_CHARS = ".,;:!?\"'()-"


def _extract_significant_words(
    name: str,
    aliases: list[str] | None = None,
    min_word_length: int = _MIN_SIGNIFICANT_WORD_LENGTH,
) -> list[str]:
    """Extract significant words from entity name and aliases.

    A word is significant if it has >= ``min_word_length`` chars after
    stripping punctuation. All words are lowercased.

    Args:
        name: Primary entity name.
        aliases: Optional alias list.
        min_word_length: Minimum character length for significance.

    Returns:
        List of significant lowercase words.

    """
    words: list[str] = []
    for n in (name, *(aliases or ())):
        for word in n.lower().split():
            stripped = word.strip(_WORD_STRIP_CHARS)
            if len(stripped) >= min_word_length:
                words.append(stripped)
    return words


def _text_contains_name(
    text: str,
    name: str,
    aliases: list[str] | None = None,
    *,
    text_lower: str | None = None,
) -> bool:
    """Check if name or any alias appears in text (case-insensitive).

    Used by **strict** mode — requires full substring match.

    Args:
        text: Text to search in.
        name: Primary entity name.
        aliases: Optional alias list.
        text_lower: Pre-lowered text to avoid redundant ``.lower()`` calls
            in hot loops. When None, computed on the fly.

    Returns:
        True if name or any alias found in text.

    """
    lowered = text_lower if text_lower is not None else text.lower()
    if name.lower() in lowered:
        return True
    if aliases:
        return any(alias.lower() in lowered for alias in aliases)
    return False


def _text_contains_significant_word(
    text: str,
    name: str,
    aliases: list[str] | None = None,
    min_word_length: int = _MIN_SIGNIFICANT_WORD_LENGTH,
    *,
    text_lower: str | None = None,
    precomputed_words: list[str] | None = None,
) -> bool:
    """Check if any significant word from name/aliases appears in text.

    Used by **standard** mode — more lenient than full substring match.
    A word is significant if it has >= ``min_word_length`` chars.
    If the name has no significant words (e.g. "Al"), falls back to full
    substring match.

    Args:
        text: Text to search in.
        name: Primary entity name.
        aliases: Optional alias list.
        min_word_length: Minimum character length for a word to count as
            significant. Defaults to ``_MIN_SIGNIFICANT_WORD_LENGTH``.
        text_lower: Pre-lowered text to avoid redundant ``.lower()`` calls.
        precomputed_words: Pre-extracted significant words to avoid redundant
            name splitting in hot loops.

    Returns:
        True if any significant word found in text.

    """
    lowered = text_lower if text_lower is not None else text.lower()
    significant_words = (
        precomputed_words
        if precomputed_words is not None
        else _extract_significant_words(name, aliases, min_word_length)
    )

    if not significant_words:
        return _text_contains_name(text, name, aliases, text_lower=lowered)

    return any(word in lowered for word in significant_words)


def validate_entity_evidence(
    entity: dict[str, Any],
    sentences: list[str],
    mode: str = "strict",
    min_significant_word_length: int = _MIN_SIGNIFICANT_WORD_LENGTH,
) -> bool:
    """Validate that an entity has valid sentence-level evidence.

    Behavior varies by mode:
    - **strict**: sent_ref valid + full name/alias appears in referenced sentences.
    - **standard**: sent_ref valid + any significant word from name/aliases appears.
    - **relaxed**: sent_ref valid and in bounds (no name matching).

    Args:
        entity: Entity dict with optional ``sent_ref`` field.
        sentences: Full sentence list from the source chunk.
        mode: Validation mode (``strict``, ``standard``, or ``relaxed``).
        min_significant_word_length: Minimum character length for a word to
            count as significant in standard mode.

    Returns:
        True if entity is supported by evidence.

    """
    sent_ref = entity.get("sent_ref")
    if not sent_ref or not parse_sent_ref(sent_ref):
        return False

    ref_sentences = get_referenced_sentences(sent_ref, sentences)
    if not ref_sentences:
        return False

    if mode == "relaxed":
        return True

    joined = " ".join(ref_sentences)
    name = entity.get("name", "")
    aliases = entity.get("aliases", [])

    if mode == "standard":
        return _text_contains_significant_word(
            joined, name, aliases, min_word_length=min_significant_word_length
        )

    # strict (default fallback)
    return _text_contains_name(joined, name, aliases)


def _try_best_effort_parse(raw_sent_ref: str) -> str | None:
    r"""Attempt best-effort extraction of sentence references from malformed sent_ref.

    Handles cases where ``parse_sent_ref`` returns None but the raw value
    contains recognizable ``S\d+`` tokens (e.g. ``"S3 to S5"``, ``"S2;S4"``).

    Args:
        raw_sent_ref: The raw sent_ref string that failed strict parsing.

    Returns:
        A cleaned sent_ref string that ``parse_sent_ref`` can handle,
        or None if no references could be extracted.

    """
    import re as _re

    nums = [int(x) for x in _re.findall(r"S(\d+)", raw_sent_ref)]
    if not nums or not all(n >= 1 for n in nums):
        return None
    if len(nums) == 1:
        return f"S{nums[0]}"
    return f"S{min(nums)}-S{max(nums)}"


def validate_relationship_evidence(  # noqa: PLR0911, C901, PLR0912
    rel: dict[str, Any],
    entities: list[dict[str, Any]],
    sentences: list[str],
    mode: str = "strict",
    min_significant_word_length: int = _MIN_SIGNIFICANT_WORD_LENGTH,
) -> bool:
    r"""Validate that a relationship has valid sentence-level evidence.

    Behavior varies by mode:
    - **strict**: Both entity names must appear in referenced sentences.
    - **standard**: At least ONE entity name must appear (the other may
      be referenced via pronoun or paraphrase).
    - **relaxed**: Valid sent_ref in bounds is sufficient.

    When the primary ``parse_sent_ref`` fails but the raw value contains
    at least one ``S\d+`` token, a best-effort fallback extracts a usable
    range before giving up.

    Args:
        rel: Relationship dict with ``source``, ``target``, ``sent_ref``.
        entities: Full entity list (for name lookup by index).
        sentences: Full sentence list from the source chunk.
        mode: Validation mode (``strict``, ``standard``, or ``relaxed``).
        min_significant_word_length: Minimum character length for a word to
            count as significant in standard mode.

    Returns:
        True if relationship is supported by evidence.

    """
    sent_ref = rel.get("sent_ref")
    if not sent_ref:
        return False

    # Try strict parse first, then best-effort fallback
    if not parse_sent_ref(sent_ref):
        repaired = _try_best_effort_parse(sent_ref)
        if not repaired or not parse_sent_ref(repaired):
            return False
        sent_ref = repaired

    ref_sentences = get_referenced_sentences(sent_ref, sentences)
    if not ref_sentences:
        return False

    if mode == "relaxed":
        return True

    joined = " ".join(ref_sentences)
    # Pre-lower text ONCE — reused by all substring searches below
    joined_lower = joined.lower()

    # Resolve source and target entities
    source_idx = rel.get("source")
    target_idx = rel.get("target")
    if not isinstance(source_idx, int) or not isinstance(target_idx, int):
        return False
    if source_idx < 0 or source_idx >= len(entities):
        return False
    if target_idx < 0 or target_idx >= len(entities):
        return False

    source_entity = entities[source_idx]
    target_entity = entities[target_idx]

    # Choose name matching strategy based on mode
    if mode in ("standard", "narrative"):
        source_found = _text_contains_significant_word(
            joined,
            source_entity.get("name", ""),
            source_entity.get("aliases", []),
            min_word_length=min_significant_word_length,
            text_lower=joined_lower,
        )
        target_found = _text_contains_significant_word(
            joined,
            target_entity.get("name", ""),
            target_entity.get("aliases", []),
            min_word_length=min_significant_word_length,
            text_lower=joined_lower,
        )
    else:
        source_found = _text_contains_name(
            joined,
            source_entity.get("name", ""),
            source_entity.get("aliases", []),
            text_lower=joined_lower,
        )
        target_found = _text_contains_name(
            joined,
            target_entity.get("name", ""),
            target_entity.get("aliases", []),
            text_lower=joined_lower,
        )

    if mode == "narrative":
        # Narrative: accept if any entity name found (no keyword required)
        if source_found or target_found:
            return True
        # No entity names — check for relationship type keyword
        rel_type = (rel.get("type") or "").replace("_", " ").lower()
        rel_words = [w for w in rel_type.split() if len(w) >= min_significant_word_length]
        if rel_words:
            return any(w in joined_lower for w in rel_words)
        return False

    if mode == "standard":
        # Standard: both entities present → valid
        if source_found and target_found:
            return True
        # Fallback: one entity + relationship type keyword in the sentence
        rel_type = (rel.get("type") or "").replace("_", " ").lower()
        rel_words = [w for w in rel_type.split() if len(w) >= min_significant_word_length]
        if (source_found or target_found) and rel_words:
            return any(w in joined_lower for w in rel_words)
        return False

    # strict: both must be present
    return source_found and target_found


async def filter_entities_by_evidence(
    entities: list[dict[str, Any]],
    sentences: list[str],
    mode: str = "strict",
    filtering_log: FilteringLog | None = None,
    min_significant_word_length: int = _MIN_SIGNIFICANT_WORD_LENGTH,
    *,
    adapter: Any | None = None,
    source_id: str | None = None,
    database_name: str | None = None,
) -> tuple[list[dict[str, Any]], dict[int, int | None], dict[str, int]]:
    """Filter entities by sentence-level evidence.

    Each entity that fails validation increments
    ``QualityCounter.EVIDENCE_ENTITIES_DROPPED`` when *adapter* and
    *source_id* are provided. The increment is best-effort: a missing
    *adapter* or *source_id* silently skips the counter so callers that
    do not have a database handle (e.g. tests, benchmarks) still work.

    Args:
        entities: Entity list to filter.
        sentences: Source chunk sentences.
        mode: Validation mode (``strict``, ``standard``, or ``relaxed``).
        filtering_log: Optional log collector for pipeline diagnostics.
        min_significant_word_length: Minimum character length for a word to
            count as significant in standard mode.
        adapter: Storage adapter for quality counter increments (optional).
        source_id: Source row identifier for quality counter increments
            (optional).
        database_name: Database name for quality counter increments
            (optional, defaults to ``"default"`` when adapter is provided).

    Returns:
        Tuple of (valid_entities, index_mapping, stats):
            - valid_entities: Entities with valid evidence.
            - index_mapping: old_idx -> new_idx (or None for dropped).
            - stats: ``{"entities_checked", "entities_dropped"}``.

    """
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteredItem,
    )

    valid: list[dict[str, Any]] = []
    index_mapping: dict[int, int | None] = {}
    dropped = 0
    removed_items: list[FilteredItem] = []

    for old_idx, entity in enumerate(entities):
        if validate_entity_evidence(
            entity, sentences, mode=mode, min_significant_word_length=min_significant_word_length
        ):
            new_idx = len(valid)
            valid.append(entity)
            index_mapping[old_idx] = new_idx
        else:
            index_mapping[old_idx] = None
            dropped += 1
            logger.debug(
                "entity_evidence_dropped",
                entity_name=entity.get("name"),
                sent_ref=entity.get("sent_ref"),
                mode=mode,
            )
            if filtering_log is not None:
                removed_items.append(
                    FilteredItem(
                        item_type="entity",
                        name=entity.get("name", "?"),
                        entity_type=entity.get("type", "?"),
                        reason=f"No valid sentence reference (sent_ref={entity.get('sent_ref')}, mode={mode})",
                        details={"sent_ref": entity.get("sent_ref"), "mode": mode},
                    )
                )
            if adapter is not None and source_id is not None:
                await increment_quality_counter(
                    adapter=adapter,
                    source_id=source_id,
                    database_name=database_name or "default",
                    counter=QualityCounter.EVIDENCE_ENTITIES_DROPPED,
                )

    stats = {"entities_checked": len(entities), "entities_dropped": dropped}

    if dropped > 0:
        logger.info(
            "evidence_filtering_entities",
            checked=len(entities),
            kept=len(valid),
            dropped=dropped,
            mode=mode,
        )

    if filtering_log is not None:
        filtering_log.add_stage(
            "entity_evidence_filter",
            input_count=len(entities),
            removed_count=dropped,
            items=removed_items,
        )

    return valid, index_mapping, stats


async def filter_relationships_by_evidence(
    relationships: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    sentences: list[str],
    mode: str = "strict",
    filtering_log: FilteringLog | None = None,
    min_significant_word_length: int = _MIN_SIGNIFICANT_WORD_LENGTH,
    *,
    adapter: Any | None = None,
    source_id: str | None = None,
    database_name: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Filter relationships by sentence-level evidence.

    Each relationship that fails validation increments
    ``QualityCounter.EVIDENCE_RELATIONSHIPS_DROPPED`` when *adapter* and
    *source_id* are provided. The increment is best-effort: a missing
    *adapter* or *source_id* silently skips the counter so callers that
    do not have a database handle (e.g. tests, benchmarks) still work.

    Args:
        relationships: Relationship list to filter.
        entities: Current entity list (already evidence-filtered).
        sentences: Source chunk sentences.
        mode: Validation mode (``strict``, ``standard``, or ``relaxed``).
        filtering_log: Optional log collector for pipeline diagnostics.
        min_significant_word_length: Minimum character length for a word to
            count as significant in standard mode.
        adapter: Storage adapter for quality counter increments (optional).
        source_id: Source row identifier for quality counter increments
            (optional).
        database_name: Database name for quality counter increments
            (optional, defaults to ``"default"`` when adapter is provided).

    Returns:
        Tuple of (valid_relationships, stats).

    """
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteredItem,
    )

    valid: list[dict[str, Any]] = []
    dropped = 0
    removed_items: list[FilteredItem] = []

    for rel in relationships:
        if validate_relationship_evidence(
            rel,
            entities,
            sentences,
            mode=mode,
            min_significant_word_length=min_significant_word_length,
        ):
            valid.append(rel)
        else:
            dropped += 1
            logger.debug(
                "relationship_evidence_dropped",
                source=rel.get("source"),
                target=rel.get("target"),
                sent_ref=rel.get("sent_ref"),
                mode=mode,
            )
            if filtering_log is not None:
                src = rel.get("source", "?")
                tgt = rel.get("target", "?")
                # Resolve integer indices to entity names
                if isinstance(src, int) and 0 <= src < len(entities):
                    src = entities[src].get("name", f"Entity {src}")
                if isinstance(tgt, int) and 0 <= tgt < len(entities):
                    tgt = entities[tgt].get("name", f"Entity {tgt}")
                removed_items.append(
                    FilteredItem(
                        item_type="relationship",
                        name=f"{src} -> {tgt}",
                        entity_type=rel.get("type", "?"),
                        reason=f"No valid sentence evidence (sent_ref={rel.get('sent_ref')}, mode={mode})",
                        details={"sent_ref": rel.get("sent_ref"), "mode": mode},
                    )
                )
            if adapter is not None and source_id is not None:
                await increment_quality_counter(
                    adapter=adapter,
                    source_id=source_id,
                    database_name=database_name or "default",
                    counter=QualityCounter.EVIDENCE_RELATIONSHIPS_DROPPED,
                )

    stats = {"relationships_checked": len(relationships), "relationships_dropped": dropped}

    if dropped > 0:
        logger.info(
            "evidence_filtering_relationships",
            checked=len(relationships),
            kept=len(valid),
            dropped=dropped,
            mode=mode,
        )

    if filtering_log is not None:
        filtering_log.add_stage(
            "relationship_evidence_filter",
            input_count=len(relationships),
            removed_count=dropped,
            items=removed_items,
        )

    return valid, stats


__all__: list[str] = [
    "filter_entities_by_evidence",
    "filter_relationships_by_evidence",
    "validate_entity_evidence",
    "validate_relationship_evidence",
]
