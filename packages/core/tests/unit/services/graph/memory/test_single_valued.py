# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The single-valued relations the memory path supersedes on (spec 2026-10-20 decision 6)."""

from __future__ import annotations

from chaoscypher_core.services.graph.memory.supersession import (
    MEMORY_SINGLE_VALUED,
    single_valued_labels,
)
from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
    EMPTY_RELATION_VOCABULARY,
    RelationVocabulary,
)


def test_the_default_set_is_the_memory_paths_own() -> None:
    assert single_valued_labels(EMPTY_RELATION_VOCABULARY) == MEMORY_SINGLE_VALUED
    assert {
        "lives_in",
        "located_in",
        "spouse_of",
        "employee_of",
        "leader_of",
    } <= MEMORY_SINGLE_VALUED
    assert "friend_of" not in MEMORY_SINGLE_VALUED and "knows" not in MEMORY_SINGLE_VALUED


def test_a_vocabulary_flag_adds_its_labels() -> None:
    vocabulary = RelationVocabulary.from_config(
        {"patronage": {"labels": ["patron_of"], "words": ["patron"], "single_valued": True}}
    )
    assert single_valued_labels(vocabulary) == MEMORY_SINGLE_VALUED | {"patron_of"}
