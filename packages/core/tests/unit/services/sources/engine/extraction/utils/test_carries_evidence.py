# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``carries_evidence``: the one reading of "has evidence" behind keep, flag, never delete (2026-10-07).

A record carrying a description or an evidence sentence is never dropped
by the pipeline; the commit's orphan rule (``flag_orphan_entities``) and the
name filters' reference rule (``kept_as_reference``) read this predicate.
"""

from __future__ import annotations

from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
    carries_evidence,
    kept_as_reference,
)


def test_a_description_is_evidence() -> None:
    assert carries_evidence({"name": "Márya Lvóvna Karágina", "description": "Julie's mother."})
    assert not carries_evidence({"name": "x", "description": "   "})
    assert not carries_evidence({"name": "x", "description": None})


def test_an_evidence_sentence_is_evidence_a_chunk_id_alone_is_not() -> None:
    assert carries_evidence({"name": "x", "evidence": [{"chunk_id": "c1", "text": "She came."}]})
    assert not carries_evidence({"name": "x", "evidence": [{"chunk_id": "c1"}]})
    assert not carries_evidence({"name": "x", "evidence": [{"chunk_id": "c1", "text": " "}]})
    assert not carries_evidence({"name": "x", "evidence": ["She came."]})  # not the stamped shape
    assert not carries_evidence({"name": "x", "evidence": "She came."})
    assert not carries_evidence({"name": "x"})


def test_the_reference_rule_reads_the_same_predicate() -> None:
    """A reference kept by the name filters: evidence of either kind, or properties of its own."""
    assert kept_as_reference({"name": "the count", "type": "Character", "description": "A host."})
    assert kept_as_reference(
        {"name": "the count", "type": "Character", "evidence": [{"chunk_id": "c", "text": "t"}]}
    )
    assert kept_as_reference({"name": "the count", "type": "Character", "properties": {"a": "b"}})
    assert not kept_as_reference({"name": "the count", "type": "Character"})
    assert not kept_as_reference({"name": "Count Rostóv", "description": "A host."})  # a name
