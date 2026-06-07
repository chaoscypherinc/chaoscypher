# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""min_chunk_size, max_chunk_size, respect_boundaries actually do something.

Workstream 5.3 (2026-05-07): three chunking settings on
``ChunkingSettings`` were assigned to ``self.*`` in ``chunk.py`` but never
read. This file enforces post-split min/max enforcement and the
sentence-aware separator toggle.

2026-05-08 (W5 follow-up): ``min_chunk_size`` no longer **drops** short
chunks — it **coalesces** them into a neighbor so natural prose
(dialogue, transitions, short paragraphs) keeps reaching extraction.
``ChunksResult.chunks_filtered`` now records merge events, not drops.

2026-05-09 (Phase 7 audit P1 #5): ``quick_mode_max_groups`` lifted from
hardcoded ``5`` in ``chunk.py:878,950`` to ``ChunkingSettings`` so
operators with larger documents can override.
"""

from __future__ import annotations

import pytest

from chaoscypher_core.settings import ChunkingSettings, EngineSettings
from chaoscypher_core.utils.chunk import ChunkingService


def _build_service(**chunking_overrides) -> ChunkingService:
    """Construct a ChunkingService with overridden chunking settings."""
    base = ChunkingSettings()
    overrides = {**base.model_dump(), **chunking_overrides}
    chunking = ChunkingSettings(**overrides)
    return ChunkingService(settings=EngineSettings(chunking=chunking))


@pytest.mark.asyncio
async def test_min_chunk_size_coalesces_short_chunks() -> None:
    """A sub-threshold chunk merges with its neighbor; no content lost.

    The pre-2026-05-08 behaviour was to **drop** sub-threshold chunks,
    losing real prose on natural-language imports (war_and_peace.txt
    regression). The new contract is to coalesce them into a neighbor.
    """
    # 1500 chars of "x" — splitter produces a 900-char chunk and a ~600
    # char trailing remainder. With min_chunk_size=800 the trailing
    # remainder is sub-threshold and must coalesce, not be dropped.
    service = _build_service(
        small_chunk_size=900,
        small_chunk_overlap=0,
        min_chunk_size=800,
        normalize_newlines=False,
        normalize_remove_structural_noise=False,
    )
    text = "x" * 1500
    result = await service.create_chunks(full_text=text, source_id="src", store=False)

    # Total content preserved — pre-fix the trailing 600 chars vanished.
    full_content = "".join(c["content"] for c in result.small_chunks)
    assert len(full_content) >= 1500, (
        f"coalesce must preserve all content; got {len(full_content)} of 1500 chars"
    )

    # At least one merge event recorded.
    assert result.chunks_filtered >= 1, (
        f"chunks_filtered records merge events; expected >=1, got {result.chunks_filtered}"
    )


@pytest.mark.asyncio
async def test_short_tail_chunk_emitted_not_lost() -> None:
    """If the document ends with a short fragment, it appears in output anyway."""
    service = _build_service(
        small_chunk_size=900,
        small_chunk_overlap=0,
        min_chunk_size=500,
        normalize_newlines=False,
        normalize_remove_structural_noise=False,
    )
    long = "Lorem ipsum dolor sit amet. " * 50  # ~1400 chars
    short_tail = "The end."
    text = f"{long}\n\n{short_tail}"

    result = await service.create_chunks(full_text=text, source_id="src", store=False)
    full_content = "\n".join(c["content"] for c in result.small_chunks)
    assert "The end." in full_content, (
        "short tail chunk was lost - coalesce must emit pending content"
    )


@pytest.mark.asyncio
async def test_two_consecutive_short_chunks_merge_with_next_long() -> None:
    """Two short chunks in a row both get folded into the next long chunk."""
    service = _build_service(
        small_chunk_size=900,
        small_chunk_overlap=0,
        min_chunk_size=500,
        normalize_newlines=False,
        normalize_remove_structural_noise=False,
    )
    short_a = "Brief line A."
    short_b = "Brief line B."
    long = "Lorem ipsum dolor sit amet. " * 30  # ~840 chars
    text = f"{short_a}\n\n{short_b}\n\n{long}"

    result = await service.create_chunks(full_text=text, source_id="src", store=False)
    full_content = "\n".join(c["content"] for c in result.small_chunks)
    assert "Brief line A" in full_content
    assert "Brief line B" in full_content


@pytest.mark.asyncio
async def test_min_chunk_size_zero_disables_coalesce() -> None:
    """min_chunk_size=0 means no coalescing - chunks emitted as-is."""
    service = _build_service(
        small_chunk_size=900,
        small_chunk_overlap=0,
        min_chunk_size=0,
        normalize_newlines=False,
        normalize_remove_structural_noise=False,
    )
    short = "Tiny."
    long = "Lorem ipsum dolor sit amet. " * 30
    text = f"{short}\n\n{long}"

    result = await service.create_chunks(full_text=text, source_id="src", store=False)
    assert result.chunks_filtered == 0, (
        f"min_chunk_size=0 must disable coalesce; got chunks_filtered={result.chunks_filtered}"
    )
    # Tiny chunk preserved as its own row (not merged).
    assert any(c["content"].startswith("Tiny.") for c in result.small_chunks), (
        "Tiny chunk must remain as its own row when coalesce is disabled"
    )


@pytest.mark.asyncio
async def test_max_chunk_size_caps_oversized_chunks() -> None:
    """An unsplittable region respects max_chunk_size as a hard cap."""
    service = _build_service(
        small_chunk_size=900,
        small_chunk_overlap=0,
        min_chunk_size=50,
        max_chunk_size=1100,
        normalize_newlines=False,
        normalize_remove_structural_noise=False,
    )

    # No separators of any kind — the splitter falls all the way back to
    # the empty-string separator and produces ~chunk_size chunks. A naive
    # implementation could still emit >1100 chars in edge cases — the cap
    # is enforced post-split as a hard truncation guard.
    text = "x" * 5000
    result = await service.create_chunks(full_text=text, source_id="src", store=False)

    for chunk in result.small_chunks:
        assert len(chunk["content"]) <= 1100, (
            f"max_chunk_size=1100 must hard-cap chunk length; got {len(chunk['content'])}"
        )


@pytest.mark.asyncio
async def test_respect_boundaries_true_vs_false_diverges_on_sentence_text() -> None:
    """respect_boundaries=False produces different chunks than =True for the same input.

    The shape of "different" depends on the text and chunk_size, but the
    contract is that flipping the flag must measurably change the
    splitter's behaviour — proving the setting is wired through.
    """
    # Make a sentence-rich text where ``. ``-aware splitting would land
    # cleanly at sentence ends, but a whitespace-only splitter cannot
    # use that signal and lands at different points.
    text = "Alpha bravo charlie delta. " * 50

    service_aware = _build_service(
        small_chunk_size=120,
        small_chunk_overlap=0,
        min_chunk_size=50,
        max_chunk_size=200,
        respect_boundaries=True,
        normalize_newlines=False,
        normalize_remove_structural_noise=False,
    )
    service_raw = _build_service(
        small_chunk_size=120,
        small_chunk_overlap=0,
        min_chunk_size=50,
        max_chunk_size=200,
        respect_boundaries=False,
        normalize_newlines=False,
        normalize_remove_structural_noise=False,
    )

    aware_result = await service_aware.create_chunks(full_text=text, source_id="src", store=False)
    raw_result = await service_raw.create_chunks(full_text=text, source_id="src", store=False)

    aware_contents = [c["content"] for c in aware_result.small_chunks]
    raw_contents = [c["content"] for c in raw_result.small_chunks]

    assert aware_contents != raw_contents, (
        "respect_boundaries must change the chunk boundaries; got identical "
        "splits with both flag values, so the setting is not wired through."
    )

    # Sentence-aware variant should land on ". " (chunks end with period
    # ignoring trailing whitespace) — at least one chunk does so cleanly.
    aware_sentence_terminated = [c for c in aware_contents if c.rstrip().endswith(".")]
    assert aware_sentence_terminated, (
        "respect_boundaries=True should still prefer sentence boundaries"
    )


def test_quick_mode_max_groups_honored_from_settings() -> None:
    """ChunkingSettings.quick_mode_max_groups overrides the hardcoded 5.

    Build 12 synthetic groups and call _filter_by_depth directly:
    - default max (5) → sample_size == 5
    - override max=8 → sample_size == 8

    This is a direct call to the private method because the public
    ``get_hierarchical_groups`` requires a repository; the private
    ``_filter_by_depth`` is the canonical test seam for the cap logic and is
    tested directly here (exactly as the depth-filter docstring tests do).
    """

    def _make_groups(n: int) -> list[dict[str, object]]:
        return [{"group_index": i, "small_chunk_ids": [str(i)]} for i in range(n)]

    def _make_chunks(n: int) -> list[dict[str, object]]:
        return [{"id": str(i), "chunk_index": i, "content": f"chunk-{i}"} for i in range(n)]

    # --- default cap=5 ----------------------------------------------------------
    svc_default = _build_service()
    all_groups = _make_groups(12)
    all_chunks = _make_chunks(12)
    selected_groups, selected_chunks, groups_skipped = svc_default._filter_by_depth(
        all_chunks, all_groups, "quick"
    )
    assert len(selected_groups) == 5, (
        f"default quick_mode_max_groups=5 must select 5 groups; got {len(selected_groups)}"
    )
    assert groups_skipped == 7, f"expected 7 groups skipped; got {groups_skipped}"

    # --- override cap=8 ---------------------------------------------------------
    svc_capped8 = _build_service(quick_mode_max_groups=8)
    all_groups = _make_groups(12)
    all_chunks = _make_chunks(12)
    selected_groups_8, _, groups_skipped_8 = svc_capped8._filter_by_depth(
        all_chunks, all_groups, "quick"
    )
    assert len(selected_groups_8) == 8, (
        f"quick_mode_max_groups=8 must select 8 groups; got {len(selected_groups_8)}"
    )
    assert groups_skipped_8 == 4, f"expected 4 groups skipped; got {groups_skipped_8}"

    # --- fewer groups than cap → returns all ------------------------------------
    all_groups_small = _make_groups(3)
    all_chunks_small = _make_chunks(3)
    selected_small, _, skipped_small = svc_default._filter_by_depth(
        all_chunks_small, all_groups_small, "quick"
    )
    assert len(selected_small) == 3, "when group count < cap, all groups must be returned"
    assert skipped_small == 0


@pytest.mark.asyncio
async def test_chunks_filtered_count_propagates() -> None:
    """ChunksResult exposes chunks_filtered so the caller can record the counter.

    Post-W5-fix the counter records **merge events**, not drops: a
    sub-threshold chunk that gets folded into its neighbor counts here.
    """
    service = _build_service(
        small_chunk_size=900,
        small_chunk_overlap=0,
        # Make min_chunk_size large enough that the trailing chunk is short
        # relative to the threshold and gets coalesced rather than dropped.
        min_chunk_size=800,
        normalize_newlines=False,
        normalize_remove_structural_noise=False,
    )

    # 1500 chars of "x" — splitter produces a 900-char chunk and a ~600
    # char trailing remainder. Pre-fix that remainder was dropped; post-fix
    # it gets emitted as a tail (last chunk) because it's pending and we
    # don't lose the tail. Either way, the merge-event counter advances.
    text = "x" * 1500
    result = await service.create_chunks(full_text=text, source_id="src", store=False)

    assert hasattr(result, "chunks_filtered"), (
        "ChunksResult must expose chunks_filtered for caller-side counter integration"
    )
    assert result.chunks_filtered >= 1, (
        f"At least one merge event must have been recorded; "
        f"got chunks_filtered={result.chunks_filtered}, "
        f"total_small_chunks={result.total_small_chunks}"
    )
