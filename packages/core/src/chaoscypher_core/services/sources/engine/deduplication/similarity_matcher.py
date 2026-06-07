# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Similarity matching and comparison logic for entity deduplication.

Provides functions for comparing entity names and computing adjusted
similarity scores. Handles alias matching bonuses, hierarchical name
containment checks, and significant word extraction with title-word
filtering.

SRP: Single responsibility for entity similarity comparison.
"""

import unicodedata
from typing import Any

import structlog


logger = structlog.get_logger(__name__)

# Types treated as generic/untyped — always compatible with any other type.
_GENERIC_TYPES = frozenset(
    {
        "unknown",
        "thing",
        "entity",
        "item",
        "object",
        "concept",
        "",
    }
)


def normalize_compatibility_map(
    compatibility_map: dict[str, list[str]] | None,
) -> dict[str, frozenset[str]] | None:
    """Pre-normalize a compatibility map for repeated lookups.

    Converts each group's type list to a lowercase frozenset so that
    ``are_types_compatible`` can skip per-call normalization when called
    in tight loops.

    Args:
        compatibility_map: Raw compatibility groups, e.g.
            ``{"Person": ["Character", "Individual"]}``.

    Returns:
        Normalized map with frozenset values, or None if input is None/empty.

    """
    if not compatibility_map:
        return None
    return {gid: frozenset(t.lower() for t in group) for gid, group in compatibility_map.items()}


def are_types_compatible(
    type_a: str,
    type_b: str,
    compatibility_map: dict[str, list[str]] | None = None,
    *,
    _normalized_map: dict[str, frozenset[str]] | None = None,
) -> bool:
    """Check whether two entity types are compatible for merging.

    Two types are compatible if:
    1. They are identical (case-insensitive), OR
    2. Either type is a generic placeholder, OR
    3. Both appear in the same group in the compatibility map.

    Args:
        type_a: First entity type.
        type_b: Second entity type.
        compatibility_map: Optional custom groups, e.g.
            ``{"Person": ["Character", "Individual"]}``.
        _normalized_map: Pre-normalized map from
            ``normalize_compatibility_map()``. When provided, takes
            precedence over ``compatibility_map`` and avoids per-call
            normalization overhead.

    Returns:
        True if the types are compatible for merging.

    """
    a = type_a.strip().lower()
    b = type_b.strip().lower()

    if a == b:
        return True
    if a in _GENERIC_TYPES or b in _GENERIC_TYPES:
        return True

    resolved = _normalized_map or (
        normalize_compatibility_map(compatibility_map) if compatibility_map else None
    )
    if resolved:
        for group_lower in resolved.values():
            if a in group_lower and b in group_lower:
                return True
    return False


def normalize_name_key(name: str) -> str:
    """Normalize a name for comparison by stripping diacritics and lowercasing.

    Uses Unicode NFD decomposition to separate base characters from combining
    marks (accents/diacritics), then removes the combining marks.

    Examples:
        "Anna Pavlovna Scherer" -> "anna pavlovna scherer"
        "Francois" -> "francois"
        "naive" -> "naive"

    Args:
        name: Entity name to normalize.

    Returns:
        Lowercase name with diacritics stripped and whitespace trimmed.

    """
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", name) if unicodedata.category(c) != "Mn"
    )
    return stripped.strip().lower()


def calculate_entity_similarity(
    entity_a: dict[str, Any],
    entity_b: dict[str, Any],
    embedding_similarity: float,
    *,
    precomputed_a: tuple[str, frozenset[str]] | None = None,
    precomputed_b: tuple[str, frozenset[str]] | None = None,
) -> float:
    """Calculate total entity similarity with name and alias matching bonus.

    Adds bonus points when:
    - Both entities have the same normalized name (strongest signal)
    - One entity's name appears in the other's aliases
    - Both entities share common aliases

    Args:
        entity_a: First entity dictionary.
        entity_b: Second entity dictionary.
        embedding_similarity: Base cosine similarity from embeddings.
        precomputed_a: Optional pre-normalized (name_key, alias_keys) for
            entity_a to avoid redundant normalize_name_key() calls in tight
            loops. When None, computed on the fly.
        precomputed_b: Optional pre-normalized (name_key, alias_keys) for
            entity_b.

    Returns:
        Adjusted similarity score (capped at 1.0).

    """
    bonus = 0.0

    # Use precomputed values or normalize on the fly
    if precomputed_a is not None:
        name_a, aliases_a = precomputed_a
    else:
        name_a = normalize_name_key(entity_a.get("name") or entity_a.get("label", ""))
        aliases_a = frozenset(normalize_name_key(a) for a in entity_a.get("aliases", []) if a)

    if precomputed_b is not None:
        name_b, aliases_b = precomputed_b
    else:
        name_b = normalize_name_key(entity_b.get("name") or entity_b.get("label", ""))
        aliases_b = frozenset(normalize_name_key(a) for a in entity_b.get("aliases", []) if a)

    # Exact name match: strongest indicator (same entity, different types/descriptions)
    if name_a and name_b and name_a == name_b:
        bonus += 0.20

    # Name matches alias: strong indicator of same entity
    if name_a and name_a in aliases_b:
        bonus += 0.15
    if name_b and name_b in aliases_a:
        bonus += 0.15

    # Aliases overlap: also a strong indicator
    if aliases_a and aliases_b and (aliases_a & aliases_b):
        bonus += 0.10

    if bonus > 0:
        logger.debug(
            "name_alias_bonus_applied",
            entity_a_name=name_a,
            entity_b_name=name_b,
            base_similarity=round(embedding_similarity, 3),
            bonus=round(bonus, 3),
            total=round(min(1.0, embedding_similarity + bonus), 3),
        )

    return min(1.0, embedding_similarity + bonus)


def extract_significant_words(name: str, title_words: frozenset[str]) -> set[str]:
    """Extract significant words from a name, ignoring titles and honorifics.

    Args:
        name: Entity name.
        title_words: Set of title/honorific words to exclude.

    Returns:
        Set of significant lowercase words.

    """
    # Strip diacritics, tokenize and lowercase - replace common separators with spaces
    words = set()
    diacritics_stripped = normalize_name_key(name)
    normalized = (
        diacritics_stripped.replace(".", " ").replace(",", " ").replace("/", " ").replace("-", " ")
    )
    for word in normalized.split():
        stripped = word.strip()
        if stripped and stripped not in title_words and len(stripped) > 1:
            words.add(stripped)
    return words


def _is_word_boundary_match(short: str, long: str) -> bool:
    """Check if short name appears at a word boundary in long name.

    Returns True if short appears as a complete word (not a substring of a
    longer word) in the long name.

    Args:
        short: Shorter normalized name to find.
        long: Longer normalized name to search in.

    Returns:
        True if short appears at a word boundary in long.

    """
    pos = long.find(short)
    if pos == -1:
        return False
    end = pos + len(short)
    # Check left boundary: start of string or preceded by space/separator
    left_ok = pos == 0 or not long[pos - 1].isalnum()
    # Check right boundary: end of string or followed by space/separator
    right_ok = end == len(long) or not long[end].isalnum()
    return left_ok and right_ok


def should_merge_names(  # noqa: PLR0911
    idx_a: int,
    name_a: str,
    words_a: set[str],
    idx_b: int,
    name_b: str,
    words_b: set[str],
) -> tuple[bool, int]:
    """Determine if two entity names should be merged.

    Merge criteria with guards against false positives:
    1. Strict containment: one name fully contains the other as a substring,
       BUT the match must occur at a word boundary to prevent false positives
       like "mary" matching inside "marya" or "prince" inside "princess".
    2. Word-set containment: one name's significant words are a complete
       subset of the other's, AND the shorter set must cover at least 40%
       of the longer set's words.

    Args:
        idx_a: Index of first entity.
        name_a: Name of first entity.
        words_a: Significant words from first entity.
        idx_b: Index of second entity.
        name_b: Name of second entity.
        words_b: Significant words from second entity.

    Returns:
        Tuple of (should_merge, canonical_idx):
            - should_merge: True if entities should be merged.
            - canonical_idx: Index of the entity to keep as canonical
              (longer/more complete).

    """
    name_a_lower = normalize_name_key(name_a)
    name_b_lower = normalize_name_key(name_b)

    # Strict containment check (one name is substring of the other)
    a_in_b = name_a_lower in name_b_lower and name_a_lower != name_b_lower
    b_in_a = name_b_lower in name_a_lower and name_a_lower != name_b_lower

    if a_in_b:
        if not _is_word_boundary_match(name_a_lower, name_b_lower):
            return False, -1
        return True, idx_b  # B is canonical (longer)

    if b_in_a:
        if not _is_word_boundary_match(name_b_lower, name_a_lower):
            return False, -1
        return True, idx_a  # A is canonical (longer)

    # Word-set containment check
    if not words_a or not words_b:
        return False, -1

    if words_a.issubset(words_b) and len(words_a) / len(words_b) >= 0.4:
        return True, idx_b  # B has more words (more complete)
    if words_b.issubset(words_a) and len(words_b) / len(words_a) >= 0.4:
        return True, idx_a  # A has more words (more complete)

    return False, -1
