# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
# ruff: noqa: D301  -- docstrings include literal "\r" / "\n" examples.

"""P2T10 (2026-05-08): chunker-stage quality counters surface on ChunksResult.

Three previously-invisible drop / modification sites in ChunkingService now
report counts on ChunksResult so the indexing handler can increment the
matching QualityCounter rows:

- ``normalize_drops``         → CHUNKER_NORMALIZE_DROPS
- ``prestrip_lines_removed``  → CHUNKER_PRESTRIP_LINES_REMOVED
- ``chunks_skipped_by_depth`` → CHUNKS_SKIPPED_BY_DEPTH

Phase 7 audit-remediation (2026-05-09): Task 3.9 fixes _normalize_text so that
all four passes (page-headers, broken-sentence joins, single-newline-to-space,
space-collapsing) contribute to the returned drop count.
"""

from __future__ import annotations

import pytest

from chaoscypher_core.settings import ChunkingSettings, EngineSettings
from chaoscypher_core.utils.chunk import (
    ChunkingService,
    LocationBoundary,
    LocationIndex,
    _lookup_location,
    merge_location_indexes,
)


def _build_service(**chunking_overrides) -> ChunkingService:
    """Construct a ChunkingService with overridden chunking settings."""
    base = ChunkingSettings()
    overrides = {**base.model_dump(), **chunking_overrides}
    chunking = ChunkingSettings(**overrides)
    return ChunkingService(settings=EngineSettings(chunking=chunking))


# ---------------------------------------------------------------------------
# _normalize_text unit — all four transformation passes counted
# (Phase 7 audit-remediation 2026-05-09, Task 3.9)
# ---------------------------------------------------------------------------


def test_normalize_text_counts_all_four_steps() -> None:
    """_normalize_text returns a drop count covering all four transformation
    passes (page-headers, broken-sentence joins, single-newline-to-space,
    space-collapsing), not just steps 1-2 (Phase 7 audit-remediation 2026-05-09).

    Constructs text that triggers each pass individually:
    1. Page header: "\\n\\n8 Introduction\\n\\n"
    2. Broken-sentence join: "broken-\\n\\njoin" (lowercase either side of \\n\\n)
    3. Single newline between words: "abc\\ndef"
    4. Multiple spaces: "abc   def"
    """
    service = _build_service()
    # Text triggers all four passes:
    # - "\n\n8 Introduction\n\n" → step 1 (page-header)
    # - "broken,\n\njoin" → step 2 (broken-sentence: lowercase/comma before \n\n,
    #   lowercase after)
    # - "abc\ndef" → step 3 (single newline → space)
    # - "abc   def" → step 4 (multiple spaces → single space)
    text = "\n\n8 Introduction\n\nbroken,\n\njoin abc\ndef abc   def"

    normalized, drops = service._normalize_text(text)

    # All four passes must have fired at least once, so drops >= 4.
    assert drops >= 4, (
        f"Expected drops >= 4 (all four passes counted), got {drops}. "
        f"Normalized text: {normalized!r}"
    )


# ---------------------------------------------------------------------------
# Site 1: _normalize_text — page-header regex substitution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalize_drops_counts_page_header_removals() -> None:
    """Page-header lines removed by _normalize_text are counted on normalize_drops.

    Builds text with 3 standalone page-header lines matching the pattern
    ``\\n\\n<digits> <Title>\\n\\n`` (e.g. "8 Introduction"). Each match is
    one substitution; the result must be >= 3.
    """
    # Three page-header patterns embedded in otherwise real paragraphs.
    headers = [
        "\n\n8 Introduction\n\n",
        "\n\n42 The Kybalion\n\n",
        "\n\n101 Final Chapter\n\n",
    ]
    paragraph = "This is a real paragraph with plenty of content. " * 5
    # interleave paragraphs and headers
    text = paragraph.join(["", *headers, ""])

    service = _build_service(
        normalize_newlines=True,
        normalize_remove_structural_noise=False,
    )
    result = await service.create_chunks(full_text=text, source_id="src-norm", store=False)

    assert result.normalize_drops >= 3, (
        f"Expected normalize_drops >= 3 for 3 page headers, got {result.normalize_drops}"
    )


@pytest.mark.asyncio
async def test_normalize_drops_zero_when_normalization_disabled() -> None:
    """normalize_drops must be 0 when normalize_newlines=False."""
    paragraph = "Clean paragraph with no page headers. " * 20
    service = _build_service(
        normalize_newlines=False,
        normalize_remove_structural_noise=False,
    )
    result = await service.create_chunks(full_text=paragraph, source_id="src-norm-off", store=False)
    assert result.normalize_drops == 0, (
        f"normalize_drops must be 0 when normalization is disabled, got {result.normalize_drops}"
    )


# ---------------------------------------------------------------------------
# Site 2: _prestrip_structural_noise — line removal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prestrip_lines_removed_counts_page_numbers() -> None:
    """Lines matching the standalone page-number regex are counted.

    Inserts 4 standalone page-number lines (e.g. "- 12 -") between real
    paragraphs. The result must have prestrip_lines_removed >= 4.
    """
    paragraph = "Substantial body paragraph. " * 10 + "\n"
    page_num_lines = "- 12 -\n- 13 -\n- 14 -\n- 15 -\n"
    text = paragraph + page_num_lines + paragraph

    service = _build_service(
        normalize_newlines=False,
        normalize_remove_structural_noise=True,
    )
    result = await service.create_chunks(full_text=text, source_id="src-prestrip", store=False)

    assert result.prestrip_lines_removed >= 4, (
        f"Expected prestrip_lines_removed >= 4 for 4 page-number lines, "
        f"got {result.prestrip_lines_removed}"
    )


@pytest.mark.asyncio
async def test_prestrip_lines_removed_counts_structural_markers() -> None:
    """Standalone CHAPTER / BOOK / PART marker lines are counted.

    Four structural markers are injected. Each is removed by Pass 2 of
    _prestrip_structural_noise, so prestrip_lines_removed must be >= 4.
    """
    paragraph = "Real narrative content here. " * 10 + "\n"
    markers = "CHAPTER IV\nBOOK 2\nPART III\nCHAPTER VI\n"
    text = paragraph + markers + paragraph

    service = _build_service(
        normalize_newlines=False,
        normalize_remove_structural_noise=True,
    )
    result = await service.create_chunks(full_text=text, source_id="src-markers", store=False)

    assert result.prestrip_lines_removed >= 4, (
        f"Expected prestrip_lines_removed >= 4 for 4 structural marker lines, "
        f"got {result.prestrip_lines_removed}"
    )


@pytest.mark.asyncio
async def test_prestrip_lines_removed_zero_when_disabled() -> None:
    """prestrip_lines_removed must be 0 when normalize_remove_structural_noise=False."""
    text = "- 5 -\nCHAPTER I\nNormal text. " * 5
    service = _build_service(
        normalize_newlines=False,
        normalize_remove_structural_noise=False,
    )
    result = await service.create_chunks(full_text=text, source_id="src-prestrip-off", store=False)
    assert result.prestrip_lines_removed == 0, (
        f"prestrip_lines_removed must be 0 when prestrip is disabled, "
        f"got {result.prestrip_lines_removed}"
    )


# ---------------------------------------------------------------------------
# Site 3: quick-mode group cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunks_skipped_by_depth_for_quick_mode() -> None:
    """Quick analysis_depth with more than 5 groups reports the skipped count.

    Builds enough text to produce >= 10 hierarchical groups (each group
    covers 4 small chunks of ~900 chars each, so we need ~36 000 chars).
    With analysis_depth='quick' the cap is 5 groups; chunks_skipped_by_depth
    must equal total_original_groups - 5.
    """
    # 40 000 chars of real-looking prose to guarantee >= 10 groups.
    sentence = "The quick brown fox jumps over the lazy dog. "
    text = sentence * 900  # ~40 500 chars

    service = _build_service(
        small_chunk_size=900,
        small_chunk_overlap=0,
        normalize_newlines=False,
        normalize_remove_structural_noise=False,
    )
    result = await service.create_chunks(
        full_text=text, source_id="src-depth", analysis_depth="quick", store=False
    )

    total_orig = result.total_original_groups
    assert total_orig > 5, (
        f"Test requires > 5 groups to exercise the cap; got {total_orig}. Increase input length."
    )
    expected_skipped = total_orig - 5
    assert result.chunks_skipped_by_depth == expected_skipped, (
        f"chunks_skipped_by_depth should be {expected_skipped} "
        f"(total={total_orig} - cap=5), got {result.chunks_skipped_by_depth}"
    )


@pytest.mark.asyncio
async def test_chunks_skipped_by_depth_zero_for_full_mode() -> None:
    """analysis_depth='full' must leave chunks_skipped_by_depth == 0."""
    sentence = "The quick brown fox jumps over the lazy dog. "
    text = sentence * 900

    service = _build_service(
        small_chunk_size=900,
        small_chunk_overlap=0,
        normalize_newlines=False,
        normalize_remove_structural_noise=False,
    )
    result = await service.create_chunks(
        full_text=text, source_id="src-depth-full", analysis_depth="full", store=False
    )

    assert result.chunks_skipped_by_depth == 0, (
        f"Full mode must not skip any groups; got "
        f"chunks_skipped_by_depth={result.chunks_skipped_by_depth}"
    )


@pytest.mark.asyncio
async def test_chunks_skipped_by_depth_zero_when_at_or_below_cap() -> None:
    """Quick mode with <= 5 groups does not skip any groups."""
    # Short text → only 1 or 2 groups
    sentence = "Brief content. " * 20
    text = sentence  # ~300 chars, well under a single chunk

    service = _build_service(
        small_chunk_size=900,
        small_chunk_overlap=0,
        normalize_newlines=False,
        normalize_remove_structural_noise=False,
    )
    result = await service.create_chunks(
        full_text=text, source_id="src-depth-small", analysis_depth="quick", store=False
    )

    assert result.chunks_skipped_by_depth == 0, (
        f"quick mode with <= 5 groups must not skip any; "
        f"got chunks_skipped_by_depth={result.chunks_skipped_by_depth} "
        f"(total_original_groups={result.total_original_groups})"
    )


# ---------------------------------------------------------------------------
# Bug 12 regression — _sanitize_text strips CR / CRLF universally
#
# Pre-fix, ChunkingService._sanitize_text only handled BOM removal + 3+
# newline collapse. _normalize_text's regex at step 3 then matched ``\n``
# but left lone ``\r`` chars alone, so CRLF input emerged as ``\r `` (CR
# + space) inside stored chunks. That garbage leaked into LLM prompts,
# inflated token counts, and (because the chunker counted ``\r `` as
# content) caused boundary fragmentation: a 13-chunk CLI run vs 12-chunk
# Cortex run on the same 8KB file. ftfy via ContentNormalizerService
# fixed it when ``enable_normalization=True``, but the CLI's path
# defaulted to skipping normalization for an explicit ``None`` and the
# raw CRLFs survived end-to-end.
#
# Universal scrub now happens inside _sanitize_text — defense in depth.
# These tests pin that contract regardless of normalize_newlines /
# normalize_remove_structural_noise / enable_normalization settings.
# ---------------------------------------------------------------------------


def test_sanitize_text_converts_crlf_to_lf() -> None:
    """Windows-style CRLF line endings collapse to LF."""
    text = "Line one\r\nLine two\r\nLine three"
    out = ChunkingService._sanitize_text(text)
    assert "\r" not in out, f"CRLF should be stripped to LF, but ``\r`` survives: {out!r}"
    assert out == "Line one\nLine two\nLine three"


def test_sanitize_text_converts_lone_cr_to_lf() -> None:
    """Old Mac-style lone CR line endings also collapse to LF."""
    text = "Line one\rLine two\rLine three"
    out = ChunkingService._sanitize_text(text)
    assert "\r" not in out
    assert out == "Line one\nLine two\nLine three"


def test_sanitize_text_handles_mixed_line_endings() -> None:
    """A file with both CRLF and lone-LF (a real Windows-edited-on-Mac
    scenario) normalizes cleanly without producing blank-line runs.
    """
    text = "CRLF line\r\n\r\nLF line\n\nBoth done"
    out = ChunkingService._sanitize_text(text)
    assert "\r" not in out
    # \r\n\r\n → \n\n (two CRLFs become two LFs, paragraph break preserved)
    assert out == "CRLF line\n\nLF line\n\nBoth done"


def test_sanitize_text_strips_bom_then_normalizes_crlf() -> None:
    """The BOM-lstrip and the CRLF-scrub compose without interfering."""
    text = "﻿WAR AND PEACE\r\n\r\nBy Leo Tolstoy"
    out = ChunkingService._sanitize_text(text)
    assert not out.startswith("﻿")
    assert "\r" not in out
    assert out == "WAR AND PEACE\n\nBy Leo Tolstoy"


def test_sanitize_text_does_not_collapse_intentional_blank_paragraph() -> None:
    """Two newlines (a paragraph break) must survive — the 3+ collapse rule
    only fires for runs of 3 or more, not 2.
    """
    text = "Para one.\n\nPara two."
    assert ChunkingService._sanitize_text(text) == "Para one.\n\nPara two."


def test_sanitize_text_collapses_three_or_more_newlines() -> None:
    """Three or more consecutive newlines collapse to a single paragraph
    break, regardless of whether they came from CR/CRLF/LF.
    """
    # Mix CRLF + LF that together produce 5 line endings in a row.
    text = "Top\r\n\r\n\r\n\r\n\r\nBottom"
    out = ChunkingService._sanitize_text(text)
    assert out == "Top\n\nBottom"


def test_sanitize_text_preserves_real_content_across_newlines() -> None:
    """Sanitization must not eat any non-whitespace content. Pin against a
    chunk drawn from the war_and_peace_tiny smoke test where the original
    bug produced ``\r `` separators between words.
    """
    text = (
        "WAR AND PEACE\r\n\r\nBy Leo Tolstoy/Tolstoi\r\n\r\n"
        "    Contents\r\n\r\n    BOOK ONE: 1805\r\n"
    )
    out = ChunkingService._sanitize_text(text)
    # No CR survives anywhere.
    assert "\r" not in out
    # Every non-whitespace token survives in original order.
    expected_tokens = [
        "WAR",
        "AND",
        "PEACE",
        "By",
        "Leo",
        "Tolstoy/Tolstoi",
        "Contents",
        "BOOK",
        "ONE:",
        "1805",
    ]
    actual_tokens = out.split()
    assert actual_tokens == expected_tokens, (
        f"Token sequence drifted during sanitize. expected={expected_tokens} actual={actual_tokens}"
    )


# ---------------------------------------------------------------------------
# Location lookup + merge tests (2026-05-18 chunk-location-unification)
# ---------------------------------------------------------------------------


def _make_page_boundary(start: int, end: int, page: int) -> LocationBoundary:
    return {
        "start_char": start,
        "end_char": end,
        "page_number": page,
        "section": None,
    }


def _make_section_boundary(start: int, end: int, section: str) -> LocationBoundary:
    return {
        "start_char": start,
        "end_char": end,
        "page_number": None,
        "section": section,
    }


def test_lookup_location_returns_none_for_empty_index() -> None:
    assert _lookup_location(None, 50) == (None, None)
    assert _lookup_location([], 50) == (None, None)


def test_lookup_location_finds_page_for_char_start() -> None:
    index: LocationIndex = [
        _make_page_boundary(0, 100, 1),
        _make_page_boundary(100, 250, 2),
        _make_page_boundary(250, 400, 3),
    ]
    assert _lookup_location(index, 0) == (1, None)
    assert _lookup_location(index, 99) == (1, None)
    assert _lookup_location(index, 100) == (2, None)  # boundary edge: start of range 2
    assert _lookup_location(index, 249) == (2, None)
    assert _lookup_location(index, 250) == (3, None)


def test_lookup_location_returns_none_when_char_start_past_index() -> None:
    index: LocationIndex = [_make_page_boundary(0, 100, 1)]
    assert _lookup_location(index, 200) == (None, None)


def test_lookup_location_finds_section() -> None:
    index: LocationIndex = [
        _make_section_boundary(0, 500, "Chapter 1"),
        _make_section_boundary(500, 1200, "Chapter 2"),
    ]
    assert _lookup_location(index, 0) == (None, "Chapter 1")
    assert _lookup_location(index, 499) == (None, "Chapter 1")
    assert _lookup_location(index, 500) == (None, "Chapter 2")


def test_merge_location_indexes_shifts_offsets_for_second_doc() -> None:
    """When two documents are joined with '\\n\\n', boundaries in the
    second document must be shifted by len(doc1_content) + 2.
    """
    doc1_content = "Hello world"  # 11 chars
    doc1_index: LocationIndex = [_make_page_boundary(0, 11, 1)]

    doc2_content = "Second doc text"  # 15 chars
    doc2_index: LocationIndex = [_make_page_boundary(0, 15, 2)]

    merged = merge_location_indexes(
        [(doc1_content, doc1_index), (doc2_content, doc2_index)],
        separator="\n\n",
    )

    # doc1 entry unchanged; doc2 entry shifted by 11 + 2 = 13.
    assert merged == [
        {"start_char": 0, "end_char": 11, "page_number": 1, "section": None},
        {"start_char": 13, "end_char": 28, "page_number": 2, "section": None},
    ]


def test_merge_location_indexes_skips_documents_without_index() -> None:
    """A document whose loader didn't emit a location_index contributes
    nothing to the merged index, but its content still shifts subsequent
    offsets so following documents land at the correct position.
    """
    doc1_content = "First"  # 5 chars
    doc2_content = "Second"  # 6 chars; no index
    doc3_content = "Third"  # 5 chars
    doc3_index: LocationIndex = [_make_page_boundary(0, 5, 1)]

    merged = merge_location_indexes(
        [(doc1_content, None), (doc2_content, None), (doc3_content, doc3_index)],
        separator="\n\n",
    )

    # Cumulative offset before doc3: 5 + 2 + 6 + 2 = 15.
    assert merged == [
        {"start_char": 15, "end_char": 20, "page_number": 1, "section": None},
    ]


def test_merge_location_indexes_returns_empty_when_all_none() -> None:
    merged = merge_location_indexes(
        [("doc1", None), ("doc2", None)],
        separator="\n\n",
    )
    assert merged == []


@pytest.mark.asyncio
async def test_create_chunks_populates_page_number_from_location_index() -> None:
    """When create_chunks receives a location_index, small_chunks get
    page_number set per the lookup. The lookup runs after Phase 5a so
    coordinates match the index built by loaders.
    """
    service = _build_service()
    page1 = "First page text. " * 60  # ~1020 chars
    page2 = "Second page text. " * 60  # ~1080 chars
    full_text = page1 + "\n\n" + page2
    page1_end = len(page1)
    index: LocationIndex = [
        _make_page_boundary(0, page1_end + 2, 1),  # +2 for the "\n\n" separator
        _make_page_boundary(page1_end + 2, len(full_text), 2),
    ]

    result = await service.create_chunks(
        full_text=full_text,
        source_id="test-src",
        store=False,
        location_index=index,
    )

    assert len(result.small_chunks) >= 2
    # First chunk starts at 0 → page 1. Last chunk starts past the
    # boundary → page 2.
    assert result.small_chunks[0]["page_number"] == 1
    assert result.small_chunks[-1]["page_number"] == 2


@pytest.mark.asyncio
async def test_create_chunks_omits_page_number_when_no_location_index() -> None:
    """Backwards compatibility: loaders that don't emit a location_index
    still produce chunks (with page_number=None and section=None).
    """
    service = _build_service()
    text = "A short document with no location information. " * 30
    result = await service.create_chunks(
        full_text=text,
        source_id="test-src",
        store=False,
    )
    assert len(result.small_chunks) >= 1
    for chunk in result.small_chunks:
        assert chunk["page_number"] is None
        assert chunk["section"] is None


# ---------------------------------------------------------------------------
# Vision-augmentation regression: PDF page_number after _page_texts grows
# (2026-05-18 bug found post-deploy: vision_finalizer mutates _page_texts
# in place, invalidating the PDF loader's original location_index.)
# ---------------------------------------------------------------------------


def test_build_pdf_location_index_covers_full_joined_content() -> None:
    """build_pdf_location_index must produce an index whose char ranges
    cover the full joined text (no gaps within pages; only "\\n\\n"
    separators between pages).
    """
    from chaoscypher_core.utils.chunk import build_pdf_location_index

    page_texts = ["First page (14)", "Second page (15)", "Third (11)."]
    # Lengths: 15, 16, 11 → joined len = 15 + 2 + 16 + 2 + 11 = 46
    joined = "\n\n".join(page_texts)
    index = build_pdf_location_index(page_texts)

    assert index == [
        {"start_char": 0, "end_char": 15, "page_number": 1, "section": None},
        {"start_char": 17, "end_char": 33, "page_number": 2, "section": None},
        {"start_char": 35, "end_char": 46, "page_number": 3, "section": None},
    ]
    # Last entry's end_char must reach the joined text length (separators are
    # between pages; nothing follows the last page).
    assert index[-1]["end_char"] == len(joined)


def test_build_pdf_location_index_handles_vision_augmented_page_texts() -> None:
    """Regression for the vision bug: vision_finalizer appends
    "[Visual Content]<desc>" to each page_text in place, growing _page_texts
    well beyond the PDF loader's original output. Rebuilding the index from
    the CURRENT _page_texts must cover the full augmented range — every
    chunk's char_start must land inside some boundary so page_number is
    assigned (not left as None).
    """
    from chaoscypher_core.utils.chunk import (
        _lookup_location,
        build_pdf_location_index,
    )

    # Simulate what vision does: original ~800-char pages augmented with
    # ~3500-char descriptions wrapped in [Visual Content] markers.
    page1 = (
        "PAGE 1 body. " * 60
        + "\n\n[Visual Content]\n"
        + "image one. " * 300
        + "\n[/Visual Content]"
    )
    page2 = (
        "PAGE 2 body. " * 60
        + "\n\n[Visual Content]\n"
        + "image two. " * 500
        + "\n[/Visual Content]"
    )
    page_texts = [page1, page2]
    joined = "\n\n".join(page_texts)

    index = build_pdf_location_index(page_texts)

    # Sanity: index covers [0, len(joined)).
    assert index[0]["start_char"] == 0
    assert index[-1]["end_char"] == len(joined)

    # Probe every 500 chars across the joined text — every lookup must
    # return a valid page (no Nones inside the boundary coverage).
    for char_start in range(0, len(joined), 500):
        page, section = _lookup_location(index, char_start)
        assert page in (1, 2), (
            f"char_start={char_start} returned page={page}, expected 1 or 2. "
            f"Index ranges: {[(b['start_char'], b['end_char'], b['page_number']) for b in index]}"
        )

    # And probe at the exact page-boundary edge.
    p1_end = len(page1)
    page_at_p1_end_minus_1, _ = _lookup_location(index, p1_end - 1)
    page_at_p2_start, _ = _lookup_location(index, p1_end + 2)
    assert page_at_p1_end_minus_1 == 1
    assert page_at_p2_start == 2
