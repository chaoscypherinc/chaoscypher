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
   the original text.
6. ``_shift_sentence_offsets`` — delta-shift correctness, key-name
   contract, and graceful no-op when metadata / sentence_offsets are
   missing.

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
    _shift_sentence_offsets,
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


def test_recompute_chunk_offsets_uses_canonical_char_start_key() -> None:
    """``old_start`` is read from the canonical key ``char_start``.

    Kills mutants:
      _recompute_chunk_offsets__mutmut_15  (old_start hardcoded to None)
      _recompute_chunk_offsets__mutmut_16  (chunk.get(None))
      _recompute_chunk_offsets__mutmut_17  (key renamed to XXchar_startXX)
      _recompute_chunk_offsets__mutmut_18  (key renamed to CHAR_START)
      _recompute_chunk_offsets__mutmut_38  (passes None instead of old_start
                                           to _shift_sentence_offsets)

    Test setup: chunk's pre-call char_start is 100 (placeholder), real
    substring position is 0, sentence_offsets is non-empty. The shift
    delta is therefore (0 - 100) = -100; sentence_offsets must reflect
    that shift. Any mutation that nukes old_start to None makes the
    shift a no-op and the sentence_offsets stay at their original values.
    """
    original = "Sentence one. Sentence two."
    chunk_content = original
    chunk: dict[str, Any] = {
        "content": chunk_content,
        # Old anchor was at position 100 (e.g. cleaned-text coordinates).
        "char_start": 100,
        "char_end": 100 + len(chunk_content),
        "chunk_metadata": {
            "sentence_offsets": [
                {"start": 100, "end": 114},
                {"start": 114, "end": 127},
            ],
        },
    }
    _recompute_chunk_offsets([chunk], original)

    # Exact match → new char_start is 0; delta = 0 - 100 = -100.
    assert chunk["char_start"] == 0
    assert chunk["citation_offset_method"] == "exact"
    shifted = chunk["chunk_metadata"]["sentence_offsets"]
    assert shifted[0]["start"] == 0, (
        f"sentence_offsets must shift by delta=-100; got {shifted}. "
        f"This kills mutations that nuke old_start or the shift call."
    )
    assert shifted[0]["end"] == 14
    assert shifted[1]["start"] == 14
    assert shifted[1]["end"] == 27


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
# 8. _shift_sentence_offsets — direct unit coverage
#
# Tests the helper in isolation so the mutmut survivors that live inside
# it (19 mutants spanning early-return guards, key lookups, delta math,
# and assignment) get killed without needing the surrounding recompute
# context.
# ---------------------------------------------------------------------------


def _shifted_offsets(chunk: dict[str, Any]) -> list[dict[str, int]]:
    """Read sentence_offsets back off a chunk after a shift call."""
    return chunk["chunk_metadata"]["sentence_offsets"]


def test_shift_sentence_offsets_applies_positive_delta() -> None:
    """Delta > 0 shifts every sentence start/end forward by delta.

    Kills mutants:
      __shift_sentence_offsets__mutmut_1   (old_start None guard inverted)
      __shift_sentence_offsets__mutmut_12  (delta = None)
      __shift_sentence_offsets__mutmut_13  (delta uses + instead of -)
      __shift_sentence_offsets__mutmut_14  (delta == 0 → != 0; early return)
      __shift_sentence_offsets__mutmut_15  (delta == 0 → == 1; misses delta=0)
      __shift_sentence_offsets__mutmut_16  (shifted = None → AttributeError)
      __shift_sentence_offsets__mutmut_17  (meta["sentence_offsets"] = None)
      __shift_sentence_offsets__mutmut_18/19  (renamed write-back key)
    """
    chunk: dict[str, Any] = {
        "chunk_metadata": {
            "sentence_offsets": [
                {"start": 10, "end": 20},
                {"start": 20, "end": 35},
            ],
        }
    }
    _shift_sentence_offsets(chunk, old_start=10, new_start=110)
    out = _shifted_offsets(chunk)
    # delta = 110 - 10 = 100.
    assert out == [
        {"start": 110, "end": 120},
        {"start": 120, "end": 135},
    ]


def test_shift_sentence_offsets_applies_negative_delta() -> None:
    """Delta < 0 shifts every sentence start/end backward by |delta|.

    Pins the subtraction direction (mut_13 swaps - for +).
    """
    chunk: dict[str, Any] = {
        "chunk_metadata": {
            "sentence_offsets": [
                {"start": 200, "end": 215},
                {"start": 215, "end": 230},
            ],
        }
    }
    _shift_sentence_offsets(chunk, old_start=200, new_start=50)
    out = _shifted_offsets(chunk)
    # delta = 50 - 200 = -150.
    assert out == [
        {"start": 50, "end": 65},
        {"start": 65, "end": 80},
    ]


def test_shift_sentence_offsets_noop_when_delta_zero() -> None:
    """Delta == 0 short-circuits and leaves sentence_offsets unchanged.

    Pins the early-return on delta==0 (mut_14 inverts the guard so
    delta=0 falls through into the rebuild loop, which would still
    produce the same data — making this a soft kill — but mut_15
    changes the literal to 1, which keeps the early-return for true
    zero-deltas (covered) but breaks delta=1 (covered by the positive
    test above).
    """
    original_offsets = [{"start": 5, "end": 10}, {"start": 10, "end": 18}]
    chunk: dict[str, Any] = {
        "chunk_metadata": {"sentence_offsets": list(original_offsets)},
    }
    _shift_sentence_offsets(chunk, old_start=42, new_start=42)
    assert _shifted_offsets(chunk) == original_offsets


def test_shift_sentence_offsets_noop_when_old_start_is_none() -> None:
    """``old_start=None`` is the 'no previous anchor' signal — the helper
    must return immediately without mutating sentence_offsets.

    Kills mutant:
      __shift_sentence_offsets__mutmut_1  (inverts the None guard so the
      function early-returns only when old_start IS set, leaving the
      None case to fall through and attempt ``None - new_start`` which
      raises TypeError).
    """
    original_offsets = [{"start": 5, "end": 10}]
    chunk: dict[str, Any] = {
        "chunk_metadata": {"sentence_offsets": list(original_offsets)},
    }
    _shift_sentence_offsets(chunk, old_start=None, new_start=42)
    assert _shifted_offsets(chunk) == original_offsets


def test_shift_sentence_offsets_noop_when_chunk_metadata_missing() -> None:
    """Silent no-op when ``chunk_metadata`` key is missing.

    Kills mutants:
      __shift_sentence_offsets__mutmut_2   (meta = None)
      __shift_sentence_offsets__mutmut_3   (chunk.get(None))
      __shift_sentence_offsets__mutmut_4/5 (renamed key lookup)
      __shift_sentence_offsets__mutmut_6   (inverted isinstance guard)

    Without the guard chain working, accessing ``meta.get(...)`` on None
    would raise AttributeError.
    """
    chunk: dict[str, Any] = {}  # no chunk_metadata key
    # Must not raise.
    _shift_sentence_offsets(chunk, old_start=10, new_start=20)
    assert chunk == {}


def test_shift_sentence_offsets_noop_when_chunk_metadata_not_dict() -> None:
    """``chunk_metadata`` that isn't a dict → guard returns early.

    Pins the ``isinstance(meta, dict)`` check (mut_6 inverts it; a
    non-dict meta would then proceed and raise AttributeError on
    ``.get("sentence_offsets")``).
    """
    chunk: dict[str, Any] = {"chunk_metadata": "not a dict"}
    _shift_sentence_offsets(chunk, old_start=10, new_start=20)
    assert chunk == {"chunk_metadata": "not a dict"}


def test_shift_sentence_offsets_noop_when_sentence_offsets_missing() -> None:
    """``chunk_metadata`` present but no ``sentence_offsets`` key → no-op.

    Kills mutants:
      __shift_sentence_offsets__mutmut_7  (sentence_offsets = None)
      __shift_sentence_offsets__mutmut_8  (meta.get(None))
      __shift_sentence_offsets__mutmut_9/10  (renamed key lookup)
      __shift_sentence_offsets__mutmut_11  (inverted isinstance(list) guard)
    """
    chunk: dict[str, Any] = {"chunk_metadata": {"other": "value"}}
    _shift_sentence_offsets(chunk, old_start=10, new_start=20)
    assert chunk == {"chunk_metadata": {"other": "value"}}


def test_shift_sentence_offsets_noop_when_sentence_offsets_not_list() -> None:
    """``sentence_offsets`` of the wrong type → guard returns early.

    Pins the ``isinstance(sentence_offsets, list)`` check (mut_11
    inverts it; a non-list would then proceed to ``for so in ...``
    and either fail or iterate incorrectly).
    """
    chunk: dict[str, Any] = {"chunk_metadata": {"sentence_offsets": "not a list"}}
    _shift_sentence_offsets(chunk, old_start=10, new_start=20)
    assert chunk == {"chunk_metadata": {"sentence_offsets": "not a list"}}


def test_shift_sentence_offsets_writes_back_under_canonical_key() -> None:
    """The shifted list must land back under the canonical
    ``sentence_offsets`` key (not a renamed variant).

    Kills mutants:
      __shift_sentence_offsets__mutmut_18  ("XXsentence_offsetsXX" key)
      __shift_sentence_offsets__mutmut_19  ("SENTENCE_OFFSETS" key)
    """
    chunk: dict[str, Any] = {
        "chunk_metadata": {
            "sentence_offsets": [{"start": 0, "end": 5}],
        }
    }
    _shift_sentence_offsets(chunk, old_start=0, new_start=10)
    meta = chunk["chunk_metadata"]
    # Original key must still exist, hold the shifted data, and no
    # alternate-cased variant must have been introduced.
    assert "sentence_offsets" in meta
    assert "XXsentence_offsetsXX" not in meta
    assert "SENTENCE_OFFSETS" not in meta
    assert meta["sentence_offsets"] == [{"start": 10, "end": 15}]


def test_shift_sentence_offsets_preserves_non_dict_entries() -> None:
    """Non-dict entries in sentence_offsets pass through unchanged.

    This is a contract test for the fall-through branch
    (``shifted.append(so)``) — it pins that the iteration handles
    mixed payloads without raising. Pre-mutation: the helper returns
    a list with non-dict entries in the same positions. Post-mutation
    (mut_16 sets ``shifted = None``): ``shifted.append(so)`` raises
    AttributeError.
    """
    sentinel = "not-a-dict-just-a-string"
    chunk: dict[str, Any] = {
        "chunk_metadata": {
            "sentence_offsets": [
                {"start": 0, "end": 5},
                sentinel,
                {"start": 5, "end": 10},
            ],
        }
    }
    _shift_sentence_offsets(chunk, old_start=0, new_start=100)
    out = _shifted_offsets(chunk)
    assert out[0] == {"start": 100, "end": 105}
    assert out[1] == sentinel
    assert out[2] == {"start": 105, "end": 110}


# ---------------------------------------------------------------------------
# Hypothesis property: arbitrary positive / negative deltas commute with
# the inverse shift — round-trips back to the original offsets. Covers the
# delta-math survivors with broad input coverage.
# ---------------------------------------------------------------------------


@given(
    starts=st.lists(
        st.integers(min_value=0, max_value=10_000),
        min_size=1,
        max_size=10,
    ),
    delta=st.integers(min_value=-5_000, max_value=5_000),
)
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_shift_sentence_offsets_round_trips(starts: list[int], delta: int) -> None:
    """Applying delta then -delta returns the original offsets."""
    sentence_offsets = [{"start": s, "end": s + 5} for s in starts]
    chunk: dict[str, Any] = {
        "chunk_metadata": {"sentence_offsets": list(sentence_offsets)},
    }
    # First shift by delta (old_start=0, new_start=delta).
    _shift_sentence_offsets(chunk, old_start=0, new_start=delta)
    if delta == 0:
        # No-op path: list is the same object/values.
        assert _shifted_offsets(chunk) == sentence_offsets
        return
    shifted_once = [dict(d) for d in _shifted_offsets(chunk)]
    # Now shift back: old_start=delta, new_start=0 → delta_back = -delta.
    _shift_sentence_offsets(chunk, old_start=delta, new_start=0)
    after_round_trip = _shifted_offsets(chunk)
    assert after_round_trip == sentence_offsets
    # And sanity: the intermediate state really was shifted by delta.
    for original_so, intermediate in zip(sentence_offsets, shifted_once, strict=True):
        assert intermediate["start"] == original_so["start"] + delta
        assert intermediate["end"] == original_so["end"] + delta


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
