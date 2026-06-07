# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``create_domain_sample_text`` sampling shape.

Pinned after the 2026-05-23 38-fixture audit: the pipeline was sampling only
~4000 chars (first 3 + middle 2 chunks, with ~800-char chunks in practice),
causing detection scores 0.2-0.3 lower than dry-run predictions on the full
file. Bumped to first 5 + middle 3 chunks, max_sample_length 12000.

These tests pin the new shape so a future refactor doesn't silently drop
the chunk count again.
"""

from __future__ import annotations

from chaoscypher_core.services.sources.engine.extraction.domains import (
    create_domain_sample_text,
)


def test_empty_input_returns_empty_string() -> None:
    assert create_domain_sample_text([]) == ""


def test_takes_first_five_when_doc_is_small() -> None:
    """A doc with <8 chunks only contributes its first 5 chunks (no middle window)."""
    chunks = [f"chunk-{i}-content" for i in range(7)]
    sample = create_domain_sample_text(chunks)
    # Should include the first 5 chunks.
    for i in range(5):
        assert f"chunk-{i}-content" in sample
    # Should NOT include chunk-5 or chunk-6 — they're after position 5
    # but the middle window only activates when len >= 8.
    assert "chunk-5-content" not in sample
    assert "chunk-6-content" not in sample


def test_takes_first_five_plus_middle_three_when_doc_is_large() -> None:
    """A doc with >=8 chunks contributes first 5 + 3 middle chunks (~40% in)."""
    chunks = [f"chunk-{i}-content" for i in range(20)]
    sample = create_domain_sample_text(chunks)
    # First 5 always present.
    for i in range(5):
        assert f"chunk-{i}-content" in sample
    # Middle 3 from index 8 (20 * 2 // 5 = 8) — chunks 8, 9, 10.
    for i in (8, 9, 10):
        assert f"chunk-{i}-content" in sample


def test_middle_window_does_not_overlap_with_first_five() -> None:
    """Edge case: with exactly 8 chunks, mid_idx = 3 but is clamped to 5 (no overlap)."""
    chunks = [f"chunk-{i}-content" for i in range(8)]
    sample = create_domain_sample_text(chunks)
    # All 8 chunks should appear (first 5 + middle starting at 5).
    for i in range(8):
        assert f"chunk-{i}-content" in sample
    # And there must not be duplicates — chunk 4 should appear exactly once.
    assert sample.count("chunk-4-content") == 1


def test_per_item_limit_truncates_individual_chunks() -> None:
    """Each chunk contributes up to per_item_limit chars, not more."""
    long_chunk = "X" * 5000
    chunks = [long_chunk for _ in range(3)]
    sample = create_domain_sample_text(chunks, per_item_limit=1500)
    # Each chunk capped to 1500; 3 chunks + 2 newline joins.
    assert len(sample) <= 1500 * 3 + 2


def test_max_sample_length_caps_total() -> None:
    """Total sample is capped at max_sample_length even if individual chunks fit."""
    chunks = ["A" * 1500 for _ in range(20)]
    sample = create_domain_sample_text(chunks, max_sample_length=6000)
    assert len(sample) <= 6000


def test_dict_items_use_content_key() -> None:
    """When items are dicts, the named content_key is read out."""
    items = [{"combined_content": f"dict-chunk-{i}"} for i in range(10)]
    sample = create_domain_sample_text(items, content_key="combined_content")
    for i in range(5):
        assert f"dict-chunk-{i}" in sample


def test_default_caps_match_2026_05_23_audit_values() -> None:
    """Lock the audit-driven defaults so a future refactor doesn't shrink them back.

    The 2026-05-23 audit showed (3 + 2, 8000) under-sampled — pipeline scores
    were 0.2-0.3 lower than full-text dry-run, pushing correct detections
    below the registry's 1.0 absolute floor. Bumped to (5 + 3, 12000).
    """
    import inspect

    sig = inspect.signature(create_domain_sample_text)
    assert sig.parameters["max_sample_length"].default == 12000
    assert sig.parameters["per_item_limit"].default == 1500
