# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 1, Task 1.2: ``raw_content`` is populated on every chunk dict that
flows into ``DocumentChunk`` persistence.

Loader call-site audit (Task 0.1) identified two real persistence sites:

1. ``ChunkingService._create_small_chunks`` (the live ingestion hot path) —
   builds chunk dicts persisted by ``store_chunks_and_groups``. Threads the
   raw, pre-normalization upload text down through ``create_chunks(...,
   original_text=...)`` and slices it against the Phase-5a recomputed
   offsets to attach a verbatim ``raw_content`` slice.
2. ``PackageSourcesLoader._load_chunks`` (offline ``.ccpkg`` restore) —
   pure pass-through: the exported chunk record may already carry
   ``raw_content`` from a Phase-1+ exporter; forward it untouched.

This file covers site 1 end-to-end (the in-process path that operators hit
on every real upload) and asserts the pass-through contract for site 2 by
exercising the chunk-record construction directly.
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


# ---------------------------------------------------------------------------
# Site 2: package-importer pass-through (SourceLoader._load_chunks)
#
# We verify the chunk_record dict literal forwards ``raw_content`` from the
# export bundle. Stand up the real loader against an in-memory SqliteAdapter
# (which already implements the storage protocols the loader expects) and
# call ``_load_chunks`` with a synthetic chunk payload — the persisted row's
# raw_content must equal the input. The loader rewrites IDs internally
# (generate_id("chunk")), so we read back via the new IDs the mapper tracks.
# ---------------------------------------------------------------------------


def test_package_importer_forwards_raw_content_from_export_bundle(
    in_memory_adapter,
) -> None:
    """``SourceLoader._load_chunks`` forwards ``raw_content`` from the
    exported chunk record to the persisted ``DocumentChunk`` row.

    Pre-Phase-1 exports legitimately omit ``raw_content``; verify the loader
    tolerates the absence by writing NULL rather than KeyError-ing.
    """
    from datetime import UTC, datetime

    from chaoscypher_core.services.package.importer.loaders.sources import SourceLoader
    from chaoscypher_core.services.package.importer.models import IdMapper, ImportStats

    # Seed a source row so the FK on document_chunks.source_id resolves.
    # The importer's create_source path requires the full SourceRow shape, so
    # use the adapter's create_source helper which mirrors what the real
    # _load_source step does.
    source_record = {
        "id": "src-pkg-raw-1",
        "database_name": "default",
        "filename": "synthetic.txt",
        "filepath": "/synthetic.txt",
        "title": "synthetic",
        "source_type": "text",
        "status": "committed",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    in_memory_adapter.create_source(source_record)

    loader = SourceLoader(sources_repository=in_memory_adapter)
    mapper = IdMapper()
    stats = ImportStats()

    chunks_data = [
        {
            "id": "chunk-with-raw",
            "chunk_index": 0,
            "content": "cleaned content here",
            "raw_content": "RAW pre-cleanup content here",
            "embedding": None,
            "embedding_model": None,
            "embedding_dimensions": None,
            "page_number": None,
            "section": None,
            "metadata": {},
            "status": "committed",
        },
        {
            "id": "chunk-without-raw",
            "chunk_index": 1,
            "content": "another cleaned chunk",
            # raw_content intentionally absent (pre-Phase-1 export)
            "embedding": None,
            "embedding_model": None,
            "embedding_dimensions": None,
            "page_number": None,
            "section": None,
            "metadata": {},
            "status": "committed",
        },
    ]

    loader._load_chunks(
        chunks_data=chunks_data,
        source_id=source_record["id"],
        mapper=mapper,
        stats=stats,
        database_name="default",
    )

    assert stats.chunks_imported == 2, (
        f"expected 2 chunks imported, got {stats.chunks_imported}; "
        f"warnings={stats.warnings} errors={stats.errors}"
    )

    new_with_raw = mapper.get_chunk_id("chunk-with-raw")
    new_without_raw = mapper.get_chunk_id("chunk-without-raw")
    assert new_with_raw and new_without_raw, "mapper did not record new chunk IDs"

    persisted_with = in_memory_adapter.get_chunk_by_id(new_with_raw)
    persisted_without = in_memory_adapter.get_chunk_by_id(new_without_raw)

    assert persisted_with is not None
    assert persisted_without is not None
    assert persisted_with.get("raw_content") == "RAW pre-cleanup content here", (
        "package importer dropped raw_content during _load_chunks"
    )
    assert persisted_without.get("raw_content") is None, (
        "pre-Phase-1 export missing raw_content should persist as NULL"
    )
