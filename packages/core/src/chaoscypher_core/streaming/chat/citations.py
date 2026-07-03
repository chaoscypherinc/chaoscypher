# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Citation and Entity Reference Processing.

Handles extraction, enrichment, and normalization of chunk citations
and entity references in streaming chat responses. Includes blockquote
citation injection, citation mismatch correction, and duplicate text
stripping for citation-by-reference rendering.
"""

import json
import re
from typing import Any, TypedDict

import structlog

from chaoscypher_core.app_config import get_settings


logger = structlog.get_logger(__name__)


# ================================
# Entity References
# ================================

# Entity reference pattern: [[node:ID|Label]] or [[edge:ID|Label]]
# Accepts common separators: colon, underscore, hyphen, dot, slash, space
# ID format can be: pure UUID, or prefixed like node_UUID or edge_UUID
ENTITY_REFERENCE_PATTERN = re.compile(
    r"\[\[(node|edge)[_:\-./\s]([a-zA-Z_]*[a-f0-9-]+)\|([^\]]+)\]\]",
    re.IGNORECASE,
)


class EntityRefData(TypedDict, total=False):
    """Data structure for extracted entity reference."""

    id: str
    type: str  # "node" or "edge"
    label: str
    template_id: str | None
    template_name: str | None
    entity_type: str | None
    description: str | None
    properties: dict[str, Any] | None
    incoming_count: int | None
    outgoing_count: int | None


def extract_entity_references(content: str) -> dict[str, EntityRefData]:
    """Extract entity references from message content.

    Parses [[node:id|label]] and [[edge:id|label]] patterns and returns
    a dictionary mapping entity IDs to their reference data.

    Args:
        content: Message content containing entity references

    Returns:
        Dictionary mapping entity ID to EntityRefData

    """
    references: dict[str, EntityRefData] = {}

    for match in ENTITY_REFERENCE_PATTERN.finditer(content):
        entity_type = match.group(1).lower()  # "node" or "edge"
        entity_id = match.group(2)
        label = match.group(3)

        # Store with just the basic info (will be enriched from tool results)
        references[entity_id] = EntityRefData(
            id=entity_id,
            type=entity_type,
            label=label,
        )

    logger.info(
        "extract_entity_references_called",
        content_length=len(content),
        content_preview=content[:200] if content else "",
        references_found=len(references),
        reference_ids=list(references.keys())[:5],
    )

    return references


# Fields copied directly from tool result data to entity info (same key)
_ENTITY_DIRECT_FIELDS = (
    "title",
    "name",
    "label",
    "description",
    "incoming_count",
    "outgoing_count",
    "source_node_id",
    "target_node_id",
)

# Keys that may contain nested entity data for recursive collection.
# Verified against the actual tool handlers (node/edge/analytics/graphrag):
# every container that carries node-like dicts must be listed, or chips for
# entities mentioned only there get no hover details (2026-06-10 audit).
_ENTITY_RECURSE_KEYS = frozenset(
    (
        "communities",  # analyze_graph_structure
        "context",
        "edges",
        "graph_context",  # graphrag_search
        "incoming",
        "incoming_edges",  # get_node_context
        "isolated_nodes",  # analyze_graph_structure
        "node",
        "nodes",
        "outgoing",
        "outgoing_edges",  # get_node_context
        "path",  # find_shortest_path / traverse_path
        "related_entities",  # graphrag_search
        "related_node",  # get_node_edges
        "relationships",
        "results",
        "sample_members",  # analyze_graph_structure communities
        "seed_entities",  # graphrag_search
        "similar_nodes",  # find_similar_nodes
        "source",  # get_node_context edge rows (nested node dict)
        "source_node",
        "start_node",  # traverse_path
        "target",  # get_node_context edge rows (nested node dict)
        "target_node",
        "top_nodes",  # analyze_graph_structure
    )
)


def _collect_entities_from_data(
    data: Any,
    entity_data: dict[str, dict[str, Any]],
) -> None:
    """Recursively collect entity data from tool result.

    Handles various formats:
    - Single node/edge objects with "id" field
    - Lists of nodes/edges
    - Nested structures like search results

    Args:
        data: Tool result data (dict, list, or primitive)
        entity_data: Dictionary to populate with entity data

    """
    if isinstance(data, dict):
        # Check if this is an entity object (has "id" field)
        if "id" in data:
            entity_id = data["id"]
            entity_info: dict[str, Any] = {
                "template_id": data.get("template_id"),
                "template_name": data.get("template_name"),
                "properties": data.get("properties"),
            }

            # Copy direct fields (same key in source and target)
            for field_name in _ENTITY_DIRECT_FIELDS:
                if field_name in data:
                    entity_info[field_name] = data[field_name]

            # "type" is renamed to "entity_type" to avoid collision
            if "type" in data:
                entity_info["entity_type"] = data["type"]

            # Derive counts from nested relationship/context dicts
            for container_key in ("relationships", "context"):
                container = data.get(container_key)
                if isinstance(container, dict):
                    for direction in ("incoming", "outgoing"):
                        if direction in container:
                            entity_info[f"{direction}_count"] = len(container[direction])

            entity_data[entity_id] = entity_info

        # Recursively process nested structures
        for key, value in data.items():
            if key in _ENTITY_RECURSE_KEYS:
                _collect_entities_from_data(value, entity_data)

    elif isinstance(data, list):
        for item in data:
            _collect_entities_from_data(item, entity_data)


def enrich_entity_references_from_tool_results(
    references: dict[str, EntityRefData],
    tool_results: list[dict[str, Any]],
) -> dict[str, EntityRefData]:
    """Enrich entity references with data from tool results.

    Searches through tool results to find matching entity data
    and adds template info, properties, and relationship counts.

    Args:
        references: Dictionary of entity references to enrich
        tool_results: List of tool result dictionaries from message history

    Returns:
        Enriched references dictionary

    """
    if not references or not tool_results:
        return references

    # Build lookup of entity data from tool results
    entity_data: dict[str, dict[str, Any]] = {}

    for result in tool_results:
        content = result.get("content")
        if not content:
            continue

        # Parse JSON content
        try:
            data = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError, TypeError:
            continue

        # Extract entities from various tool result formats
        _collect_entities_from_data(data, entity_data)

    # Enrich references with collected data
    # Handle ID format mismatch: LLM may generate plain UUIDs while tool results have prefixed IDs
    for entity_id, ref in references.items():
        entity_info = None
        # Try direct match first
        if entity_id in entity_data:
            entity_info = entity_data[entity_id]
        else:
            # Try with prefix based on entity type
            prefix = "node_" if ref.get("type") == "node" else "edge_"
            prefixed_id = f"{prefix}{entity_id}" if not entity_id.startswith(prefix) else entity_id
            if prefixed_id in entity_data:
                entity_info = entity_data[prefixed_id]

        if entity_info:
            ref["template_id"] = entity_info.get("template_id")
            ref["template_name"] = entity_info.get("template_name")
            ref["properties"] = entity_info.get("properties")
            ref["incoming_count"] = entity_info.get("incoming_count")
            ref["outgoing_count"] = entity_info.get("outgoing_count")
            # Also include description and entity_type for tooltip display
            ref["description"] = entity_info.get("description")
            ref["entity_type"] = entity_info.get("entity_type")

    return references


# ================================
# Chunk Citations
# ================================

# Pattern: [[cite:CHUNK_ID:Sn|label]] or [[cite:CHUNK_ID#Sn]] (label optional)
# Accepts : or # as separator between chunk ID and sentence refs
# ID group must match both hex UUIDs (a-f0-9-) and chunk aliases (C0, C1, etc.)
CHUNK_CITATION_PATTERN = re.compile(
    r"\[\[cite:([A-Za-z0-9-]+)[:#](S\d+(?:[,;]\s*S\d+)*)(?:\|([^\]]+))?\]\]",
    re.IGNORECASE,
)


class ChunkCitationData(TypedDict, total=False):
    """Data structure for extracted chunk citation."""

    chunk_id: str
    sentence_refs: str  # e.g. "S3" or "S1,S2"
    label: str  # Display label (usually filename)
    sentence_text: str | None
    source_id: str | None
    page_number: int | None
    has_vision_image: bool  # True if chunk contains vision-described image content


# Pattern: [CHUNK <alias-or-uuid>] or [CHUNK <alias-or-uuid> | <filename>]
_RAW_CHUNK_REF_PATTERN = re.compile(
    r"\[CHUNK\s+(C\d+|[a-f0-9-]+)(?:\s*\|\s*([^\]]+))?\]",
    re.IGNORECASE,
)

# Pattern: (Chunk C0, Sentence S1) or (Chunk C0, Sentences S1, S2)
_PAREN_CHUNK_REF_PATTERN = re.compile(
    r"\(Chunk\s+(C\d+|[a-f0-9-]+),?\s*Sentences?\s+(S\d+(?:[,;\s]+S\d+)*)\)",
    re.IGNORECASE,
)

# Pattern: bare (C0) or (C5, C2) — chunk aliases in parens without "Chunk" keyword
_BARE_ALIAS_PAREN_PATTERN = re.compile(
    r"\((C\d+(?:\s*,\s*C\d+)*)\)",
)

# Pattern: [[cite:<alias>:Sn|label]] where alias is C0, C1, etc.
_ALIAS_CITE_PATTERN = re.compile(
    r"\[\[cite:(C\d+)([:#]S\d+(?:[,;]\s*S\d+)*)(?:\|([^\]]+))?\]\]",
    re.IGNORECASE,
)

# Loose pattern: ANY [[cite:...]] marker regardless of internal validity.
# Used by the salvage and scrub passes to find markers the strict pattern
# rejects (e.g. mixed refs like [[cite:C1:S15,C17|f]]).
_LOOSE_CITE_PATTERN = re.compile(
    r"\[\[cite:([^\]|]*)(?:\|([^\]]+))?\]\]",
    re.IGNORECASE,
)

# Token classifiers for salvaging malformed citation ref lists (fullmatch use)
_SENTENCE_TOKEN_RE = re.compile(r"S\d+", re.IGNORECASE)
_ALIAS_TOKEN_RE = re.compile(r"C\d+", re.IGNORECASE)
_UUIDISH_TOKEN_RE = re.compile(r"[a-f0-9-]{8,}", re.IGNORECASE)


def _tidy_removal_whitespace(text: str) -> str:
    """Collapse mid-line whitespace runs left where markers were removed.

    Lookbehind/lookahead guards keep leading indentation intact — markdown
    code blocks and nested lists depend on it.
    """
    text = re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", text)
    return re.sub(r"(?<=\S)[ \t]+([.,;:!?])", r"\1", text)


def _salvage_mixed_ref_citations(
    content: str,
    alias_map: dict[str, tuple[str, str]],
) -> str:
    """Split malformed multi-chunk citation markers into valid per-chunk markers.

    LLMs sometimes cram several chunk refs into one marker's sentence list
    (``[[cite:C1:S15,C17|f]]`` — C17 is a chunk alias, not a sentence) or
    invent junk tokens (``O5``). The strict patterns reject these outright,
    so without salvage the raw marker text reaches the UI. This pass
    re-groups the token list into one marker per chunk ref, drops junk
    tokens, and (when an alias map is available) drops chunk refs that
    don't correspond to any chunk the LLM actually saw.

    Args:
        content: LLM response text.
        alias_map: Mapping of chunk alias to (chunk_id, filename); may be
            empty, in which case alias existence is not validated.

    Returns:
        Content with malformed markers rewritten to strict syntax (aliases
        still unresolved — step 3 of ``normalize_chunk_references`` handles
        that) or removed when nothing salvageable remains.

    """

    def _fix(match: re.Match[str]) -> str:
        original = match.group(0)
        if CHUNK_CITATION_PATTERN.fullmatch(original):
            return original  # already valid — leave for alias resolution

        label = (match.group(2) or "source").strip()
        # Group tokens into (chunk_ref, [sentence_refs]) runs: a chunk-like
        # token starts a new group, sentence tokens attach to the current one.
        groups: list[tuple[str, list[str]]] = []
        for token in re.split(r"[:#,;\s]+", match.group(1)):
            if not token:
                continue
            if _SENTENCE_TOKEN_RE.fullmatch(token):
                if groups:
                    groups[-1][1].append(token.upper())
            elif _ALIAS_TOKEN_RE.fullmatch(token) or _UUIDISH_TOKEN_RE.fullmatch(token):
                groups.append((token, []))
            # anything else (O5, ...) is junk — drop it

        parts: list[str] = []
        for chunk_ref, sentences in groups:
            is_alias = _ALIAS_TOKEN_RE.fullmatch(chunk_ref)
            if alias_map and is_alias and chunk_ref.upper() not in alias_map:
                continue  # hallucinated alias — no such chunk in tool results
            refs = ",".join(sentences) if sentences else "S1"
            parts.append(f"[[cite:{chunk_ref}:{refs}|{label}]]")

        logger.info(
            "citation_salvaged",
            original=original[:120],
            salvaged_markers=len(parts),
        )
        return "".join(parts)

    salvaged = _LOOSE_CITE_PATTERN.sub(_fix, content)
    if salvaged == content:
        return content
    return _tidy_removal_whitespace(salvaged)


def strip_malformed_citations(content: str) -> str:
    """Remove citation markers that would render as raw text or dead chips.

    Final scrub before reference extraction: drops (a) markers the strict
    pattern still can't parse after salvage and (b) well-formed markers
    whose chunk ref is an unresolved ``C0``-style alias — those can't be
    enriched or clicked, so showing them only adds noise.

    Args:
        content: LLM response after ``normalize_chunk_references``.

    Returns:
        Content with unusable citation markers removed and removal
        whitespace artifacts tidied.

    """

    def _check(match: re.Match[str]) -> str:
        full = match.group(0)
        strict = CHUNK_CITATION_PATTERN.fullmatch(full)
        if strict and not _ALIAS_TOKEN_RE.fullmatch(strict.group(1)):
            return full
        logger.info("citation_stripped_malformed", marker=full[:120])
        return ""

    stripped = _LOOSE_CITE_PATTERN.sub(_check, content)
    if stripped == content:
        return content
    return _tidy_removal_whitespace(stripped)


def _build_chunk_alias_map(
    tool_results: list[dict[str, Any]],
) -> dict[str, tuple[str, str]]:
    """Build a mapping from chunk aliases (C0, C1, ...) to (chunk_id, filename).

    Args:
        tool_results: List of tool result message dicts.

    Returns:
        Mapping of alias (e.g. "C0") to (chunk_uuid, filename).

    """
    alias_map: dict[str, tuple[str, str]] = {}
    for result in tool_results:
        content = result.get("content")
        if not content:
            continue

        try:
            data = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError, TypeError:
            continue

        if not isinstance(data, dict):
            continue

        for key in ("chunks", "related_chunks"):
            chunk_list = data.get(key)
            if not isinstance(chunk_list, list):
                continue
            for chunk in chunk_list:
                if isinstance(chunk, dict) and "chunk_alias" in chunk:
                    alias = chunk["chunk_alias"]
                    alias_map[alias.upper()] = (
                        chunk["chunk_id"],
                        chunk.get("filename", "source"),
                    )

    return alias_map


def normalize_chunk_references(
    content: str,
    tool_results: list[dict[str, Any]] | None = None,
) -> str:
    """Convert raw chunk references and short aliases to citation syntax.

    Handles three cases:
    1. Raw ``[CHUNK C0 | filename]`` written verbatim by the LLM
    2. ``(Chunk C0, Sentence S1)`` parenthesized form
    3. ``[[cite:C0:S1|filename]]`` using short alias instead of UUID

    All are resolved to ``[[cite:<real-uuid>:S1|filename]]`` when
    tool results are available for alias mapping.

    Args:
        content: LLM response text.
        tool_results: Tool result messages for alias resolution.

    Returns:
        Content with aliases resolved to UUIDs and raw refs normalized.

    """
    alias_map: dict[str, tuple[str, str]] = {}
    if tool_results:
        alias_map = _build_chunk_alias_map(tool_results)

    # 1. Convert raw [CHUNK C0 | filename] to [[cite:C0:S1|filename]]
    def _replace_raw(match: re.Match[str]) -> str:
        ref = match.group(1)
        label = (match.group(2) or "source").strip()
        return f"[[cite:{ref}:S1|{label}]]"

    content = _RAW_CHUNK_REF_PATTERN.sub(_replace_raw, content)

    # 2. Convert (Chunk C0, Sentence S1) to [[cite:C0:S1|source]]
    def _replace_paren(match: re.Match[str]) -> str:
        ref = match.group(1)
        raw_refs = match.group(2)  # e.g. "S1" or "S1, S2"
        sentence_refs = ",".join(s.strip() for s in re.findall(r"S\d+", raw_refs))
        return f"[[cite:{ref}:{sentence_refs}|source]]"

    content = _PAREN_CHUNK_REF_PATTERN.sub(_replace_paren, content)

    # 2b. Convert bare (C0) or (C5, C2) to citation markers
    # Only when alias_map exists so we can verify these are real chunk aliases
    if alias_map:

        def _replace_bare_alias(match: re.Match[str]) -> str:
            aliases_str = match.group(1)
            aliases = [a.strip() for a in aliases_str.split(",")]
            # Only convert if ALL aliases are known chunk refs
            if not all(a.upper() in alias_map for a in aliases):
                return match.group(0)
            parts = []
            for alias in aliases:
                real_id, filename = alias_map[alias.upper()]
                parts.append(f"[[cite:{real_id}:S1|{filename or 'source'}]]")
            return "".join(parts)

        content = _BARE_ALIAS_PAREN_PATTERN.sub(_replace_bare_alias, content)

    # 2c. Salvage malformed multi-chunk markers (e.g. [[cite:C1:S15,C17|f]])
    # into one valid marker per chunk so step 3 can resolve the aliases.
    content = _salvage_mixed_ref_citations(content, alias_map)

    # 3. Resolve aliases (C0, C1, ...) to real UUIDs in [[cite:...]] patterns
    if alias_map:

        def _resolve_alias(match: re.Match[str]) -> str:
            alias = match.group(1).upper()
            sentence_part = match.group(2)  # e.g. ":S1"
            label = match.group(3)
            real_id, filename = alias_map.get(alias, (match.group(1), ""))
            resolved_label = label or filename or "source"
            return f"[[cite:{real_id}{sentence_part}|{resolved_label}]]"

        content = _ALIAS_CITE_PATTERN.sub(_resolve_alias, content)

    return content


def _collect_chunk_data_from_tool_results(
    tool_results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build a lookup of chunk data from tool result messages.

    Args:
        tool_results: List of tool result message dicts.

    Returns:
        Mapping of chunk_id to chunk data dict.

    """
    chunk_data_map: dict[str, dict[str, Any]] = {}
    for result in tool_results:
        content = result.get("content")
        if not content:
            continue

        try:
            data = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError, TypeError:
            continue

        if not isinstance(data, dict):
            continue

        for key in ("chunks", "related_chunks"):
            chunk_list = data.get(key)
            if not isinstance(chunk_list, list):
                continue
            for chunk in chunk_list:
                if isinstance(chunk, dict) and "chunk_id" in chunk:
                    chunk_data_map[chunk["chunk_id"]] = chunk

    return chunk_data_map


def _find_chunk_for_quote(
    quote_text: str,
    chunk_data_map: dict[str, dict[str, Any]],
    min_match_length: int = 40,
) -> tuple[str, str, str] | None:
    """Find which chunk contains the quoted text.

    When multiple chunks match (due to overlap), prefers the chunk with
    the lowest ``chunk_index`` -- the canonical position in the document.

    Args:
        quote_text: Cleaned blockquote text.
        chunk_data_map: Mapping of chunk_id to chunk data.
        min_match_length: Minimum quote length to attempt matching.

    Returns:
        Tuple of (chunk_id, sentence_ref, filename) or None.

    """
    if len(quote_text) < min_match_length:
        return None

    best_match: tuple[str, str, str, int] | None = None

    for chunk_id, chunk_info in chunk_data_map.items():
        original = chunk_info.get("original_content", "")
        if not original:
            continue

        # Check if a substantial substring of the quote appears in the chunk.
        # Use the configured search-key window (enough to be unique).
        search_key = quote_text[: get_settings().chat.citation_search_key_chars].strip()
        if search_key not in original:
            continue

        # Found a match -- figure out which sentence
        sentence_ref = "S1"
        chunk_meta = chunk_info.get("chunk_metadata")
        if isinstance(chunk_meta, dict):
            offsets = chunk_meta.get("sentence_offsets")
            if isinstance(offsets, list):
                pos = original.find(search_key)
                for idx, off in enumerate(offsets):
                    if off.get("start", 0) <= pos < off.get("end", 0):
                        sentence_ref = f"S{idx + 1}"
                        break

        filename = chunk_info.get("filename", "source")
        # Prefer the earliest chunk in the document (lowest chunk_index)
        # to point to the canonical location rather than an overlap duplicate
        chunk_index = chunk_info.get("chunk_index") or 0

        if best_match is None or chunk_index < best_match[3]:
            best_match = (chunk_id, sentence_ref, filename, chunk_index)

    if best_match:
        return best_match[0], best_match[1], best_match[2]
    return None


def correct_mismatched_citations(  # noqa: C901
    content: str,
    tool_results: list[dict[str, Any]],
) -> str:
    """Correct citations that point to the wrong chunk.

    When the LLM attaches a citation to a blockquote but cites the wrong
    chunk (e.g. C4 instead of C0), this function detects the mismatch by
    checking whether the blockquote text actually appears in the cited
    chunk.  If not, it searches all available chunks for the correct match
    and rewrites the citation in-place.

    Must run AFTER ``normalize_chunk_references`` (aliases -> UUIDs) and
    BEFORE ``inject_citations_into_blockquotes`` (which only handles
    uncited blockquotes).

    Args:
        content: LLM response with UUID-resolved citations.
        tool_results: Tool result messages containing chunk data.

    Returns:
        Content with mismatched citations corrected.

    """
    if not tool_results:
        return content

    chunk_data_map = _collect_chunk_data_from_tool_results(tool_results)
    if not chunk_data_map:
        return content

    lines = content.split("\n")
    result_lines: list[str] = []
    bq_run: list[int] = []  # indices of consecutive blockquote lines

    def _correct_bq_run() -> None:
        """Check citations in a blockquote run and fix wrong chunk refs."""
        if not bq_run:
            return

        run_text = "\n".join(lines[i] for i in bq_run)

        # Find all citations in this blockquote run
        cite_matches = list(CHUNK_CITATION_PATTERN.finditer(run_text))
        if not cite_matches:
            # No citations to correct -- pass through unchanged
            result_lines.extend(lines[i] for i in bq_run)
            return

        # Extract the plain blockquote text (strip > prefix, quotes, citations)
        plain_parts = []
        for i in bq_run:
            stripped = re.sub(r"^>\s*", "", lines[i]).strip()
            # Remove citation markers for clean text
            stripped = re.sub(r"\[\[cite:[^\]]+\]\]", "", stripped).strip()
            stripped = stripped.strip('"').strip("\u201c\u201d")
            if stripped:
                plain_parts.append(stripped)
        plain_text = " ".join(plain_parts)

        if len(plain_text) < get_settings().chat.citation_min_quote_chars:
            # Too short to reliably match
            result_lines.extend(lines[i] for i in bq_run)
            return

        # For each citation, check if the quoted text is in the cited chunk
        # We only correct the LAST citation in the run (typically one per blockquote)
        last_cite = cite_matches[-1]
        cited_chunk_id = last_cite.group(1)

        # Check if quoted text is in the cited chunk
        cited_chunk = chunk_data_map.get(cited_chunk_id)
        text_matches_cited = False
        if cited_chunk:
            search_key = plain_text[: get_settings().chat.citation_search_key_chars].strip()
            original = cited_chunk.get("original_content", "")
            if original and search_key in original:
                text_matches_cited = True

        if text_matches_cited:
            # Citation is correct (or in the right group) -- pass through
            result_lines.extend(lines[i] for i in bq_run)
            return

        # Citation is WRONG -- find the correct chunk
        correct_match = _find_chunk_for_quote(plain_text, chunk_data_map)
        if not correct_match:
            # Can't find correct chunk -- leave as-is
            result_lines.extend(lines[i] for i in bq_run)
            return

        correct_id, correct_sref, correct_filename = correct_match

        if correct_id == cited_chunk_id:
            # Same chunk, just different sentence ref -- leave for enrichment to fix
            result_lines.extend(lines[i] for i in bq_run)
            return

        logger.info(
            "citation_chunk_corrected",
            wrong_chunk=cited_chunk_id,
            correct_chunk=correct_id,
            quote_preview=plain_text[:80],
        )

        # Build the corrected citation
        old_cite_str = last_cite.group(0)
        new_cite_str = f"[[cite:{correct_id}:{correct_sref}|{correct_filename}]]"

        # Replace in the run text, then split back into lines
        corrected_run = run_text.replace(old_cite_str, new_cite_str, 1)
        corrected_lines = corrected_run.split("\n")
        result_lines.extend(corrected_lines)

    for idx, line in enumerate(lines):
        if line.startswith(">"):
            bq_run.append(idx)
        else:
            _correct_bq_run()
            bq_run = []
            result_lines.append(line)

    # Handle trailing blockquote run
    _correct_bq_run()

    return "\n".join(result_lines)


def inject_citations_into_blockquotes(
    content: str,
    tool_results: list[dict[str, Any]],
) -> str:
    """Add citations to blockquotes that don't already have them.

    When the LLM quotes chunk text in a blockquote but omits the citation
    marker, this finds the source chunk by text matching and appends the
    citation inline.

    Args:
        content: LLM response (after normalize_chunk_references).
        tool_results: Tool result messages containing chunk data.

    Returns:
        Content with citations injected into uncited blockquotes.

    """
    if not tool_results:
        return content

    chunk_data_map = _collect_chunk_data_from_tool_results(tool_results)
    if not chunk_data_map:
        return content

    # Split into lines and process blockquote runs
    lines = content.split("\n")
    result_lines: list[str] = []
    bq_run: list[int] = []  # indices of consecutive blockquote lines

    def _process_bq_run(next_line_has_cite: bool = False) -> None:
        """Inject citation at end of a blockquote run if missing."""
        if not bq_run:
            return

        # Check if any line in the run (or the line immediately after) has a citation
        run_text = "\n".join(lines[i] for i in bq_run)
        if "[[cite:" in run_text or next_line_has_cite:
            result_lines.extend(lines[i] for i in bq_run)
            return

        # Extract plain text from blockquote (strip > prefix and quotes)
        plain_parts = []
        for i in bq_run:
            line = lines[i]
            stripped = re.sub(r"^>\s*", "", line).strip().strip('"').strip("\u201c\u201d")
            if stripped:
                plain_parts.append(stripped)
        plain_text = " ".join(plain_parts)

        # Try to find which chunk this quote came from
        match = _find_chunk_for_quote(plain_text, chunk_data_map)
        if match:
            chunk_id, sentence_ref, filename = match
            citation = f" [[cite:{chunk_id}:{sentence_ref}|{filename}]]"
            # Append citation to the last blockquote line
            last_idx = bq_run[-1]
            result_lines.extend(lines[i] for i in bq_run[:-1])
            result_lines.append(lines[last_idx] + citation)
        else:
            result_lines.extend(lines[i] for i in bq_run)

    for idx, line in enumerate(lines):
        if line.startswith(">"):
            bq_run.append(idx)
        else:
            _process_bq_run(next_line_has_cite="[[cite:" in line)
            bq_run = []
            result_lines.append(line)

    # Handle trailing blockquote run
    _process_bq_run()

    return "\n".join(result_lines)


# Quoted-span pattern: ASCII double quotes, curly double quotes, or
# guillemets, capturing the inner text. We accept quite short spans
# (>= 12 chars) because in a single-source / small-corpus chat the
# distinctiveness threshold is much lower than in open-web search —
# a phrase like ``"Annette Schérer"`` is only ~16 chars but is uniquely
# attributable to one chunk.
_QUOTED_SPAN_RE = re.compile(
    r"""(?:"|“|«)(?P<inner>[^"”»]{12,})(?:"|”|»)""",
    re.UNICODE,
)


def inject_citations_for_uncited_paragraphs(
    content: str,
    tool_results: list[dict[str, Any]],
) -> str:
    """Append a citation marker to paragraphs that quote chunk text without one.

    Fallback for the common LLM failure mode where the model paraphrases or
    inline-quotes a chunk in plain prose (not a markdown blockquote) and
    forgets the ``[[cite:...]]`` marker. ``inject_citations_into_blockquotes``
    only covers ``> "..."`` runs; this covers paragraphs.

    Strategy:
    1. Split on blank lines into paragraphs.
    2. Skip paragraphs that already contain ``[[cite:`` (already cited) or
       start with ``>`` (handled by the blockquote injector).
    3. For each remaining paragraph, find inline quoted spans of >= 20
       chars and attempt :func:`_find_chunk_for_quote` against them.
    4. Pick the first matching chunk and append
       ``[[cite:<chunk_id>:<sentence_ref>|<filename>]]`` to the end of the
       paragraph (before any trailing punctuation).

    Args:
        content: LLM response (after the blockquote injector has run).
        tool_results: Tool result messages containing chunk data.

    Returns:
        Content with citation markers appended where matches were found.

    """
    if not tool_results:
        return content

    chunk_data_map = _collect_chunk_data_from_tool_results(tool_results)
    if not chunk_data_map:
        return content

    # Split on blank-line boundaries so list items / multi-line paragraphs
    # stay together. We re-join with the same separator that we split on.
    paragraphs = re.split(r"(\n\s*\n)", content)

    out_parts: list[str] = []
    for part in paragraphs:
        # Separators (whitespace/newlines) preserved verbatim.
        if not part.strip() or part.startswith("\n"):
            out_parts.append(part)
            continue
        if "[[cite:" in part:
            out_parts.append(part)
            continue
        # Don't touch markdown blockquote runs — the dedicated injector
        # handles those, and double-injecting would push the citation
        # outside the blockquote and visibly duplicate it.
        if all(line.lstrip().startswith(">") or not line.strip() for line in part.splitlines()):
            out_parts.append(part)
            continue

        match = _first_matching_chunk_for_paragraph(part, chunk_data_map)
        if match is None:
            out_parts.append(part)
            continue

        chunk_id, sentence_ref, filename = match
        marker = f" [[cite:{chunk_id}:{sentence_ref}|{filename}]]"
        out_parts.append(_append_marker_before_trailing_punct(part, marker))

    return "".join(out_parts)


def _first_matching_chunk_for_paragraph(
    paragraph: str,
    chunk_data_map: dict[str, dict[str, Any]],
) -> tuple[str, str, str] | None:
    """Return the first (chunk_id, sentence_ref, filename) matching a quoted span.

    Tries each quoted span in document order. For each span we attempt
    several normalisations against the chunk text — LLMs add trailing
    punctuation, smart quotes, or capitalisation that the chunk's
    ``original_content`` doesn't have, so a strict substring match
    against the raw quote misses too often.
    """
    for m in _QUOTED_SPAN_RE.finditer(paragraph):
        inner = m.group("inner").strip()
        if not inner:
            continue
        min_match_chars = get_settings().chat.citation_min_match_chars
        for candidate in _quote_match_candidates(inner):
            if len(candidate) < min_match_chars:
                continue
            result = _find_chunk_for_quote(
                candidate, chunk_data_map, min_match_length=min_match_chars
            )
            if result is not None:
                return result
    return None


# Trailing/leading runs of whitespace + sentence punctuation that LLMs
# often append to a quote when they integrate it into prose.
_QUOTE_TRIM_RE = re.compile(r"^[\s.,;:!?]+|[\s.,;:!?]+$")


def _quote_match_candidates(quote: str) -> list[str]:
    """Generate progressively looser variants of a quote for substring matching.

    Order: as-is, punctuation-trimmed, longest internal letter/digit-only
    run. Returning a list (rather than yielding) keeps the call sites
    simple and avoids re-running the regex per call.
    """
    candidates: list[str] = [quote]
    trimmed = _QUOTE_TRIM_RE.sub("", quote)
    if trimmed and trimmed != quote:
        candidates.append(trimmed)
    # As a last-resort fallback: take the longest contiguous run of
    # alphanumeric + spaces. Useful when the LLM rewrote punctuation
    # (e.g. swapped curly for straight quotes inside the span).
    runs = re.findall(r"[\w' ]+", quote, flags=re.UNICODE)
    if runs:
        longest = max(runs, key=len).strip()
        if longest and longest not in candidates:
            candidates.append(longest)
    return candidates


def _append_marker_before_trailing_punct(paragraph: str, marker: str) -> str:
    r"""Insert ``marker`` before any trailing whitespace / sentence punctuation.

    "...her standing.\n" + " [[cite:...]]" -> "...her standing. [[cite:...]]\n"
    so the citation stays attached to the sentence rather than ending up on
    a line of its own.
    """
    stripped = paragraph.rstrip()
    trailing = paragraph[len(stripped) :]
    if stripped and stripped[-1] in ".;,!?":
        body, last = stripped[:-1], stripped[-1]
        return f"{body}{marker}{last}{trailing}"
    return f"{stripped}{marker}{trailing}"


# Quoted spans used only for citation RELOCATION matching. The floor is lower
# (6 chars) than _QUOTED_SPAN_RE because relocation matches a paragraph quote
# against the SPECIFIC chunk a citation already points at — confirming "does
# this chunk contain this paragraph's quote" rather than searching the whole
# corpus — so short distinctive quotes (e.g. "Antichrist") are safe here.
_RELOCATE_QUOTE_RE = re.compile(r"""(?:"|“|«)([^"”»\n]{6,}?)(?:"|”|»)""")


def _relocate_quoted_spans(text: str) -> list[str]:
    """Quoted phrases inside a paragraph, used to find a citation's home."""
    return [m.group(1).strip() for m in _RELOCATE_QUOTE_RE.finditer(text)]


def _is_marker_only_para(text: str) -> bool:
    """True when a paragraph is nothing but citation markers (whitespace aside)."""
    return bool(text.strip()) and not _LOOSE_CITE_PATTERN.sub("", text).strip()


def _relocate_home_index(
    chunk_id: str,
    paras: list[str],
    chunk_data_map: dict[str, dict[str, Any]],
) -> int | None:
    """Earliest prose paragraph quoting a phrase that appears in the cited chunk.

    Returns the paragraph index, or ``None`` when no paragraph quotes a
    phrase found in the chunk's text. The model often keeps the sentence's
    own punctuation inside the quotes (e.g. ``"...run over."``) while the
    chunk stores it without, so match on punctuation-trimmed candidates.
    """
    original = (chunk_data_map.get(chunk_id) or {}).get("original_content") or ""
    if not original:
        return None
    lowered = original.lower()
    for idx, para in enumerate(paras):
        if _is_marker_only_para(para):
            continue
        for span in _relocate_quoted_spans(para):
            for cand in _quote_match_candidates(span):
                if len(cand) >= 6 and cand.lower() in lowered:
                    return idx
    return None


def relocate_grouped_citations(
    content: str,
    tool_results: list[dict[str, Any]],
) -> str:
    """Move citations the model dumped at the end to the paragraph they support.

    LLMs sometimes group every ``[[cite:...]]`` marker at the end of the answer
    (one trailing the last paragraph, the rest orphaned on their own lines)
    instead of placing each after the claim it supports. That leaves the quoted
    paragraphs above looking unsourced and renders lone floating chips.

    For each citation marker this finds its *home* paragraph — the earliest
    prose paragraph that quotes a phrase appearing in the cited chunk's text —
    and moves the marker to the end of that paragraph. A marker already in its
    home paragraph is left untouched; a marker with no identifiable home stays
    where it is. Paragraphs left holding only markers are dissolved.

    Runs AFTER ``normalize_chunk_references`` (markers carry real UUIDs) and
    BEFORE the blockquote / paragraph injectors.

    Args:
        content: LLM response with UUID-resolved citations.
        tool_results: Tool result messages containing chunk data.

    Returns:
        Content with grouped / orphaned citations relocated to their home
        paragraphs.

    """
    if not tool_results:
        return content
    chunk_data_map = _collect_chunk_data_from_tool_results(tool_results)
    if not chunk_data_map:
        return content

    paras = re.split(r"\n\s*\n", content)
    new_paras = list(paras)
    appends: dict[int, list[str]] = {}
    changed = False

    for cur_idx, para in enumerate(paras):
        for match in CHUNK_CITATION_PATTERN.finditer(para):
            marker = match.group(0)
            home = _relocate_home_index(match.group(1), paras, chunk_data_map)
            if home is None or home == cur_idx:
                continue
            new_paras[cur_idx] = new_paras[cur_idx].replace(marker, "", 1)
            appends.setdefault(home, []).append(marker)
            changed = True

    if not changed:
        return content

    for idx, markers in appends.items():
        body = new_paras[idx].rstrip()
        new_paras[idx] = _append_marker_before_trailing_punct(body, " " + " ".join(markers))

    result = "\n\n".join(para for para in new_paras if para.strip())
    return _tidy_removal_whitespace(result)


def _strip_blockquotes_before_citations(content: str) -> str:
    """Remove blockquote text preceding or containing citation markers.

    With citation-by-reference, the frontend renders source text from
    enriched sentence_text. If the LLM still writes a blockquote before
    or around a citation, strip the blockquote and keep only the citation
    marker so text is not displayed twice.

    Args:
        content: LLM response content.

    Returns:
        Content with pre-citation blockquotes removed.

    """
    lines = content.split("\n")
    result: list[str] = []
    bq_buffer: list[int] = []

    for idx, line in enumerate(lines):
        if line.startswith(">"):
            bq_buffer.append(idx)
        else:
            if bq_buffer:
                # Check if any line in the blockquote run contains a citation
                bq_has_cite = any("[[cite:" in lines[i] for i in bq_buffer)

                if bq_has_cite:
                    # Extract just the citation markers from the blockquote
                    for i in bq_buffer:
                        result.extend(
                            cite_match.group(0)
                            for cite_match in CHUNK_CITATION_PATTERN.finditer(lines[i])
                        )
                else:
                    # Regular blockquote without citation -- keep it
                    result.extend(lines[i] for i in bq_buffer)

                bq_buffer = []

            result.append(line)

    # Flush any trailing blockquote buffer
    if bq_buffer:
        bq_has_cite = any("[[cite:" in lines[i] for i in bq_buffer)
        if bq_has_cite:
            for i in bq_buffer:
                result.extend(
                    cite_match.group(0) for cite_match in CHUNK_CITATION_PATTERN.finditer(lines[i])
                )
        else:
            result.extend(lines[i] for i in bq_buffer)

    return "\n".join(result)


def _strip_inline_quotes_before_citations(content: str) -> str:
    """Remove long inline quoted text that precedes citation markers.

    With citation-by-reference the frontend renders source text as a visible
    blockquote.  When the LLM *also* writes that same text in double-quotes
    the user sees it twice.  This strips quoted runs of 30+ characters that
    appear immediately before a ``[[cite:...]]`` marker.

    Args:
        content: LLM response content (after blockquote stripping).

    Returns:
        Content with pre-citation inline quotes removed.

    """
    # Strip long quoted text (straight double-quotes) before citation markers
    content = re.sub(
        r'"[^"\n]{30,}"[.!?,;:]*\s*(?=\[\[cite:)',
        "",
        content,
    )

    # Same for smart / curly double-quotes
    content = re.sub(
        r"\u201c[^\u201d\n]{30,}\u201d[.!?,;:]*\s*(?=\[\[cite:)",
        "",
        content,
    )

    # Clean up dangling intro phrases left after quote removal,
    # e.g. ", with the line [[cite:" -> " [[cite:"
    return re.sub(
        r",?\s*(?:with (?:the )?(?:line|passage|text|quote)|"
        r"(?:documented|described|recorded) (?:as|in the text as)|"
        r"the (?:text|document|passage|source) (?:reads|states|says))"
        r"\s*:?\s*(?=\[\[cite:)",
        " ",
        content,
        flags=re.IGNORECASE,
    )


def _normalize_for_dedup(text: str) -> str:
    """Normalize text for duplicate-detection comparison.

    Collapses whitespace, strips quote characters, and lowercases.

    Args:
        text: Raw text to normalize.

    Returns:
        Lowercased, whitespace-collapsed text without quote marks.

    """
    text = re.sub(r'["\u201c\u201d\u2018\u2019\']+', "", text)
    text = re.sub(r"\s+", " ", text)
    return text.lower().strip()


def strip_duplicated_citation_text(
    content: str,
    citations: dict[str, ChunkCitationData],
) -> str:
    """Remove prose that duplicates the sentence text a citation will display.

    After enrichment each citation may carry ``sentence_text``.  If the LLM
    wrote that same text (quoted or unquoted) near the citation marker the
    user would see it twice.  This function detects the overlap and strips
    the LLM's copy.

    Processes citations right-to-left so earlier removals don't shift the
    positions of later markers.

    Args:
        content: Cleaned LLM response (citations already normalized).
        citations: Enriched citation map keyed by ``chunk_id:sentence_refs``.

    Returns:
        Content with duplicate prose removed.

    """
    if not citations:
        return content

    for cite_match in reversed(list(CHUNK_CITATION_PATTERN.finditer(content))):
        chunk_id = cite_match.group(1)
        sentence_refs = cite_match.group(2)
        key = f"{chunk_id}:{sentence_refs}"

        cite_data = citations.get(key)
        if not cite_data:
            continue
        sentence_text = cite_data.get("sentence_text")
        if not sentence_text or len(sentence_text) < 30:
            continue

        # Look at the window of text before this citation marker
        window_size = len(sentence_text) + 200
        window_start = max(0, cite_match.start() - window_size)
        before_text = content[window_start : cite_match.start()]

        norm_sentence = _normalize_for_dedup(sentence_text)
        norm_before = _normalize_for_dedup(before_text)

        if norm_sentence not in norm_before:
            continue

        # Build a flexible regex from the sentence text
        escaped = re.escape(sentence_text.strip())
        # Allow flexible whitespace between words
        flexible = re.sub(r"\\ +", r"\\s+", escaped)
        # Optional surrounding quotes
        qc = r'["\u201c\u201d]?'
        pattern = rf"{qc}{flexible}{qc}[.!?,;:]*\s*"

        try:
            match = re.search(pattern, before_text, re.IGNORECASE)
        except re.error:
            continue

        if not match:
            continue

        abs_start = window_start + match.start()
        abs_end = window_start + match.end()

        # Also strip a leading setup phrase like ": " or ", with the line "
        prefix_window = content[max(0, abs_start - 80) : abs_start]
        setup = re.search(
            r"(?:[,:;]\s*(?:(?:with )?the (?:line|passage|text|quote)|"
            r"(?:documented|described|recorded) (?:as|in the text as)|"
            r"the (?:text|document|passage|source) (?:reads|states|says)|"
            r"(?:as follows|reads as follows|is described as))"
            r"\s*:?\s*)$",
            prefix_window,
            re.IGNORECASE,
        )
        if setup:
            abs_start -= len(setup.group(0))

        content = content[:abs_start] + " " + content[abs_end:]

    # Clean up double spaces and excessive blank lines
    content = re.sub(r"  +", " ", content)
    return re.sub(r"\n\s*\n\s*\n", "\n\n", content)


def extract_chunk_citations(content: str) -> dict[str, ChunkCitationData]:
    """Extract chunk citations from message content.

    Parses ``[[cite:CHUNK_ID:Sn|label]]`` patterns and returns
    a dictionary mapping chunk IDs to their citation data.

    Args:
        content: Message content containing chunk citations.

    Returns:
        Dictionary mapping chunk ID to ChunkCitationData.

    """
    citations: dict[str, ChunkCitationData] = {}

    for match in CHUNK_CITATION_PATTERN.finditer(content):
        chunk_id = match.group(1)
        sentence_refs = match.group(2)
        label = match.group(3) or ""  # Label is optional

        # Key by chunk_id:sentence_refs so the same chunk cited with
        # different sentences (e.g. S1 vs S3) gets separate entries.
        citation_key = f"{chunk_id}:{sentence_refs}"
        citations[citation_key] = ChunkCitationData(
            chunk_id=chunk_id,
            sentence_refs=sentence_refs,
            label=label,
        )

    if citations:
        logger.info(
            "extract_chunk_citations_called",
            content_length=len(content),
            citations_found=len(citations),
            chunk_ids=list(citations.keys())[:5],
        )

    return citations


def _resolve_sentence_text(
    original_content: str,
    offsets: list[dict[str, int]],
    sentence_refs: str,
) -> str | None:
    """Resolve sentence text from content, offsets, and sentence references.

    Args:
        original_content: Full chunk text.
        offsets: List of {start, end} dicts for each sentence.
        sentence_refs: Comma-separated sentence refs like "S1,S3".

    Returns:
        Joined sentence text, or None if nothing resolved.

    """
    indices = [int(s) - 1 for s in re.findall(r"S(\d+)", sentence_refs)]
    sentences = [
        original_content[offsets[idx]["start"] : offsets[idx]["end"]]
        for idx in indices
        if 0 <= idx < len(offsets)
    ]
    return " ".join(sentences) if sentences else None


def enrich_chunk_citations_from_tool_results(
    citations: dict[str, ChunkCitationData],
    tool_results: list[dict[str, Any]],
) -> dict[str, ChunkCitationData]:
    """Enrich chunk citations with data from tool results.

    Searches through tool results to find matching chunk data and resolves
    sentence text from sentence offsets.

    Args:
        citations: Dictionary of chunk citations to enrich.
        tool_results: List of tool result message dicts from message history.

    Returns:
        Enriched citations dictionary.

    """
    if not citations or not tool_results:
        return citations

    chunk_data_map = _collect_chunk_data_from_tool_results(tool_results)

    for citation in citations.values():
        chunk_info = chunk_data_map.get(citation["chunk_id"])
        if not chunk_info:
            continue

        citation["source_id"] = chunk_info.get("source_id")
        citation["page_number"] = chunk_info.get("page_number")

        # Check if chunk content came from vision processing
        chunk_content = chunk_info.get("content") or chunk_info.get("original_content") or ""
        if "[Visual Content]" in chunk_content and citation.get("source_id"):
            citation["has_vision_image"] = True

        # Fill label from filename when LLM omitted it
        if not citation.get("label"):
            citation["label"] = chunk_info.get("filename", "source")

        # Resolve sentence text from original_content + sentence_offsets
        original_content = chunk_info.get("original_content")
        chunk_meta = chunk_info.get("chunk_metadata")
        sentence_refs = citation.get("sentence_refs", "")

        if not (original_content and isinstance(chunk_meta, dict) and sentence_refs):
            continue

        offsets = chunk_meta.get("sentence_offsets")
        if not isinstance(offsets, list):
            continue

        text = _resolve_sentence_text(original_content, offsets, sentence_refs)
        if text:
            citation["sentence_text"] = text

    return citations


__all__ = [
    "CHUNK_CITATION_PATTERN",
    "ENTITY_REFERENCE_PATTERN",
    "ChunkCitationData",
    "EntityRefData",
    "_strip_blockquotes_before_citations",
    "_strip_inline_quotes_before_citations",
    "correct_mismatched_citations",
    "enrich_chunk_citations_from_tool_results",
    "enrich_entity_references_from_tool_results",
    "extract_chunk_citations",
    "extract_entity_references",
    "inject_citations_for_uncited_paragraphs",
    "inject_citations_into_blockquotes",
    "normalize_chunk_references",
    "relocate_grouped_citations",
    "strip_duplicated_citation_text",
    "strip_malformed_citations",
]
