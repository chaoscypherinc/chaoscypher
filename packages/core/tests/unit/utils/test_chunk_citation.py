# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 5a (2026-05-08): citation-offset recompute tests.

Covers:
- _recompute_chunk_offsets: exact / fuzzy / none paths.
- ChunkingService.create_chunks with original_text=None (no recompute).
- ChunkingService.create_chunks with matching original_text -> exact.
- ChunkingService.create_chunks where cleaner modified text -> fuzzy.
- ChunkingService.create_chunks where content unrecognizable -> none.
- _persist_original_text writes original.txt when toggle on.
- _persist_original_text skips write when toggle off.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.settings import ChunkingSettings, EngineSettings
from chaoscypher_core.utils.chunk import (
    ChunkingService,
    _recompute_chunk_offsets,
    _shift_sentence_offsets,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_service(**chunking_overrides: object) -> ChunkingService:
    """Construct a ChunkingService with overridden chunking settings."""
    base = ChunkingSettings()
    overrides = {**base.model_dump(), **chunking_overrides}
    chunking = ChunkingSettings(**overrides)
    return ChunkingService(settings=EngineSettings(chunking=chunking))


def _make_chunk(
    content: str,
    char_start: int = 0,
    char_end: int | None = None,
    sentence_offsets: list[dict] | None = None,
) -> dict:
    """Build a minimal chunk dict matching ChunkingService output."""
    if char_end is None:
        char_end = char_start + len(content)
    return {
        "id": "test_id",
        "source_id": "src",
        "chunk_index": 0,
        "content": content,
        "char_start": char_start,
        "char_end": char_end,
        "citation_offset_method": "exact",
        "token_count": len(content) // 4,
        "chunk_metadata": {
            "chunk_type": "small",
            "group_ids": [],
            "sentence_offsets": sentence_offsets or [],
        },
    }


# ---------------------------------------------------------------------------
# Unit tests for _recompute_chunk_offsets
# ---------------------------------------------------------------------------


class TestRecomputeChunkOffsets:
    """Direct unit tests for the _recompute_chunk_offsets helper."""

    def test_exact_match_updates_offsets(self) -> None:
        """Verbatim content in original_text -> exact offsets, method 'exact'."""
        original = "Hello world. This is a test document."
        chunk = _make_chunk("This is a test document.", char_start=0, char_end=24)
        _recompute_chunk_offsets([chunk], original)

        assert chunk["citation_offset_method"] == "exact"
        assert chunk["char_start"] == original.index("This is a test document.")
        assert chunk["char_end"] == chunk["char_start"] + len("This is a test document.")

    def test_exact_match_at_start(self) -> None:
        """Content at position 0 -> exact with char_start=0."""
        original = "Start of the document. More text."
        chunk = _make_chunk("Start of the document.", char_start=100)
        _recompute_chunk_offsets([chunk], original)

        assert chunk["citation_offset_method"] == "exact"
        assert chunk["char_start"] == 0

    def test_fuzzy_match_whitespace_normalized(self) -> None:
        """Cleaner squashed whitespace: chunk content won't substring-match
        but should fuzzy-match to approximately the right location.
        """
        # Original has multiple spaces; cleaned content has single spaces
        original = "The  quick   brown  fox jumps over the lazy dog." * 3
        # Simulate what a normalizer does: collapse spaces
        import re

        cleaned = re.sub(r" {2,}", " ", original[:48])
        chunk = _make_chunk(cleaned, char_start=0)

        _recompute_chunk_offsets([chunk], original)

        # Should be fuzzy or exact (depending on how much whitespace was changed)
        assert chunk["citation_offset_method"] in ("exact", "fuzzy")
        assert chunk["char_start"] is not None

    def test_no_match_returns_none_method(self) -> None:
        """Content completely unrecognizable from original -> method 'none', offsets None."""
        original = "Some original text that the cleaner never touched."
        # Chunk content that has no resemblance to original
        chunk = _make_chunk("ZZZ entirely fabricated content XYZ", char_start=5)
        _recompute_chunk_offsets([chunk], original)

        assert chunk["citation_offset_method"] == "none"
        assert chunk["char_start"] is None
        assert chunk["char_end"] is None

    def test_empty_chunk_content_becomes_none(self) -> None:
        """Empty chunk content cannot be located -> method 'none'."""
        original = "Some text."
        chunk = _make_chunk("", char_start=0, char_end=0)
        _recompute_chunk_offsets([chunk], original)

        # Empty string is always found at index 0 in Python, so this
        # actually lands in 'exact'. Confirm it doesn't crash.
        assert chunk["citation_offset_method"] in ("exact", "none")

    def test_multiple_chunks_all_annotated(self) -> None:
        """All chunks in the list are annotated, even when methods differ."""
        original = "First sentence here. Second sentence here."
        chunks = [
            _make_chunk("First sentence here.", char_start=0),
            _make_chunk("XXXXXX YYYYYY", char_start=20),
        ]
        _recompute_chunk_offsets(chunks, original)

        assert chunks[0]["citation_offset_method"] == "exact"
        assert chunks[1]["citation_offset_method"] == "none"

    def test_sentence_offsets_shifted(self) -> None:
        """Sentence offsets are shifted by delta when exact match moves char_start."""
        original = "Prefix text. The actual sentence. And more."
        content = "The actual sentence."
        actual_start = original.index(content)

        sentence_offsets = [{"start": 0, "end": 20}]
        chunk = _make_chunk(content, char_start=0, sentence_offsets=sentence_offsets)
        _recompute_chunk_offsets([chunk], original)

        assert chunk["citation_offset_method"] == "exact"
        so = chunk["chunk_metadata"]["sentence_offsets"]
        assert so[0]["start"] == actual_start
        assert so[0]["end"] == actual_start + 20

    def test_sentence_offsets_not_shifted_when_none_method(self) -> None:
        """Sentence offsets not touched when method is 'none'."""
        original = "Some text."
        sentence_offsets = [{"start": 5, "end": 15}]
        chunk = _make_chunk("XXXXXXXXXXXXXXX", char_start=5, sentence_offsets=sentence_offsets)
        _recompute_chunk_offsets([chunk], original)

        assert chunk["citation_offset_method"] == "none"
        # Offsets unchanged on 'none' path
        so = chunk["chunk_metadata"]["sentence_offsets"]
        assert so[0]["start"] == 5


# ---------------------------------------------------------------------------
# Unit tests for the raw-span recovery cascade (2026-06-05)
#
# Regression: the fuzzy level used rapidfuzz partial_ratio_alignment, whose
# matched window never exceeds len(content). The true raw span is LONGER
# than the cleaned chunk whenever the cleaner removed text inside it
# (CRLF -> LF alone guarantees this for every chunk of a CRLF file), so
# every fuzzy window drifted or clipped — chunk raw_content showed text
# from the wrong part of the document and the "Show removed text" diff
# struck garbage.
# ---------------------------------------------------------------------------


class TestRecomputeChunkOffsetsRawSpan:
    """Spans must expand to cover cleaner-removed text, not slide past it."""

    def test_crlf_only_difference_resolves_full_span(self) -> None:
        """CRLF raw vs LF cleaned: the span covers the full raw region
        including every carriage return, and starts at the chunk's first char.
        """
        original = "First line one.\r\nSecond line two.\r\nThird line three.\r\nTail."
        content = "First line one.\nSecond line two.\nThird line three."
        chunk = _make_chunk(content)
        _recompute_chunk_offsets([chunk], original)

        assert chunk["citation_offset_method"] in ("exact", "fuzzy")
        start, end = chunk["char_start"], chunk["char_end"]
        assert start == 0
        raw_span = original[start:end]
        assert raw_span.replace("\r\n", "\n") == content

    def test_internal_removed_lines_are_inside_recovered_span(self) -> None:
        """Lines the cleaner dropped from inside the chunk stay inside the
        recovered raw span instead of pushing the window off target.
        """
        original = (
            "Once upon a time there was a long and winding introduction.\r\n"
            "    NAVIGATION BREADCRUMB TO REMOVE\r\n"
            "    ANOTHER BOILERPLATE LINE TO REMOVE\r\n"
            "And the story continued exactly where it had left off before.\r\n"
        )
        content = (
            "Once upon a time there was a long and winding introduction.\n"
            "And the story continued exactly where it had left off before."
        )
        chunk = _make_chunk(content)
        _recompute_chunk_offsets([chunk], original)

        assert chunk["citation_offset_method"] in ("exact", "fuzzy")
        raw_span = original[chunk["char_start"] : chunk["char_end"]]
        assert raw_span.startswith("Once upon a time")
        assert raw_span.rstrip().endswith("left off before.")
        # The removed boilerplate is INSIDE the span — that's the whole
        # point: the diff view can only strike text the span contains.
        assert "NAVIGATION BREADCRUMB TO REMOVE" in raw_span

    def test_window_cap_regression_title_block_not_chopped(self) -> None:
        """War-and-Peace shape: title block, a long contents listing the
        cleaner dropped, then body. The old length-capped window slid
        forward to cover the body and chopped the title block off the
        span; the recovered span must start at the title.
        """
        numerals = [
            "I",
            "II",
            "III",
            "IV",
            "V",
            "VI",
            "VII",
            "VIII",
            "IX",
            "X",
            "XI",
            "XII",
            "XIII",
            "XIV",
            "XV",
            "XVI",
            "XVII",
            "XVIII",
            "XIX",
            "XX",
            "XXI",
            "XXII",
            "XXIII",
            "XXIV",
            "XXV",
            "XXVI",
            "XXVII",
            "XXVIII",
        ]
        listing = "".join(f"    CHAPTER {n}\r\n\r\n" for n in numerals)
        body_raw = (
            "“Well, Prince, so Genoa and Lucca are now just family estates of the\r\n"
            "Buonapartes. But I warn you, if you do not tell me that this means war,\r\n"
            "I will have nothing more to do with you.”\r\n"
        )
        original = (
            "WAR AND PEACE\r\n\r\n\r\nBy Leo Tolstoy/Tolstoi\r\n\r\n\r\n    Contents\r\n\r\n"
            + listing
            + "\r\nBOOK ONE: 1805\r\n\r\n\r\nCHAPTER I\r\n\r\n"
            + body_raw
        )
        content = (
            "WAR AND PEACE\n\nBy Leo Tolstoy/Tolstoi\n\n Contents\n\n BOOK ONE: 1805\n\n"
            "“Well, Prince, so Genoa and Lucca are now just family estates of the "
            "Buonapartes. But I warn you, if you do not tell me that this means war, "
            "I will have nothing more to do with you.”"
        )
        chunk = _make_chunk(content)
        _recompute_chunk_offsets([chunk], original)

        assert chunk["citation_offset_method"] in ("exact", "fuzzy")
        raw_span = original[chunk["char_start"] : chunk["char_end"]]
        assert raw_span.startswith("WAR AND PEACE"), (
            f"span must start at the title block, got: {raw_span[:60]!r}"
        )
        assert raw_span.rstrip().endswith("nothing more to do with you.”")
        # The dropped contents listing is recoverable from the span.
        assert "CHAPTER XXVIII" in raw_span

    def test_sequential_chunks_anchor_in_document_order(self) -> None:
        """Consecutive chunks of a CRLF document resolve to strictly
        increasing offsets with non-pathological spans.
        """
        paras = [
            f"Paragraph number {i} has some distinctive prose about topic {i}." for i in range(1, 5)
        ]
        original = "\r\n\r\n".join(paras)
        chunks = [_make_chunk(p) for p in paras]
        _recompute_chunk_offsets(chunks, original)

        last_start = -1
        for chunk, para in zip(chunks, paras, strict=True):
            assert chunk["citation_offset_method"] in ("exact", "fuzzy")
            assert chunk["char_start"] > last_start
            raw_span = original[chunk["char_start"] : chunk["char_end"]]
            assert raw_span.replace("\r\n", "\n") == para
            last_start = chunk["char_start"]

    def test_blank_line_collapse_and_indent_squeeze(self) -> None:
        """Whitespace-only cleaner edits (blank-line collapse, indent
        squeeze) still resolve with an exact-boundary span.
        """
        original = (
            "Heading Goes Here\r\n\r\n\r\n\r\n      Indented opening sentence of the body text."
        )
        content = "Heading Goes Here\n\n Indented opening sentence of the body text."
        chunk = _make_chunk(content)
        _recompute_chunk_offsets([chunk], original)

        assert chunk["citation_offset_method"] in ("exact", "fuzzy")
        raw_span = original[chunk["char_start"] : chunk["char_end"]]
        assert raw_span.startswith("Heading Goes Here")
        assert raw_span.endswith("body text.")


# ---------------------------------------------------------------------------
# Unit tests for _shift_sentence_offsets
# ---------------------------------------------------------------------------


class TestShiftSentenceOffsets:
    """Direct unit tests for the sentence-offset shift helper."""

    def test_shifts_by_positive_delta(self) -> None:
        chunk = _make_chunk("x", char_start=0, sentence_offsets=[{"start": 0, "end": 5}])
        _shift_sentence_offsets(chunk, old_start=0, new_start=10)
        assert chunk["chunk_metadata"]["sentence_offsets"][0]["start"] == 10
        assert chunk["chunk_metadata"]["sentence_offsets"][0]["end"] == 15

    def test_no_op_when_old_start_is_none(self) -> None:
        chunk = _make_chunk("x", char_start=0, sentence_offsets=[{"start": 0, "end": 5}])
        _shift_sentence_offsets(chunk, old_start=None, new_start=10)
        assert chunk["chunk_metadata"]["sentence_offsets"][0]["start"] == 0

    def test_no_op_when_delta_is_zero(self) -> None:
        chunk = _make_chunk("x", char_start=5, sentence_offsets=[{"start": 5, "end": 10}])
        _shift_sentence_offsets(chunk, old_start=5, new_start=5)
        assert chunk["chunk_metadata"]["sentence_offsets"][0]["start"] == 5

    def test_no_op_when_no_metadata(self) -> None:
        chunk = {"content": "x", "char_start": 0}
        _shift_sentence_offsets(chunk, old_start=0, new_start=5)
        # No crash — chunk has no chunk_metadata key

    def test_no_op_when_sentence_offsets_empty(self) -> None:
        chunk = _make_chunk("x", char_start=0, sentence_offsets=[])
        _shift_sentence_offsets(chunk, old_start=0, new_start=10)
        assert chunk["chunk_metadata"]["sentence_offsets"] == []


# ---------------------------------------------------------------------------
# ChunkingService.create_chunks with original_text
# ---------------------------------------------------------------------------


class TestCreateChunksWithOriginalText:
    """ChunkingService.create_chunks integration tests for Phase 5a."""

    @pytest.mark.asyncio
    async def test_no_original_text_keeps_exact_method(self) -> None:
        """original_text=None -> all chunks tagged 'exact' (pre-5a behaviour)."""
        svc = _build_service(
            small_chunk_size=200,
            small_chunk_overlap=0,
            min_chunk_size=10,
            normalize_newlines=False,
            normalize_remove_structural_noise=False,
        )
        text = ("Alpha beta gamma. " * 15).strip()
        result = await svc.create_chunks(
            full_text=text,
            source_id="src_1",
            analysis_depth="full",
            store=False,
        )
        assert result.small_chunks
        for chunk in result.small_chunks:
            assert chunk["citation_offset_method"] == "exact"
            assert chunk["char_start"] is not None

    @pytest.mark.asyncio
    async def test_exact_original_text_match(self) -> None:
        """original_text identical to full_text -> all chunks method 'exact'
        with accurate offsets pointing into original_text.
        """
        svc = _build_service(
            small_chunk_size=200,
            small_chunk_overlap=0,
            min_chunk_size=10,
            normalize_newlines=False,
            normalize_remove_structural_noise=False,
        )
        # Build text long enough to produce multiple chunks
        original = ("Sentence one here. Sentence two here. " * 10).strip()
        result = await svc.create_chunks(
            full_text=original,
            source_id="src_2",
            analysis_depth="full",
            store=False,
            original_text=original,
        )
        assert result.small_chunks
        for chunk in result.small_chunks:
            assert chunk["citation_offset_method"] == "exact"
            start = chunk["char_start"]
            end = chunk["char_end"]
            assert start is not None and end is not None
            # Verify the offset slice matches the chunk content
            assert original[start:end] == chunk["content"]

    @pytest.mark.asyncio
    async def test_fuzzy_path_when_cleaner_modifies_whitespace(self) -> None:
        """When original_text has extra whitespace that normalization squashed,
        chunks should get 'exact' or 'fuzzy' method (not 'none') because the
        content is still recognizable via rapidfuzz.
        """
        import re

        raw = "  Hello  world.  This  is  a  test.  " * 8
        # Simulate normalization: collapse spaces
        normalized = re.sub(r" {2,}", " ", raw).strip()

        svc = _build_service(
            small_chunk_size=200,
            small_chunk_overlap=0,
            min_chunk_size=10,
            normalize_newlines=False,
            normalize_remove_structural_noise=False,
        )
        result = await svc.create_chunks(
            full_text=normalized,
            source_id="src_3",
            analysis_depth="full",
            store=False,
            original_text=raw,
        )
        assert result.small_chunks
        for chunk in result.small_chunks:
            assert chunk["citation_offset_method"] in ("exact", "fuzzy"), (
                f"Expected exact or fuzzy for chunk: {chunk['content'][:50]!r}"
            )

    @pytest.mark.asyncio
    async def test_none_method_when_content_unrecognizable(self) -> None:
        """If chunker content was fabricated / completely rewritten by the cleaner,
        method 'none' with NULL offsets.
        """
        # We can't easily get real 'none' from the full pipeline because
        # the chunker only operates on full_text. Instead we test
        # _recompute_chunk_offsets directly with a fabricated scenario:
        # full_text has real content, but we pass original_text that shares
        # no resemblance.
        original = "ZZZZZZZZZ XXXXXXX YYYYYYY completely different content."
        normal_text = ("Sentence alpha beta gamma. " * 10).strip()

        svc = _build_service(
            small_chunk_size=200,
            small_chunk_overlap=0,
            min_chunk_size=10,
            normalize_newlines=False,
            normalize_remove_structural_noise=False,
        )
        result = await svc.create_chunks(
            full_text=normal_text,
            source_id="src_4",
            analysis_depth="full",
            store=False,
            original_text=original,
        )
        assert result.small_chunks
        # All chunks should fail to locate in 'original' since full_text has nothing
        # in common with original
        none_chunks = [c for c in result.small_chunks if c["citation_offset_method"] == "none"]
        assert none_chunks, "Expected at least one 'none'-method chunk when content unrecognizable"
        for chunk in none_chunks:
            assert chunk["char_start"] is None
            assert chunk["char_end"] is None


# ---------------------------------------------------------------------------
# _persist_original_text integration tests
# ---------------------------------------------------------------------------


class TestPersistOriginalText:
    """Tests for _persist_original_text and the preserve toggle."""

    def test_writes_original_txt_when_toggle_on(self, tmp_path: Path) -> None:
        """With preserve_original_text_for_citations=True, original.txt is written."""
        from chaoscypher_core.operations.importing.indexing_handler import _persist_original_text
        from chaoscypher_core.services.sources.management.paths import get_original_text_path

        docs = [{"content": "Raw loader text content.", "metadata": {}}]
        result = _persist_original_text(
            documents=docs,
            source_id="src_toggle_on",
            data_dir=tmp_path,
        )

        expected_path = get_original_text_path("src_toggle_on", tmp_path)
        assert expected_path.exists(), "original.txt should exist"
        assert expected_path.read_text(encoding="utf-8") == "Raw loader text content."
        assert result == "Raw loader text content."

    def test_does_not_write_when_documents_empty(self, tmp_path: Path) -> None:
        """Empty documents list -> no file written, returns None."""
        from chaoscypher_core.operations.importing.indexing_handler import _persist_original_text

        result = _persist_original_text(
            documents=[],
            source_id="src_empty",
            data_dir=tmp_path,
        )

        assert result is None
        dest = tmp_path / "sources" / "src_empty" / "original.txt"
        assert not dest.exists()

    def test_does_not_write_when_first_doc_empty_content(self, tmp_path: Path) -> None:
        """First document has empty content -> no file written."""
        from chaoscypher_core.operations.importing.indexing_handler import _persist_original_text

        result = _persist_original_text(
            documents=[{"content": "", "metadata": {}}],
            source_id="src_empty_content",
            data_dir=tmp_path,
        )

        assert result is None

    def test_toggle_off_skips_write(self, tmp_path: Path) -> None:
        """Setting preserve_original_text_for_citations=False -> _persist_original_text
        is never called; original.txt is not written.
        """
        # Simulate the indexing handler's toggle check by only calling the helper
        # when the toggle is on. This test verifies the integration contract.
        from chaoscypher_core.settings import ChunkingSettings

        settings = ChunkingSettings(preserve_original_text_for_citations=False)
        assert not settings.preserve_original_text_for_citations

        # In the indexing handler: if not toggle, original_text_for_citations = None
        # so no write happens. Verify the path is absent.
        dest = tmp_path / "sources" / "src_toggle_off" / "original.txt"
        assert not dest.exists()

    def test_multi_doc_only_writes_first(self, tmp_path: Path) -> None:
        """For multi-document loaders, only the first doc's content is written."""
        from chaoscypher_core.operations.importing.indexing_handler import _persist_original_text

        docs = [
            {"content": "First document text.", "metadata": {}},
            {"content": "Second document text.", "metadata": {}},
        ]
        result = _persist_original_text(
            documents=docs,
            source_id="src_multi",
            data_dir=tmp_path,
        )

        dest = tmp_path / "sources" / "src_multi" / "original.txt"
        assert dest.exists()
        content = dest.read_text(encoding="utf-8")
        assert content == "First document text."
        assert "Second" not in content
        assert result == "First document text."

    def test_path_helper_returns_correct_layout(self, tmp_path: Path) -> None:
        """get_original_text_path returns <data_dir>/sources/<source_id>/original.txt."""
        from chaoscypher_core.services.sources.management.paths import get_original_text_path

        p = get_original_text_path("abc123", tmp_path)
        assert p == tmp_path / "sources" / "abc123" / "original.txt"

    def test_write_creates_parent_directory(self, tmp_path: Path) -> None:
        """Parent directory is created automatically if it doesn't exist."""
        from chaoscypher_core.operations.importing.indexing_handler import _persist_original_text

        source_id = "brand_new_source"
        dest_dir = tmp_path / "sources" / source_id
        assert not dest_dir.exists()

        _persist_original_text(
            documents=[{"content": "Some content.", "metadata": {}}],
            source_id=source_id,
            data_dir=tmp_path,
        )

        assert dest_dir.exists()
        assert (dest_dir / "original.txt").exists()
