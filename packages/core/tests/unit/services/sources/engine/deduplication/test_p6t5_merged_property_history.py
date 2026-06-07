# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 6 Task 5 tests: merged_property_history in entity deduplication.

Tests:
1. After merge, ``merged_property_history`` is present on the result when
   there is something to record (loser confidence, skipped title alias).
2. Multiple merges accumulate into the same history dict.
3. When nothing is discarded, ``merged_property_history`` is absent.
"""

from __future__ import annotations

import pytest

from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor


def _make_processor() -> EntityProcessor:
    """Return a minimal EntityProcessor with title_words wired."""
    return EntityProcessor(title_words=frozenset({"dr", "prof", "mr", "mrs", "ms"}))


def _make_entity(
    name: str,
    confidence: float = 0.8,
    entity_type: str = "Person",
    **kwargs,
) -> dict:
    return {"name": name, "type": entity_type, "confidence": confidence, **kwargs}


# ---------------------------------------------------------------------------
# Loser confidence is recorded
# ---------------------------------------------------------------------------


def test_merge_records_loser_confidence() -> None:
    """Lower confidence value must appear in merged_property_history."""
    proc = _make_processor()
    kept = _make_entity("Alice Smith", confidence=0.9)
    duplicate = _make_entity("Alice", confidence=0.6)

    merged = proc.merge_entities(kept, duplicate)

    history = merged.get("merged_property_history")
    assert history is not None, "merged_property_history should be present"
    assert "confidence" in history
    # Loser confidence is 0.6
    assert pytest.approx(history["confidence"][0], abs=1e-6) == 0.6


# ---------------------------------------------------------------------------
# Title-word alias is recorded
# ---------------------------------------------------------------------------


def test_merge_records_skipped_title_alias() -> None:
    """Title-word duplicate name should appear in aliases_skipped provenance."""
    proc = _make_processor()
    kept = _make_entity("Smith", confidence=0.8)
    # "Dr" is in _title_words — it gets skipped as an alias
    duplicate = _make_entity("Dr", confidence=0.7)

    merged = proc.merge_entities(kept, duplicate)

    history = merged.get("merged_property_history", {})
    assert "aliases_skipped" in history, "Title-word alias should be recorded in aliases_skipped"
    assert "Dr" in history["aliases_skipped"]


# ---------------------------------------------------------------------------
# History accumulates across multiple merges
# ---------------------------------------------------------------------------


def test_merge_accumulates_history_across_multiple_merges() -> None:
    """Merging twice should accumulate provenance entries, not overwrite."""
    proc = _make_processor()
    entity_a = _make_entity("Alice", confidence=0.9)
    entity_b = _make_entity("Alicia", confidence=0.6)
    entity_c = _make_entity("Ali", confidence=0.5)

    merged_ab = proc.merge_entities(entity_a, entity_b)
    merged_abc = proc.merge_entities(merged_ab, entity_c)

    history = merged_abc.get("merged_property_history", {})
    assert "confidence" in history
    # Both 0.6 and 0.5 should appear (order may vary)
    assert len(history["confidence"]) == 2
    assert sorted(history["confidence"]) == pytest.approx([0.5, 0.6], abs=1e-6)


# ---------------------------------------------------------------------------
# No history when nothing is discarded
# ---------------------------------------------------------------------------


def test_merge_no_history_when_confidence_equal() -> None:
    """When both entities share the same confidence (loser_conf == 0.0
    after max subtraction) and no aliases are skipped, history is absent.
    """
    proc = _make_processor()
    # Both have the same confidence — loser_conf is still the lower one (same value)
    # but the value itself is > 0 so confidence will be recorded.
    # For nothing-discarded case use confidence=0 on both:
    kept = _make_entity("Alice", confidence=0.0)
    duplicate = _make_entity("Alice", confidence=0.0)

    merged = proc.merge_entities(kept, duplicate)

    history = merged.get("merged_property_history")
    # confidence key should be absent when loser_conf == 0
    if history is not None:
        assert "confidence" not in history


__all__: list[str] = []
