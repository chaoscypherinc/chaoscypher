# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Similarity matching and comparison logic for entity deduplication.

Provides functions for comparing entity names and computing adjusted
similarity scores for the semantic (embedding) pass: name normalisation,
alias matching bonuses and type compatibility (core types, :func:`core_type_key`). Whether two names are one
entity by name alone is decided by
:mod:`~chaoscypher_core.services.sources.engine.deduplication.name_identity`.

SRP: Single responsibility for entity similarity comparison.
"""

import unicodedata
from functools import lru_cache
from typing import Any

import structlog


logger = structlog.get_logger(__name__)


@lru_cache(maxsize=4096)
def core_type_key(entity_type: str | None) -> str:
    """The type ``entity_type`` is compared as wherever two types are compared: its core type.

    The one reading of a written type by the dedup type gate
    (:func:`are_types_compatible`), the semantic partition, the hierarchical
    grouping, the name rule's person test (``NameConventions.is_person``) and
    the typed ``NameIndex`` (spec 2026-10-07 § 6). A core type is itself in
    any case (``person`` is ``Person``); an old template name is its mapping
    table row's core type (``core_types.core_type_of``, the plain row: an old
    snapshot's ``Character`` is ``Person``); a written ``Core/subtype`` is its
    written core type (what the evidence filter reads ahead of the type
    rescue, which places the subtype); a name the table does not know
    compares as itself, folded (``UNKNOWN`` is ``unknown``), and no type is
    the empty string. Until 2026-10-07 a type was compared as its own string,
    with a domain's ``compatibility_group`` map and a list of generic types
    (``Concept``, ``Object``, ``unknown``: compatible with every type) beside it.
    """
    # Lazy: the extraction package imports this one at load time.
    from chaoscypher_core.services.sources.engine.extraction.domains.core_types import (
        core_type_of,
        split_qualified_type,
    )

    name = " ".join((entity_type or "").strip().split())
    if not name:
        return ""
    qualified = split_qualified_type(name)
    if qualified is not None:  # Person/character, before the type rescue places it
        return qualified[0]
    try:
        return core_type_of(name)[0]
    except KeyError:
        return name.lower()


def are_types_compatible(type_a: str, type_b: str) -> bool:
    """Whether two entity types may be one entity: their core types are one (:func:`core_type_key`).

    ``Person`` and ``person`` are compatible, and so are two subtypes'
    old template names of one core type (``Character`` and ``Author``, both
    Person); ``Person`` and ``Location`` never are, nor ``Concept`` and
    ``Person`` (a core type is no generic placeholder). A merge keeps the
    core type and unions the subtypes (``EntityProcessor.merge_entities``).

    Args:
        type_a: First entity type.
        type_b: Second entity type.

    Returns:
        True if the types are compatible for merging.

    """
    return core_type_key(type_a) == core_type_key(type_b)


def normalize_name_key(name: str) -> str:
    """Normalize a name for comparison by stripping diacritics and lowercasing.

    Uses Unicode NFD decomposition to separate base characters from combining
    marks (accents/diacritics), then removes the combining marks.

    Examples:
        "Anna Pavlovna Scherer" -> "anna pavlovna scherer"
        "Francois" -> "francois"
        "naive" -> "naive"
        "Duc d\u2019Enghien" -> "duc d'enghien" (a typographic apostrophe is one)

    Args:
        name: Entity name to normalize.

    Returns:
        Lowercase name with diacritics stripped and whitespace trimmed.

    """
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", name) if unicodedata.category(c) != "Mn"
    )
    # One apostrophe: U+2019 is written as ' ("Duc d'Enghien" either way; audit wave 5).
    return stripped.replace("\u2019", "'").strip().lower()


def calculate_entity_similarity(
    entity_a: dict[str, Any],
    entity_b: dict[str, Any],
    embedding_similarity: float,
    *,
    precomputed_a: tuple[str, frozenset[str]] | None = None,
    precomputed_b: tuple[str, frozenset[str]] | None = None,
    alias_evidence: tuple[bool, bool] | None = None,
) -> float:
    """Calculate total entity similarity with name and alias matching bonus.

    Adds bonus points when:
    - Both entities have the same normalized name (strongest signal)
    - One entity's name is an alias the other was given, and that alias is
      the name rule's alias evidence (``NameIndex.alias_evidence``)

    Two entities sharing an alias get nothing for it: an alias two names
    carry says which family, not whom (``Kurágin`` on Anatole and on
    Vasíli), the rule the exact dedup keys by. Until 2026-10-06 that overlap
    added 0.10 and a name in the other's aliases 0.15 with no guard, so a
    father and son or a count and countess merged on a shared surname at a
    cosine of 0.86 (audit W4-3).

    Args:
        entity_a: First entity dictionary.
        entity_b: Second entity dictionary.
        embedding_similarity: Base cosine similarity from embeddings.
        precomputed_a: Optional pre-normalized (name_key, alias_keys) for
            entity_a to avoid redundant normalize_name_key() calls in tight
            loops. When None, computed on the fly from its given aliases.
        precomputed_b: Optional pre-normalized (name_key, alias_keys) for
            entity_b.
        alias_evidence: ``(a's name in b's aliases is evidence, b's name in
            a's aliases is evidence)`` as the caller's name index judged it
            over every entity; when None, judged over the two entities alone
            under the universal naming conventions.

    Returns:
        Adjusted similarity score (capped at 1.0).

    """
    bonus = 0.0

    name_a, aliases_a = precomputed_a or _name_keys(entity_a)
    name_b, aliases_b = precomputed_b or _name_keys(entity_b)

    # Exact name match: strongest indicator (same entity, different types/descriptions)
    if name_a and name_b and name_a == name_b:
        bonus += 0.20

    a_in_b = bool(name_a) and name_a in aliases_b
    b_in_a = bool(name_b) and name_b in aliases_a
    if (a_in_b or b_in_a) and alias_evidence is None:
        alias_evidence = _pair_alias_evidence(entity_a, entity_b)
    # Name matches an alias the other was given: evidence only when the name rule says so.
    if a_in_b and alias_evidence is not None and alias_evidence[0]:
        bonus += 0.15
    if b_in_a and alias_evidence is not None and alias_evidence[1]:
        bonus += 0.15

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


def _name_keys(entity: dict[str, Any]) -> tuple[str, frozenset[str]]:
    """An entity's normalized name and given aliases (``aliases_given`` when a merge recorded it)."""
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        GIVEN_ALIASES_KEY,
    )

    raw = entity.get(GIVEN_ALIASES_KEY)
    aliases = raw if isinstance(raw, list) else entity.get("aliases") or []
    return (
        normalize_name_key(entity.get("name") or entity.get("label", "")),
        frozenset(normalize_name_key(str(a)) for a in aliases if a),
    )


def _pair_alias_evidence(entity_a: dict[str, Any], entity_b: dict[str, Any]) -> tuple[bool, bool]:
    """``(b gave a's name, a gave b's name)`` as alias evidence over the two entities alone."""
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        NameIndex,
        name_record,
    )

    index = NameIndex([name_record(entity_a), name_record(entity_b)], typed=False)
    return index.alias_evidence_between(1, 0), index.alias_evidence_between(0, 1)
