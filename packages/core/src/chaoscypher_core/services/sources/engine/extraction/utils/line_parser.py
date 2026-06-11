# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

r"""Line-based parser for LLM extraction output.

Parses pipe-delimited line format for entities, relationships, properties, and renames.
Uses greedy parsing (volatile fields LAST) to handle unescaped pipes in prose text.

Format specifications (V1 — legacy):
- Entity: E|name|type|aliases|confidence|description
- Relationship: R|source|target|type|confidence|justification
- Property: P|entity_name|key|value
- Rename: A|old_name|new_name|confidence|reasoning

Format specifications (V2 — evidence-gated):
- Entity: E|name|type|aliases|confidence|sent_ref|description
- Relationship: R|source|target|type|confidence|sent_ref|justification
- Property: P|entity_index|key|value  (index is 0-based int)
- Rename: A|old_name|new_name|confidence|sent_ref|reasoning

Auto-detection: V2 is detected when field count is 6+ and the 5th field
matches the sentence reference pattern ``S\d+(-S\d+)?``.

Example:
    E|Prince Andrei|Character|Andrei; Prince Andrew|0.9|S1-S3|The eldest son of Prince Bolkonsky
    R|0|1|spouse_of|0.9|S2|They are married in the novel
    P|0|title|Prince
    A|The System|Linux Kernel|0.9|S5|Context makes clear this refers to the Linux kernel
"""

import re
from typing import Any

import structlog

from chaoscypher_core.services.sources.engine.extraction.utils.constants import (
    LOOP_MAX_OUT_OF_BOUNDS,
    LOOP_MAX_SOURCE_TYPE_REPEAT,
)


logger = structlog.get_logger(__name__)

# Minimum character length for an alias to be accepted
_DEFAULT_MIN_ALIAS_LENGTH = 2

# Known null/placeholder values that LLMs output as aliases
_NULL_ALIASES = frozenset({"n/a", "none", "unknown", "null", "n\\a", "na", "nil"})

# LLM meta-commentary prefixes that leak into alias fields
_META_PREFIXES = ("should be", "also known as", "formerly", "previously")

# Pattern to match markdown bullet prefixes: "- ", "* ", "1. ", "2) ", etc.
_BULLET_PREFIX_PATTERN = re.compile(r"^(?:[-*+]|\d+[.)])?\s*")

# Pattern to match a balanced outer Markdown emphasis wrapper: **bold**,
# __bold__, *italic*, _italic_, or `code`. ``(.+?)`` is lazy but anchored
# by ``$`` so the inner group must extend to the matching closer. The
# ``**``/``__`` alternatives come first so they're preferred over single
# ``*``/``_`` matches when both are present.
_MARKDOWN_WRAPPER_PATTERN = re.compile(r"^(\*\*|__|\*|_|`)(.+?)\1$")

# Pattern to detect sentence references: S3, S2-S4, S1,S2,S3, "S1, S3",
# "S4-S7", "S4 - S7", etc. Tolerates arbitrary whitespace around the comma
# and dash separators so LLM outputs like ``S4, S11`` (space after comma)
# aren't rejected as malformed — mirrors the tolerance of the authoritative
# parse_sent_ref() parser downstream.
#
# ``;`` is accepted as an alternative to ``,`` because the prompt teaches
# ``;`` as the alias delimiter, and the model generalizes that convention
# to sent_ref lists (e.g. ``S1;S15``). ``;`` has no other valid meaning
# inside a sent_ref so accepting it is unambiguous.
_SENT_REF_PATTERN = re.compile(r"^S\d+(?:\s*[-,;]\s*S?\d+)*$")


def _looks_like_sent_ref(value: str) -> bool:
    """Check if value looks like a sentence reference (S3 or S2-S4).

    Args:
        value: String to check.

    Returns:
        True if value matches the sentence reference pattern.

    """
    return bool(_SENT_REF_PATTERN.match(value.strip()))


def _strip_bullet_prefix(line: str) -> str:
    """Strip common markdown bullet/list prefixes from a line.

    Handles various formats that models commonly add:
    - "- E|..." -> "E|..."
    - "* E|..." -> "E|..."
    - "1. E|..." -> "E|..."
    - "2) E|..." -> "E|..."

    Args:
        line: Line that may have a bullet prefix

    Returns:
        Line with bullet prefix removed, or original line if no prefix

    """
    return _BULLET_PREFIX_PATTERN.sub("", line)


def _strip_markdown_decoration(value: str) -> str:
    """Strip balanced Markdown emphasis wrappers from the outer edges of a value.

    Some LLMs (observed: ``ministral-3:14b``) decorate pipe-delimited fields
    with Markdown emphasis — ``**bold**``, ``__bold__``, ``*italic*``,
    ``_italic_``, ```code```. Left in place these wrappers fragment
    the graph: ``**interacts_with**`` and ``interacts_with`` become two
    distinct edge types; ``**Prince Vasíli**`` becomes a separate node from
    any non-decorated variant.

    Only fully-balanced *outer* wrappers are stripped — inner emphasis on
    substrings is preserved, so descriptions like
    ``"A class **named Foo**"`` are untouched. Iterates so nested wrappers
    like ``***Anna***`` (bold+italic) are fully unwrapped. Whitespace inside
    the wrapper is stripped on each pass (it's insertion noise, not content).

    Args:
        value: Field value that may have Markdown emphasis wrappers.

    Returns:
        Value with outer Markdown wrappers (and their inner whitespace) removed.

    """
    prev: str | None = None
    while value != prev:
        prev = value
        match = _MARKDOWN_WRAPPER_PATTERN.match(value)
        if match:
            value = match.group(2).strip()
    return value


def unescape_field(value: str) -> str:
    r"""Unescape pipe and backslash characters in a field value.

    Also strips outer Markdown emphasis wrappers (``**bold**``, ``*italic*``,
    etc.) that some LLMs add to pipe-delimited field values. See
    ``_strip_markdown_decoration`` for the rules.

    Args:
        value: Field value that may contain escaped characters or Markdown
            emphasis wrappers.

    Returns:
        Unescaped value with \| -> | and \\ -> \, with outer Markdown
        wrappers removed.

    """
    # Use placeholder to handle overlapping escapes correctly
    unescaped = value.replace("\\|", "\x00").replace("\\\\", "\\").replace("\x00", "|")
    return _strip_markdown_decoration(unescaped)


def safe_float(value: str, default: float = 0.8) -> float:
    """Parse confidence score with fallback for non-numeric values.

    Handles various LLM output formats:
    - "0.9" -> 0.9
    - "0.9 (High)" -> 0.9
    - "Confidence: 0.9" -> 0.9
    - "High" -> 0.8 (default)
    - "" -> 0.8 (default)

    Args:
        value: String that may contain a float
        default: Default value if parsing fails

    Returns:
        Parsed float or default value

    """
    if not value or not value.strip():
        return default
    # Extract first numeric value from string
    match = re.search(r"(\d+(?:\.\d+)?)", value.strip())
    if match:
        try:
            parsed = float(match.group(1))
            # Clamp to valid confidence range
            return max(0.0, min(1.0, parsed))
        except ValueError:
            return default
    return default


def parse_entity_line(
    line: str, minimum_alias_length: int = _DEFAULT_MIN_ALIAS_LENGTH
) -> dict[str, Any] | None:
    """Parse an entity line into a dictionary.

    Format: ``E|name|type|aliases|confidence|sent_ref|description``

    The sent_ref must be a sentence reference like ``S3`` or ``S2-S5`` —
    the LLM is prompted to always supply it. Lines without a valid
    sent_ref are rejected as malformed.

    Uses maxsplit to ensure description (last field) can contain unescaped pipes.

    Args:
        line: Line starting with "E|"
        minimum_alias_length: Minimum character length for an alias to be
            accepted. Defaults to ``_DEFAULT_MIN_ALIAS_LENGTH``.

    Returns:
        Entity dictionary with name, type, description, aliases, confidence,
        and sent_ref; or None if parsing fails

    """
    if not line.startswith("E|"):
        return None

    parts = line[2:].split("|", 5)

    if len(parts) != 6:
        logger.warning(
            "entity_line_malformed",
            reason="part_count",
            line=line[:100],
            parts_found=len(parts),
            expected=6,
        )
        return None
    if not _looks_like_sent_ref(parts[4]):
        logger.warning(
            "entity_line_malformed",
            reason="bad_sent_ref",
            line=line[:100],
            sent_ref=parts[4][:40],
        )
        return None

    name_raw, type_raw, aliases_str, confidence_str, sent_ref_raw, description_raw = parts
    sent_ref = sent_ref_raw.strip()

    name = unescape_field(name_raw.strip())
    entity_type = unescape_field(type_raw.strip())

    if not name:
        logger.warning("entity_line_missing_name", line=line[:100])
        return None

    if not entity_type:
        entity_type = "UNKNOWN"

    parsed_aliases, rejected = _parse_aliases(
        aliases_str, minimum_alias_length=minimum_alias_length
    )
    proper_aliases, descriptors = _separate_descriptors(parsed_aliases)

    entity: dict[str, Any] = {
        "name": name,
        "type": entity_type,
        "description": unescape_field(description_raw.strip()),
        "aliases": proper_aliases,
        "confidence": safe_float(confidence_str),
        "sent_ref": sent_ref,
    }
    if descriptors:
        entity["descriptors"] = descriptors
    if rejected:
        entity["rejected_aliases"] = rejected
        logger.debug("aliases_rejected", entity_name=name, rejected=rejected)
    return entity


def _is_valid_alias(alias: str) -> bool:
    """Check whether an alias is a valid entity name (not LLM garbage).

    Rejects aliases that contain structural artifacts, null placeholders,
    or LLM meta-commentary that leaked into the alias field.

    Args:
        alias: Cleaned alias string (already length-filtered)

    Returns:
        True if alias is a valid entity alias

    """
    if alias.lower() in _NULL_ALIASES:
        return False
    if any(c in alias for c in "()[]{}"):
        return False
    if "=" in alias:
        return False
    alias_lower = alias.lower()
    return not any(alias_lower.startswith(p) for p in _META_PREFIXES)


def _parse_aliases(
    aliases_str: str, minimum_alias_length: int = _DEFAULT_MIN_ALIAS_LENGTH
) -> tuple[list[str], list[str]]:
    """Parse alias string, splitting on semicolons and comma-space.

    Primary delimiter is semicolon. Each semicolon-delimited segment is
    further split on ", " (comma followed by space) to handle LLMs that
    use commas despite the prompt specifying semicolons. Individual parts
    shorter than ``minimum_alias_length`` characters are discarded. Invalid
    aliases (garbage, null values, meta-commentary) are filtered via
    ``_is_valid_alias()``.

    Args:
        aliases_str: Alias string (semicolon or comma separated)
        minimum_alias_length: Minimum character length for an alias to be
            accepted. Defaults to ``_DEFAULT_MIN_ALIAS_LENGTH``.

    Returns:
        Tuple of (valid_aliases, rejected_aliases)

    """
    if not aliases_str or not aliases_str.strip():
        return [], []

    result: list[str] = []
    rejected: list[str] = []
    # First split on semicolons
    for segment in aliases_str.split(";"):
        stripped_segment = segment.strip()
        if not stripped_segment:
            continue
        # Then split each segment on comma-space
        for part in stripped_segment.split(", "):
            cleaned = unescape_field(part.strip())
            if len(cleaned) >= minimum_alias_length:
                if _is_valid_alias(cleaned):
                    result.append(cleaned)
                else:
                    rejected.append(cleaned)
    return result, rejected


def _is_proper_name_alias(alias: str) -> bool:
    """Determine whether an alias is a proper name (vs a descriptive phrase).

    Proper names have at least one word starting with an uppercase letter.
    Descriptive phrases are all-lowercase or contain possessive markers.

    Args:
        alias: Alias string to classify

    Returns:
        True if alias looks like a proper name

    """
    # Possessive phrases like "Boris's mother" are descriptors
    if "'s " in alias:
        return False
    # Check if any word starts with uppercase
    return any(word[0].isupper() for word in alias.split() if word)


def _separate_descriptors(aliases: list[str]) -> tuple[list[str], list[str]]:
    """Separate proper-name aliases from descriptive phrases.

    Args:
        aliases: List of alias strings

    Returns:
        Tuple of (proper_aliases, descriptors)

    """
    proper: list[str] = []
    descriptors: list[str] = []
    for alias in aliases:
        if _is_proper_name_alias(alias):
            proper.append(alias)
        else:
            descriptors.append(alias)
    return proper, descriptors


def _parse_source_target(value: str) -> int | None:
    """Parse source/target as a 0-based integer entity index.

    Returns ``None`` for non-integer values — relationships referencing
    entities by name (the old V1 format) are no longer supported.

    Args:
        value: Source or target field value

    Returns:
        Integer index, or ``None`` if value is not a valid integer.

    """
    stripped = value.strip()
    try:
        return int(stripped)
    except ValueError:
        return None


def parse_relationship_line(line: str) -> dict[str, Any] | None:
    """Parse a relationship line into a dictionary.

    Format: ``R|source_index|target_index|type|confidence|sent_ref|justification``

    Source and target are 0-based integer entity indices. The sent_ref
    must be a sentence reference like ``S3`` or ``S2-S5`` — the LLM is
    prompted to always supply it. Lines without a valid sent_ref or with
    non-integer source/target are rejected as malformed.

    Uses maxsplit to ensure justification (last field) can contain unescaped pipes.

    Args:
        line: Line starting with "R|"

    Returns:
        Relationship dictionary with source, target, type, confidence,
        justification, and sent_ref; or None if parsing fails

    """
    if not line.startswith("R|"):
        return None

    parts = line[2:].split("|", 5)

    if len(parts) != 6:
        logger.warning(
            "relationship_line_malformed",
            reason="part_count",
            line=line[:100],
            parts_found=len(parts),
            expected=6,
        )
        return None
    if not _looks_like_sent_ref(parts[4]):
        logger.warning(
            "relationship_line_malformed",
            reason="bad_sent_ref",
            line=line[:100],
            sent_ref=parts[4][:40],
        )
        return None

    source_str, target_str, rel_type_raw, confidence_str, sent_ref_raw, justification_raw = parts
    sent_ref = sent_ref_raw.strip()

    source = _parse_source_target(source_str)
    target = _parse_source_target(target_str)
    rel_type = unescape_field(rel_type_raw.strip())

    if source is None or target is None:
        logger.warning(
            "relationship_line_invalid_indices",
            line=line[:100],
        )
        return None

    if not rel_type:
        rel_type = "related_to"

    return {
        "source": source,
        "target": target,
        "type": rel_type,
        "confidence": safe_float(confidence_str),
        "justification": unescape_field(justification_raw.strip()),
        "sent_ref": sent_ref,
    }


def parse_property_line(line: str) -> dict[str, Any] | None:
    """Parse a property line into a dictionary.

    Format: ``P|entity_index|key|value`` (0-based index attachment).

    Lines using a name-based ``entity_name`` reference (the old V1 format)
    are no longer supported and will be rejected.

    Uses maxsplit=2 so value (last field) can contain unescaped pipes.

    Args:
        line: Line starting with "P|"

    Returns:
        Property dictionary with entity_index, key, value, or None if
        parsing fails.

    """
    if not line.startswith("P|"):
        return None

    parts = line[2:].split("|", 2)

    if len(parts) < 3:
        logger.warning(
            "property_line_incomplete",
            line=line[:100],
            parts_found=len(parts),
            expected=3,
        )
        return None

    entity_ref_raw, key, value = parts

    entity_ref = entity_ref_raw.strip()
    key = unescape_field(key.strip())
    value = unescape_field(value.strip())

    if not entity_ref or not key:
        logger.warning(
            "property_line_missing_entity_or_key",
            line=line[:100],
        )
        return None

    try:
        entity_index = int(entity_ref)
    except ValueError:
        logger.warning(
            "property_line_invalid_entity_index",
            line=line[:100],
            entity_ref=entity_ref,
        )
        return None

    return {
        "entity_index": entity_index,
        "key": key,
        "value": value,
    }


def parse_rename_line(line: str) -> dict[str, Any] | None:
    """Parse a rename/alias line into a dictionary.

    Format: ``A|old_name|new_name|confidence|sent_ref|reasoning``

    The sent_ref must be a sentence reference like ``S3`` or ``S2-S5``.
    Lines without a valid sent_ref are rejected as malformed.

    Uses maxsplit to ensure reasoning (last field) can contain unescaped pipes.

    Args:
        line: Line starting with "A|"

    Returns:
        Rename dictionary with old_name, new_name, confidence, reasoning,
        and sent_ref; or None if parsing fails

    """
    if not line.startswith("A|"):
        return None

    parts = line[2:].split("|", 4)

    if len(parts) != 5:
        logger.warning(
            "rename_line_malformed",
            reason="part_count",
            line=line[:100],
            parts_found=len(parts),
            expected=5,
        )
        return None
    if not _looks_like_sent_ref(parts[3]):
        logger.warning(
            "rename_line_malformed",
            reason="bad_sent_ref",
            line=line[:100],
            sent_ref=parts[3][:40],
        )
        return None

    old_name = unescape_field(parts[0].strip())
    new_name = unescape_field(parts[1].strip())
    confidence = safe_float(parts[2])
    sent_ref = parts[3].strip()
    reasoning = unescape_field(parts[4].strip())

    if not old_name or not new_name:
        logger.warning("rename_line_missing_names", line=line[:100])
        return None

    return {
        "old_name": old_name,
        "new_name": new_name,
        "confidence": confidence,
        "reasoning": reasoning,
        "sent_ref": sent_ref,
    }


def _parse_mixed_lines(  # noqa: C901, PLR0912, PLR0915 - line dispatcher: each branch handles a distinct LLM-output row format
    content: str,
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    properties: list[dict[str, Any]],
    renames: list[dict[str, Any]] | None = None,
    *,
    max_out_of_bounds: int | None = None,
    max_source_type_repeat: int | None = None,
    skip_loop_detection: bool = False,
    minimum_alias_length: int = _DEFAULT_MIN_ALIAS_LENGTH,
    stats: dict[str, int] | None = None,
) -> None:
    """Parse lines that may contain entities, relationships, properties, or renames.

    Includes repetition detection to handle degenerate LLM output where the
    model gets stuck in a loop generating the same pattern repeatedly.

    Args:
        content: Multi-line string to parse
        entities: List to append parsed entities to
        relationships: List to append parsed relationships to
        properties: List to append parsed properties to
        renames: Optional list to append parsed renames to
        max_out_of_bounds: Override for out-of-bounds threshold (default from settings).
        max_source_type_repeat: Override for source-type repeat threshold (default from settings).
        skip_loop_detection: Skip OOB streak and source-type-repeat checks.
            Set True when the caller (streaming) already performed loop detection.
        minimum_alias_length: Minimum character length for an alias to be accepted.
        stats: Optional dict for counting per-line outcomes (parsed/skipped/dropped).

    """
    # Repetition / hallucination detection for degenerate LLM output.
    # Two signals:
    # 1. Same (source, type) repeating — target may increment
    # 2. Either source or target index exceeds entity count (hallucinated refs)
    max_same_source_type = (
        max_source_type_repeat
        if max_source_type_repeat is not None
        else LOOP_MAX_SOURCE_TYPE_REPEAT
    )
    _max_out_of_bounds = (
        max_out_of_bounds if max_out_of_bounds is not None else LOOP_MAX_OUT_OF_BOUNDS
    )
    last_source_type: tuple[str, str] | None = None
    source_type_streak = 0
    out_of_bounds_streak = 0

    # Track the index of the most recently parsed entity so P| lines
    # can be annotated with their proximity parent (the E| they follow).
    last_entity_index = len(entities) - 1  # -1 means "no entity yet"

    for raw_line in content.strip().splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue

        # Strip common markdown bullet prefixes that models sometimes add
        # Handles: "- E|...", "* E|...", "1. E|...", etc.
        normalized = _strip_bullet_prefix(stripped)

        # Strip trailing pipe — LLMs often treat | as a line terminator,
        # producing "P|Napoleon|age|Middle-aged|". Without this, the last
        # field in every parser captures the trailing pipe as part of the value.
        if normalized.endswith("|"):
            normalized = normalized[:-1]

        if normalized.startswith("E|"):
            entity = parse_entity_line(normalized, minimum_alias_length=minimum_alias_length)
            if entity:
                entities.append(entity)
                last_entity_index = len(entities) - 1
            elif stats is not None:
                stats["dropped_lines"] = stats.get("dropped_lines", 0) + 1
            # Reset tracking on entity lines
            last_source_type = None
            source_type_streak = 0
            out_of_bounds_streak = 0
        elif normalized.startswith("R|"):
            rel = parse_relationship_line(normalized)
            if rel:
                source_idx = rel.get("source")
                target_idx = rel.get("target")

                if not skip_loop_detection:
                    # Check 1: either index references a non-existent entity
                    entity_count = len(entities)
                    if entity_count > 0 and (
                        (isinstance(source_idx, int) and source_idx >= entity_count)
                        or (isinstance(target_idx, int) and target_idx >= entity_count)
                    ):
                        out_of_bounds_streak += 1
                        if stats is not None:
                            stats["dropped_lines"] = stats.get("dropped_lines", 0) + 1
                        if out_of_bounds_streak >= _max_out_of_bounds:
                            logger.warning(
                                "out_of_bounds_loop_detected_truncating",
                                source_index=source_idx,
                                target_index=target_idx,
                                entity_count=entity_count,
                                relationships_so_far=len(relationships),
                            )
                            break
                        continue  # Skip this invalid relationship but keep parsing
                    out_of_bounds_streak = 0

                    # Check 2: same (source, type) repeating — catches loops where
                    # source stays the same and target increments
                    source_type = (
                        str(source_idx),
                        rel.get("type", ""),
                    )
                    if source_type == last_source_type:
                        source_type_streak += 1
                        if source_type_streak >= max_same_source_type:
                            logger.warning(
                                "repetition_loop_detected_truncating",
                                pattern=f"{source_type[0]}|*|{source_type[1]}",
                                relationships_so_far=len(relationships),
                            )
                            break
                    else:
                        last_source_type = source_type
                        source_type_streak = 1

                relationships.append(rel)
            elif stats is not None:
                stats["dropped_lines"] = stats.get("dropped_lines", 0) + 1
        elif normalized.startswith("P|"):
            prop = parse_property_line(normalized)
            if prop:
                # Annotate with proximity: the index of the last E| line
                # seen before this P| line. Used by apply_properties_to_entities()
                # to prefer positional context over the LLM's entity_index.
                if last_entity_index >= 0:
                    prop["_proximity_entity_index"] = last_entity_index
                properties.append(prop)
            elif stats is not None:
                stats["dropped_lines"] = stats.get("dropped_lines", 0) + 1
        elif normalized.startswith("A|"):
            rename = parse_rename_line(normalized)
            if rename and renames is not None:
                renames.append(rename)
            elif stats is not None and rename is None:
                stats["dropped_lines"] = stats.get("dropped_lines", 0) + 1
        elif not _is_preamble_line(stripped):
            logger.debug("skipping_unknown_line_format", line=stripped[:80])


def parse_extraction_output(
    entities_str: str,
    relationships_str: str = "",
    properties_str: str = "",
    *,
    max_out_of_bounds: int | None = None,
    max_source_type_repeat: int | None = None,
    skip_loop_detection: bool = False,
    minimum_alias_length: int = _DEFAULT_MIN_ALIAS_LENGTH,
    stats: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse line-based extraction output into entity, relationship, and property lists.

    Handles various newline formats, skips blank lines and malformed lines
    (logs warnings for malformed lines).

    Properties in entities_str are parsed and returned in the properties list.
    Entities in relationships_str are parsed and returned in the entities list.

    Args:
        entities_str: String containing E| and optionally P| lines
        relationships_str: String containing R| lines (optional)
        properties_str: String containing P| lines (optional)
        max_out_of_bounds: Override for out-of-bounds loop threshold.
        max_source_type_repeat: Override for source-type repeat loop threshold.
        skip_loop_detection: Skip OOB and source-type-repeat checks in the parser.
            Set True when the caller (streaming) already performed loop detection.
        minimum_alias_length: Minimum character length for an alias to be accepted.
            Defaults to ``_DEFAULT_MIN_ALIAS_LENGTH`` (2). Mirrors the
            FilteringConfig field of the same name; production callers
            thread the resolved value from ``FilteringConfig`` through.
        stats: Optional mutable dict that the parser populates with
            ``dropped_lines`` (count of structured-prefix lines that
            failed to parse: malformed entity / relationship / property /
            rename, plus relationships skipped by loop detection). Lets
            the indexing pipeline surface the parser-drop count as a
            quality counter on the source row without changing the
            return shape.

    Returns:
        Tuple of (entities, relationships, properties)

    """
    entities: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    properties: list[dict[str, Any]] = []
    local_stats: dict[str, int] = {"dropped_lines": 0}

    # Parse all input strings - each may contain mixed line types
    if entities_str:
        _parse_mixed_lines(
            entities_str,
            entities,
            relationships,
            properties,
            max_out_of_bounds=max_out_of_bounds,
            max_source_type_repeat=max_source_type_repeat,
            skip_loop_detection=skip_loop_detection,
            minimum_alias_length=minimum_alias_length,
            stats=local_stats,
        )

    if relationships_str:
        _parse_mixed_lines(
            relationships_str,
            entities,
            relationships,
            properties,
            max_out_of_bounds=max_out_of_bounds,
            max_source_type_repeat=max_source_type_repeat,
            skip_loop_detection=skip_loop_detection,
            minimum_alias_length=minimum_alias_length,
            stats=local_stats,
        )

    if properties_str:
        _parse_mixed_lines(
            properties_str,
            entities,
            relationships,
            properties,
            max_out_of_bounds=max_out_of_bounds,
            max_source_type_repeat=max_source_type_repeat,
            skip_loop_detection=skip_loop_detection,
            minimum_alias_length=minimum_alias_length,
            stats=local_stats,
        )

    logger.info(
        "line_parsing_complete",
        entities_parsed=len(entities),
        relationships_parsed=len(relationships),
        properties_parsed=len(properties),
        dropped_lines=local_stats["dropped_lines"],
    )

    if stats is not None:
        stats["dropped_lines"] = stats.get("dropped_lines", 0) + local_stats["dropped_lines"]

    return entities, relationships, properties


def _is_preamble_line(line: str) -> bool:
    """Check if a line is likely LLM preamble/commentary to ignore silently.

    Args:
        line: Line to check

    Returns:
        True if line looks like preamble text

    """
    line_lower = line.lower()
    preamble_patterns = [
        "here are",
        "below are",
        "extracted entities",
        "extracted relationships",
        "i found",
        "the following",
        "entities:",
        "relationships:",
        "properties:",
        "note:",
        "---",
        "```",
    ]
    return any(pattern in line_lower for pattern in preamble_patterns)
