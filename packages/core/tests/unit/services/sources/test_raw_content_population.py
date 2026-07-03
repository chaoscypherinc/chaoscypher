# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 1, Task 1.2: ``raw_content`` is populated on every chunk dict that
flows into ``DocumentChunk`` persistence.

Covers the live ingestion hot path: ``ChunkingService._create_small_chunks``
builds chunk dicts persisted by ``store_chunks_and_groups``. It threads the
raw, pre-normalization upload text down through ``create_chunks(...,
original_text=...)`` and slices it against the Phase-5a recomputed offsets to
attach a verbatim ``raw_content`` slice.

(The v2.0 package-importer pass-through site — ``SourceLoader._load_chunks``
— was deleted in the CCX 3.0 migration; the CCX 3.0 ``CcxImporter`` chunk
round-trip is covered by ``tests/integration/services/package/
test_ccx_importer.py``.)
"""

from __future__ import annotations

import pytest

from chaoscypher_core.settings import ChunkingSettings, EngineSettings
from chaoscypher_core.utils.chunk import ChunkingService


# ---------------------------------------------------------------------------
# Noisy text fixture — inline (no shared conftest fixture exists for this).
# Includes obvious cleanup targets so the post-normalization ``content``
# diverges from the pre-normalization ``raw_content``:
#
#   * Standalone PDF-style page-header lines ("42 The Kybalion")  → _normalize_text
#     removes them entirely.
#   * Single-newline line wraps inside paragraphs                 → _normalize_text
#     collapses to spaces.
#   * Multiple spaces / trailing whitespace                       → _normalize_text
#     compacts to single spaces.
# ---------------------------------------------------------------------------

_NOISY_TEXT = (
    "This is the first paragraph. It contains real prose that the chunker "
    "should keep intact across normalization. It also has line\nwraps that "
    "the normalizer collapses to spaces, plus multiple   spaces that get "
    "compacted into single spaces by the final regex pass.\n\n"
    "42 The Kybalion\n\n"
    "This is the second paragraph, which arrives after a synthetic PDF page "
    "header that the normalizer strips. It also has its own line\nwraps and "
    "trailing whitespace problems that disappear after cleanup.   \n\n"
    "101 Final Chapter\n\n"
    "This is the third and final paragraph. It is intentionally long enough "
    "that the splitter has plenty of sentence boundaries to anchor on, so we "
    "end up with at least one persisted chunk and not an empty result. "
    "We add a few more sentences here. And one more. And one more for "
    "good measure. Lorem ipsum dolor sit amet, consectetur adipiscing elit, "
    "sed do eiusmod tempor incididunt ut labore et dolore magna aliqua."
)


def _build_service() -> ChunkingService:
    """Build a ChunkingService with normalization + prestrip enabled so the
    diff between ``content`` and ``raw_content`` is guaranteed to fire.
    """
    base = ChunkingSettings()
    overrides = {
        **base.model_dump(),
        "normalize_newlines": True,
        "normalize_remove_structural_noise": True,
        # Keep min_chunk_size small so the short final paragraphs persist.
        "min_chunk_size": 50,
    }
    chunking = ChunkingSettings(**overrides)
    return ChunkingService(settings=EngineSettings(chunking=chunking))


# ---------------------------------------------------------------------------
# Site 1: live ingestion (ChunkingService._create_small_chunks)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raw_content_present_on_every_chunk_when_original_text_supplied() -> None:
    """Every chunk dict gets a non-None ``raw_content`` when ``original_text``
    is threaded into ``create_chunks``.

    This is the production path: ``indexing_handler._persist_original_text``
    captures the raw upload, then passes it into ``create_chunks(...,
    original_text=...)`` (chunk.py:321). With Task 1.2's plumbing, the value
    must flow one level deeper into ``_create_small_chunks`` so each emitted
    chunk dict carries a sliced ``raw_content``.
    """
    service = _build_service()
    result = await service.create_chunks(
        full_text=_NOISY_TEXT,
        source_id="src-raw-content-1",
        store=False,
        original_text=_NOISY_TEXT,
    )

    assert result.small_chunks, "expected at least one persisted chunk"

    nulls = [c for c in result.small_chunks if c.get("raw_content") is None]
    assert not nulls, (
        f"{len(nulls)}/{len(result.small_chunks)} chunks had raw_content=None — "
        "Task 1.2 plumbing did not reach _create_small_chunks"
    )


@pytest.mark.asyncio
async def test_raw_content_differs_from_content_when_cleanup_runs() -> None:
    """At least one chunk must show ``raw_content != content`` when the
    normalizer actually rewrites text.

    The fixture above is engineered to trigger _normalize_text (page-header
    strips, line-wrap collapses, multi-space collapses). If every chunk's
    raw == content, either cleanup didn't run or raw_content is being set
    to the *cleaned* slice instead of the pre-normalization slice.
    """
    service = _build_service()
    result = await service.create_chunks(
        full_text=_NOISY_TEXT,
        source_id="src-raw-content-2",
        store=False,
        original_text=_NOISY_TEXT,
    )

    differing = [c for c in result.small_chunks if c["raw_content"] != c["content"]]
    assert differing, (
        "no chunk had raw_content != content — either cleanup did not run "
        "or raw_content is being populated with the post-cleanup slice"
    )


@pytest.mark.asyncio
async def test_raw_content_is_none_when_original_text_not_supplied() -> None:
    """When the caller does NOT supply ``original_text`` (e.g. legacy code
    paths, tests, or a pre-Phase-1 caller), ``raw_content`` is None rather
    than silently falling back to the post-cleanup ``content``.

    This is the explicit-absence contract the UI relies on to render the
    "raw view unavailable" fallback.
    """
    service = _build_service()
    result = await service.create_chunks(
        full_text=_NOISY_TEXT,
        source_id="src-raw-content-3",
        store=False,
        # original_text intentionally omitted
    )

    assert result.small_chunks, "expected at least one persisted chunk"
    for chunk in result.small_chunks:
        assert chunk.get("raw_content") is None, (
            f"chunk {chunk['id']} had raw_content={chunk['raw_content']!r} "
            "but no original_text was supplied — expected None"
        )
