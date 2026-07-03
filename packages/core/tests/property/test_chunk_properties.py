# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Property-based tests for pure helpers in ``chaoscypher_core.utils.chunk``.

We deliberately skip the full ``ChunkingService.create_chunks`` async path
because it depends on LangChain text splitters + embeddings, which are
heavy to mock for property tests. Instead we cover:

1. ``ChunkingService._sanitize_text`` — BOM strip, CRLF/CR normalisation,
   blank-line collapse. Idempotent on its own output.
2. ``ChunkingService._protect_quoted_text`` /
   ``ChunkingService._restore_quoted_text`` — round-trip preserves the
   original string and never changes its length.
3. ``build_pdf_location_index`` — invariants on offsets covering the
   joined per-page text.
4. ``merge_location_indexes`` — invariants on offsets covering joined
   per-document content.
5. ``_recompute_chunk_offsets`` — exact-match path always anchors to the
   real substring position when the chunk content appears verbatim in
   the original text, and leaves ``sentence_offsets`` untouched (they are
   chunk-local, relative to ``content``).

The second cluster of tests at the bottom of this file is a 2026-05-19
mutmut survivor-kill pass: 60 surviving mutants in chunk.py boiled down
to ~20 real test gaps (the rest were equivalent or log-only). Each new
test below pins a specific contract that one or more mutations broke.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from chaoscypher_core.utils.chunk import (
    ChunkingService,
    LocationBoundary,
    _recompute_chunk_offsets,
    build_pdf_location_index,
    merge_location_indexes,
)


# --- strategies ------------------------------------------------------------

# Plain text the sanitiser / protector helpers should handle. We allow
# control characters because that's exactly what sanitiser is supposed to
# normalise. We exclude two categories:
#   * surrogates (Cs, \ud800-\udfff) — UTF-8 strict refuses them and they
#     would fail re-encoding round-trips;
#   * private-use (Co, e.g. U+E000-U+F8FF) — ``_protect_quoted_text`` reserves
#     these code points as internal sentinel placeholders (_PERIOD_PLACEHOLDER
#     etc. in chunk.py). By documented contract source text must not contain
#     them: the substitution is length-preserving and therefore non-injective
#     over an alphabet that includes its own sentinels (a bare U+E000 in the
#     input is indistinguishable from an inserted placeholder on restore).
_text_strategy = st.text(
    alphabet=st.characters(
        blacklist_categories=["Cs", "Co"],  # surrogates + private-use sentinels
    ),
    min_size=0,
    max_size=2000,
)

# Per-page text for ``build_pdf_location_index`` — keep length manageable.
_page_text = st.text(min_size=0, max_size=200)
_pages_strategy = st.lists(_page_text, min_size=0, max_size=20)


# --- 1. _sanitize_text ------------------------------------------------------


@given(text=_text_strategy)
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_sanitize_text_is_idempotent(text: str) -> None:
    """``_sanitize_text(_sanitize_text(x)) == _sanitize_text(x)`` for all x.

    The sanitiser normalises BOMs / CR / CRLF / blank-line bursts; once
    applied, a second application must be a no-op.
    """
    once = ChunkingService._sanitize_text(text)
    twice = ChunkingService._sanitize_text(once)
    assert once == twice


@given(text=_text_strategy)
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_sanitize_text_strips_leading_bom(text: str) -> None:
    """A leading BOM is always stripped from the result."""
    payload = "﻿" + text
    cleaned = ChunkingService._sanitize_text(payload)
    # The sanitiser only strips leading BOM / ZWNBSP characters; embedded
    # ones elsewhere in ``text`` are not its concern.
    assert not cleaned.startswith("﻿")
    assert not cleaned.startswith("￾")


@given(text=_text_strategy)
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_sanitize_text_normalises_crlf_to_lf(text: str) -> None:
    """After sanitising, the output contains no CR characters."""
    crlf_variant = text.replace("\n", "\r\n")
    cleaned = ChunkingService._sanitize_text(crlf_variant)
    assert "\r" not in cleaned


@given(text=_text_strategy)
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_sanitize_text_collapses_blank_line_bursts(text: str) -> None:
    """After sanitising, no run of 3+ consecutive newlines remains."""
    cleaned = ChunkingService._sanitize_text(text)
    assert "\n\n\n" not in cleaned


# --- 2. protect / restore round-trip ---------------------------------------


@given(text=_text_strategy)
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_protect_restore_quoted_text_round_trip(text: str) -> None:
    """``restore(protect(x))`` recovers x verbatim and preserves length."""
    protected = ChunkingService._protect_quoted_text(text)
    restored = ChunkingService._restore_quoted_text(protected)
    # The placeholder substitution is character-for-character, so length
    # MUST be preserved at every step of the cycle.
    assert len(protected) == len(text)
    assert len(restored) == len(text)
    assert restored == text


# --- 3. build_pdf_location_index -------------------------------------------


@given(pages=_pages_strategy, separator=st.sampled_from(["\n\n", "\n", " "]))
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_build_pdf_location_index_covers_joined_text(pages: list[str], separator: str) -> None:
    """Each index entry maps to the exact char range of its page in joined text.

    Invariants:
    - The index has one entry per page.
    - ``page_number`` is 1-based and dense (1, 2, 3, ...).
    - For each entry, ``joined_text[start_char:end_char] == pages[i]``.
    - Entries are non-overlapping and sorted.
    """
    index = build_pdf_location_index(pages, separator=separator)
    assert len(index) == len(pages)

    joined = separator.join(pages)
    prev_end = -1
    for i, entry in enumerate(index):
        assert entry["page_number"] == i + 1
        assert entry["section"] is None
        # Range matches the page slice.
        assert joined[entry["start_char"] : entry["end_char"]] == pages[i]
        # Non-overlapping & monotonic.
        assert entry["start_char"] >= prev_end
        assert entry["end_char"] >= entry["start_char"]
        prev_end = entry["end_char"]


def test_build_pdf_location_index_empty_pages() -> None:
    """An empty page list yields an empty index."""
    assert build_pdf_location_index([]) == []


# --- 4. merge_location_indexes ---------------------------------------------


@given(
    contents=st.lists(st.text(min_size=0, max_size=100), min_size=0, max_size=10),
    separator=st.sampled_from(["\n\n", "\n", " "]),
)
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_merge_location_indexes_with_one_index_per_doc(contents: list[str], separator: str) -> None:
    """Merging single-entry indexes preserves offsets relative to joined text.

    For each document we build a trivial one-entry index covering the
    whole content. After merging, each entry's range must still slice
    back to its original content from the joined output.
    """
    docs_with_indexes: list[tuple[str, list[LocationBoundary] | None]] = []
    for i, content in enumerate(contents):
        index: list[LocationBoundary] = [
            LocationBoundary(
                start_char=0,
                end_char=len(content),
                page_number=i + 1,
                section=None,
            )
        ]
        docs_with_indexes.append((content, index))

    merged = merge_location_indexes(docs_with_indexes, separator=separator)
    joined = separator.join(contents)

    # One merged entry per non-empty input index.
    assert len(merged) == len(contents)
    for entry, expected_content in zip(merged, contents, strict=True):
        assert joined[entry["start_char"] : entry["end_char"]] == expected_content


def test_merge_location_indexes_empty_input() -> None:
    """Empty input yields an empty merged index."""
    assert merge_location_indexes([]) == []


@given(
    contents=st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=5),
    separator=st.sampled_from(["\n\n", "\n"]),
)
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_merge_location_indexes_skips_missing_indexes_but_advances_offset(
    contents: list[str], separator: str
) -> None:
    """Docs without indexes contribute no entries but still shift later offsets."""
    # Only the LAST document carries an index; all earlier docs are
    # index-less but their content still pushes the offset forward.
    docs_with_indexes: list[tuple[str, list[LocationBoundary] | None]] = [
        (c, None) for c in contents[:-1]
    ]
    last = contents[-1]
    docs_with_indexes.append(
        (
            last,
            [
                LocationBoundary(
                    start_char=0,
                    end_char=len(last),
                    page_number=1,
                    section=None,
                )
            ],
        )
    )

    merged = merge_location_indexes(docs_with_indexes, separator=separator)
    assert len(merged) == 1
    joined = separator.join(contents)
    entry = merged[0]
    assert joined[entry["start_char"] : entry["end_char"]] == last


# --- 5. _recompute_chunk_offsets exact path --------------------------------


@given(
    prefix=st.text(min_size=0, max_size=200),
    middle=st.text(min_size=1, max_size=200),
    suffix=st.text(min_size=0, max_size=200),
)
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_recompute_chunk_offsets_exact_match_anchors_to_substring(
    prefix: str, middle: str, suffix: str
) -> None:
    """When the chunk content is a verbatim substring of original_text the
    recompute always uses the exact path and sets char_start/char_end to
    the substring's position.

    ``str.find`` returns the FIRST occurrence; we ensure ``middle`` does
    not also appear in ``prefix`` so the test is deterministic. We do
    this by re-deriving the expected index with ``original.find(middle)``
    rather than asserting on ``len(prefix)``.
    """
    original = prefix + middle + suffix
    chunk: dict[str, Any] = {
        "content": middle,
        "char_start": 0,
        "char_end": len(middle),
        "chunk_metadata": {"sentence_offsets": []},
    }
    _recompute_chunk_offsets([chunk], original)

    assert chunk["citation_offset_method"] == "exact"
    expected_idx = original.find(middle)
    assert chunk["char_start"] == expected_idx
    assert chunk["char_end"] == expected_idx + len(middle)
    # And the slice round-trips.
    assert original[chunk["char_start"] : chunk["char_end"]] == middle


@given(
    chunk_content=st.text(min_size=1, max_size=100),
    other=st.text(min_size=0, max_size=200),
)
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_recompute_chunk_offsets_no_match_yields_none(chunk_content: str, other: str) -> None:
    """When the chunk content cannot be located, char_start/char_end are
    None and the method is either 'fuzzy' (if rapidfuzz crossed the
    threshold) or 'none'. We pin the looser invariant: method is one of
    the three expected values and char_start/char_end are mutually
    consistent (both None or both ints).
    """
    # Build an "original" text we know does NOT contain chunk_content.
    original = other.replace(chunk_content, "")
    chunk: dict[str, Any] = {
        "content": chunk_content,
        "char_start": 0,
        "char_end": len(chunk_content),
        "chunk_metadata": {"sentence_offsets": []},
    }
    _recompute_chunk_offsets([chunk], original)

    assert chunk["citation_offset_method"] in {"exact", "fuzzy", "none"}
    start = chunk["char_start"]
    end = chunk["char_end"]
    # Either both None (no-match path) or both ints (fuzzy match found).
    assert (start is None) == (end is None)
    if start is not None and end is not None:
        assert start >= 0
        assert end >= start
        assert end <= len(original)


# ---------------------------------------------------------------------------
# 6. merge_location_indexes — key contract on per-entry dicts
#
# mutmut survivors:
#   _merge_location_indexes__mutmut_17 / 18 — "page_number" key renamed
#   _merge_location_indexes__mutmut_21 / 22 — "section" key renamed
# The existing tests only sliced joined text by start_char/end_char and
# never read the page_number / section fields back off the merged entry.
# ---------------------------------------------------------------------------


def test_merge_location_indexes_preserves_page_number_and_section_keys() -> None:
    """Merged entries must use the canonical key names ``page_number`` and
    ``section`` (matching the ``LocationBoundary`` TypedDict). A renamed key
    would silently break the downstream ``_lookup_location`` consumer.
    """
    doc1_content = "Doc one content"
    doc2_content = "Doc two content"
    doc1_index: list[LocationBoundary] = [
        LocationBoundary(
            start_char=0,
            end_char=len(doc1_content),
            page_number=7,
            section="Intro",
        ),
    ]
    doc2_index: list[LocationBoundary] = [
        LocationBoundary(
            start_char=0,
            end_char=len(doc2_content),
            page_number=42,
            section="Conclusion",
        ),
    ]

    merged = merge_location_indexes(
        [(doc1_content, doc1_index), (doc2_content, doc2_index)],
        separator="\n\n",
    )

    assert len(merged) == 2
    for entry in merged:
        # Canonical keys must be present, no others.
        assert set(entry.keys()) == {"start_char", "end_char", "page_number", "section"}
    assert merged[0]["page_number"] == 7
    assert merged[0]["section"] == "Intro"
    assert merged[1]["page_number"] == 42
    assert merged[1]["section"] == "Conclusion"


# ---------------------------------------------------------------------------
# 7. _recompute_chunk_offsets — fuzzy / multi-chunk / counter survivors
# ---------------------------------------------------------------------------


def test_recompute_chunk_offsets_fuzzy_path_anchors_via_alignment() -> None:
    """Chunk whose content differs from original by whitespace normalisation
    still resolves via rapidfuzz.

    Kills mutants:
      _recompute_chunk_offsets__mutmut_3   (fuzzy_count = None init)
      _recompute_chunk_offsets__mutmut_50  (partial_ratio first arg → None)
      _recompute_chunk_offsets__mutmut_51  (partial_ratio second arg → None)
    All three break the fuzzy path so the chunk falls through to 'none'.
    """
    # Original loader text uses paragraph breaks; the cleaner collapsed
    # them to single spaces. The chunk content no longer matches verbatim
    # but partial_ratio scores well above the 80 threshold.
    original = "The quick brown fox jumps\n\nover the lazy dog near the river."
    chunk_content = "The quick brown fox jumps over the lazy dog near the river."
    chunk: dict[str, Any] = {
        "content": chunk_content,
        "char_start": 0,
        "char_end": len(chunk_content),
        "chunk_metadata": {"sentence_offsets": []},
    }
    _recompute_chunk_offsets([chunk], original)

    assert chunk["citation_offset_method"] == "fuzzy"
    assert chunk["char_start"] is not None
    assert chunk["char_end"] is not None
    assert chunk["char_start"] >= 0
    assert chunk["char_end"] <= len(original)


def test_recompute_chunk_offsets_processes_every_chunk_in_list() -> None:
    """``_recompute_chunk_offsets`` iterates over the full chunks list.

    Kills mutant:
      _recompute_chunk_offsets__mutmut_46  ("continue" → "break")
    Pre-mutation the function processes every chunk. Post-mutation it
    exits after the first exact match, leaving subsequent chunks
    untouched (they keep their pre-call char_start/char_end and never
    get a citation_offset_method tag).
    """
    original = "alpha bravo charlie delta echo foxtrot"
    # Three chunks all present verbatim → all should hit exact path.
    chunks: list[dict[str, Any]] = [
        {
            "content": "alpha",
            "char_start": 999,
            "char_end": 999,
            "chunk_metadata": {"sentence_offsets": []},
        },
        {
            "content": "charlie",
            "char_start": 999,
            "char_end": 999,
            "chunk_metadata": {"sentence_offsets": []},
        },
        {
            "content": "foxtrot",
            "char_start": 999,
            "char_end": 999,
            "chunk_metadata": {"sentence_offsets": []},
        },
    ]
    _recompute_chunk_offsets(chunks, original)

    # All three must be re-anchored to the substring position.
    assert chunks[0]["char_start"] == 0
    assert chunks[1]["char_start"] == original.find("charlie")
    assert chunks[2]["char_start"] == original.find("foxtrot")
    for c in chunks:
        assert c["citation_offset_method"] == "exact"


def test_recompute_chunk_offsets_handles_chunk_without_content_key() -> None:
    """A chunk dict missing the ``content`` key must not crash.

    Kills mutants:
      _recompute_chunk_offsets__mutmut_9   (default → None → TypeError on find)
      _recompute_chunk_offsets__mutmut_11  (default removed → None)
      _recompute_chunk_offsets__mutmut_14  (default → "XXXX" → bogus search)
    Original code defaults missing content to ``""`` so ``original.find("")``
    returns 0 (the empty string is found at index 0 of every string) and
    the chunk is tagged ``"exact"`` with char_start=0/char_end=0.
    """
    original = "real content here"
    chunk: dict[str, Any] = {
        # No "content" key at all.
        "char_start": 0,
        "char_end": 0,
        "chunk_metadata": {"sentence_offsets": []},
    }
    _recompute_chunk_offsets([chunk], original)

    # Empty string finds at index 0; method should be 'exact' and offsets 0.
    assert chunk["citation_offset_method"] == "exact"
    assert chunk["char_start"] == 0
    assert chunk["char_end"] == 0


def test_recompute_chunk_offsets_reanchors_char_start_leaves_offsets_local() -> None:
    """Recompute re-anchors ``char_start`` to the real substring position
    while leaving ``sentence_offsets`` untouched.

    ``sentence_offsets`` are chunk-local (0-based, relative to ``content``)
    and every consumer slices ``content`` directly, so the recompute must
    not rewrite them even though it moves ``char_start`` to a different
    document position. Test setup: chunk's pre-call char_start is a stale
    placeholder (100); the content is found verbatim at position 0.
    """
    original = "Sentence one. Sentence two."
    chunk_content = original
    chunk: dict[str, Any] = {
        "content": chunk_content,
        # Stale anchor (e.g. cleaned-text coordinates) — must be replaced.
        "char_start": 100,
        "char_end": 100 + len(chunk_content),
        "chunk_metadata": {
            # Chunk-local offsets, exactly as the producer emits them.
            "sentence_offsets": [
                {"start": 0, "end": 13},
                {"start": 14, "end": 27},
            ],
        },
    }
    _recompute_chunk_offsets([chunk], original)

    # Exact match → char_start re-anchored to the real position (0).
    assert chunk["char_start"] == 0
    assert chunk["citation_offset_method"] == "exact"
    # sentence_offsets stay chunk-local and round-trip against content.
    so = chunk["chunk_metadata"]["sentence_offsets"]
    assert chunk_content[so[0]["start"] : so[0]["end"]] == "Sentence one."
    assert chunk_content[so[1]["start"] : so[1]["end"]] == "Sentence two."


def test_recompute_chunk_offsets_none_path_for_unfindable_content() -> None:
    """When neither exact nor fuzzy can find the chunk, both offsets land
    on ``None`` and the method tag is ``"none"``.

    Implicitly pins the contract that the fuzzy-path threshold gating
    actually runs (i.e. ``_FUZZY_SCORE_THRESHOLD`` is enforced, not
    bypassed by truthy mutations).
    """
    original = "totally unrelated content"
    chunk: dict[str, Any] = {
        "content": "xyzqqq_no_match_anywhere_!@#$%",
        "char_start": 0,
        "char_end": 30,
        "chunk_metadata": {"sentence_offsets": []},
    }
    _recompute_chunk_offsets([chunk], original)

    assert chunk["citation_offset_method"] == "none"
    assert chunk["char_start"] is None
    assert chunk["char_end"] is None


# ---------------------------------------------------------------------------
# Aux: ensure the recompute helper does not raise when handed an empty
# chunks list — covers the loop boundary independently of any single
# mutmut survivor (defence-in-depth).
# ---------------------------------------------------------------------------


def test_recompute_chunk_offsets_empty_chunks_list_is_noop() -> None:
    """Empty chunks input must complete without error."""
    _recompute_chunk_offsets([], "any original text")


@pytest.mark.parametrize(
    "separator",
    ["\n\n", "\n", " "],
)
def test_build_pdf_location_index_single_page_no_trailing_separator(
    separator: str,
) -> None:
    """A single-page input must produce one entry whose end_char equals
    the page length — no trailing separator must be added to the offset.

    The off-by-one guard ``page_idx < len(page_texts) - 1`` is what
    keeps the offset from advancing past the final page. The mutmut
    survivors for this line (build_pdf_location_index_mutmut_21 / 22)
    are equivalent for our current call sites because nothing reads
    ``offset`` after the loop exits; this test pins the observable
    invariant ``end_char == len(only_page)`` regardless.
    """
    only_page = "single page content"
    index = build_pdf_location_index([only_page], separator=separator)
    assert len(index) == 1
    assert index[0]["start_char"] == 0
    assert index[0]["end_char"] == len(only_page)
