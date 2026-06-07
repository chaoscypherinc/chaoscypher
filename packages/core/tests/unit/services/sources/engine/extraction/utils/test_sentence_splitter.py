# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for sentence_splitter module.

Covers split_into_sentences_with_offsets offset accuracy, edge cases,
and consistency with split_into_sentences.
"""

from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
    get_referenced_sentences,
    parse_sent_ref,
    split_into_sentences,
    split_into_sentences_with_offsets,
)


class TestSplitIntoSentencesWithOffsets:
    """Tests for split_into_sentences_with_offsets."""

    def test_basic_offsets(self) -> None:
        """Two simple sentences produce two entries with correct start/end."""
        text = "Hello world. Goodbye world."
        offsets = split_into_sentences_with_offsets(text)

        assert len(offsets) == 2
        assert text[offsets[0]["start"] : offsets[0]["end"]] == "Hello world."
        assert text[offsets[1]["start"] : offsets[1]["end"]] == "Goodbye world."

    def test_offsets_match_content(self) -> None:
        """Verify text[start:end] equals each sentence for multi-sentence text."""
        text = "The cat sat. The dog ran. The bird flew."
        offsets = split_into_sentences_with_offsets(text)
        sentences = split_into_sentences(text)

        assert len(offsets) == len(sentences)
        for offset, sentence in zip(offsets, sentences, strict=True):
            assert text[offset["start"] : offset["end"]] == sentence

    def test_single_sentence(self) -> None:
        """Single sentence returns one entry spanning full text (stripped)."""
        text = "Just one sentence here."
        offsets = split_into_sentences_with_offsets(text)

        assert len(offsets) == 1
        assert offsets[0]["start"] == 0
        assert offsets[0]["end"] == len(text)

    def test_abbreviations_preserved(self) -> None:
        """Abbreviations like 'Dr.' don't cause extra splits."""
        text = "Dr. Smith went home."
        offsets = split_into_sentences_with_offsets(text)

        assert len(offsets) == 1
        assert text[offsets[0]["start"] : offsets[0]["end"]] == "Dr. Smith went home."

    def test_empty_text(self) -> None:
        """Empty string returns a single entry."""
        text = ""
        offsets = split_into_sentences_with_offsets(text)

        assert len(offsets) == 1

    def test_multiple_sentences_offsets_are_non_overlapping(self) -> None:
        """Offsets should not overlap and should be in order."""
        text = "First sentence. Second sentence. Third sentence."
        offsets = split_into_sentences_with_offsets(text)

        assert len(offsets) == 3
        for i in range(len(offsets) - 1):
            assert offsets[i]["end"] <= offsets[i + 1]["start"]

    def test_offsets_cover_all_sentence_text(self) -> None:
        """Every offset range should extract non-empty text."""
        text = "Alpha. Beta. Gamma. Delta."
        offsets = split_into_sentences_with_offsets(text)

        for offset in offsets:
            extracted = text[offset["start"] : offset["end"]]
            assert len(extracted) > 0
            assert extracted.strip() != ""

    def test_quoted_dialogue(self) -> None:
        """Quoted text with internal periods stays as one sentence."""
        text = '"Hello there. How are you?" She asked.'
        offsets = split_into_sentences_with_offsets(text)
        sentences = split_into_sentences(text)

        assert len(offsets) == len(sentences)
        for offset, sentence in zip(offsets, sentences, strict=True):
            assert text[offset["start"] : offset["end"]] == sentence


class TestParseSentRef:
    """Tests for parse_sent_ref.

    The parser returns a sorted, deduplicated list of 1-indexed sentence
    numbers (or ``None`` if the input is unparseable). The list-of-indices
    contract is required because the LLM cites disjoint sets of sentences
    (e.g. ``S1-S4, S9, S11``); a single ``{start, end}`` range cannot
    represent that without silently filling in unreferenced sentences.
    """

    def test_single_reference(self) -> None:
        assert parse_sent_ref("S3") == [3]

    def test_simple_range(self) -> None:
        assert parse_sent_ref("S2-S4") == [2, 3, 4]

    def test_range_without_second_S_prefix(self) -> None:
        assert parse_sent_ref("S3-5") == [3, 4, 5]

    def test_pure_comma_list_does_not_fill_gaps(self) -> None:
        """``S3,S5`` means sentences 3 and 5, NOT 3, 4, and 5.

        Regression: the legacy parser returned ``{start: 3, end: 5}``
        which expanded to ``[3, 4, 5]`` — silently including S4 in the
        evidence window even though the LLM did not cite it.
        """
        assert parse_sent_ref("S3,S5") == [3, 5]

    def test_mixed_range_and_singletons(self) -> None:
        """``S1-S4, S9, S11, S24-S28, S31`` produces the union.

        Regression: the legacy parser had two regexes (single-range
        and pure-comma-list) and rejected anything mixing both, so
        every entity citing this format was dropped by the evidence
        filter.
        """
        result = parse_sent_ref("S1-S4, S9, S11, S24-S28, S31")
        assert result == [1, 2, 3, 4, 9, 11, 24, 25, 26, 27, 28, 31]

    def test_unsorted_input_returns_sorted(self) -> None:
        assert parse_sent_ref("S5, S2-S4, S2") == [2, 3, 4, 5]

    def test_whitespace_around_separators_is_tolerated(self) -> None:
        assert parse_sent_ref("S1 ,  S2-S3 , S5") == [1, 2, 3, 5]

    def test_empty_string_returns_none(self) -> None:
        assert parse_sent_ref("") is None

    def test_garbage_returns_none(self) -> None:
        assert parse_sent_ref("not a ref") is None

    def test_zero_index_rejected(self) -> None:
        """Sentences are 1-indexed; ``S0`` is malformed."""
        assert parse_sent_ref("S0") is None

    def test_inverted_range_rejected(self) -> None:
        assert parse_sent_ref("S5-S2") is None

    def test_semicolon_separator_accepted(self) -> None:
        """The LLM occasionally emits ``S1;S15`` instead of ``S1, S15``.

        The prompt teaches ``;`` as the alias delimiter and the model
        generalizes that convention to sent_ref lists. Treating ``;`` as
        equivalent to ``,`` here turns a previously-dropped entity into a
        salvaged one — ``;`` has no other valid meaning inside a sent_ref.
        """
        assert parse_sent_ref("S1;S15") == [1, 15]

    def test_mixed_semicolon_and_comma_separators(self) -> None:
        """Mixed ``;`` and ``,`` separators within one ref still parse."""
        assert parse_sent_ref("S1;S5,S7") == [1, 5, 7]

    def test_semicolon_with_range(self) -> None:
        assert parse_sent_ref("S1-S3;S7") == [1, 2, 3, 7]


class TestGetReferencedSentences:
    """Tests for get_referenced_sentences.

    Returns the union of sentences cited by ``sent_ref`` — exactly those
    sentences, no implicit fill-in. Returns ``None`` if any cited index
    is out of bounds or the ref is unparseable.
    """

    @staticmethod
    def _sample(n: int = 6) -> list[str]:
        return [f"sentence_{i}" for i in range(1, n + 1)]

    def test_single_reference(self) -> None:
        sentences = self._sample()
        assert get_referenced_sentences("S2", sentences) == ["sentence_2"]

    def test_range(self) -> None:
        sentences = self._sample()
        assert get_referenced_sentences("S2-S4", sentences) == [
            "sentence_2",
            "sentence_3",
            "sentence_4",
        ]

    def test_pure_comma_list_does_not_fill_gaps(self) -> None:
        """Regression: ``S3,S5`` previously returned [s3, s4, s5]."""
        sentences = self._sample()
        assert get_referenced_sentences("S3,S5", sentences) == [
            "sentence_3",
            "sentence_5",
        ]

    def test_mixed_range_and_singletons(self) -> None:
        """``S1-S2, S5`` → [s1, s2, s5] (no s3, no s4)."""
        sentences = self._sample()
        assert get_referenced_sentences("S1-S2, S5", sentences) == [
            "sentence_1",
            "sentence_2",
            "sentence_5",
        ]

    def test_out_of_bounds_returns_none(self) -> None:
        sentences = self._sample(3)
        assert get_referenced_sentences("S5", sentences) is None

    def test_partially_out_of_bounds_returns_none(self) -> None:
        """If any cited sentence is missing the whole ref is invalid."""
        sentences = self._sample(3)
        assert get_referenced_sentences("S1, S5", sentences) is None

    def test_unparseable_returns_none(self) -> None:
        sentences = self._sample()
        assert get_referenced_sentences("garbage", sentences) is None
