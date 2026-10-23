# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for sentence_splitter module.

Covers split_into_sentences_with_offsets offset accuracy, edge cases,
and consistency with split_into_sentences.
"""

from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
    GROUP_TEXT_SEPARATOR,
    evidence_snippet,
    merge_evidence,
    parse_sent_ref,
    resolve_evidence,
    sentences_at,
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


_QUOTE_CASES = [
    # (text, expected sentences)
    (
        "\u201cYes.\u201d\n\n\u201cNo,\u201d said he.",
        ["\u201cYes.\u201d", "\u201cNo,\u201d said he."],
    ),
    ('"Go!"\n\nHe went.', ['"Go!"', "He went."]),
    ("\u201cYes.\u201d \u201cNo.\u201d", ["\u201cYes.\u201d", "\u201cNo.\u201d"]),
    (
        "\u201cShe said, \u2018Come.\u2019\u201d\n\nHe laughed.",
        ["\u201cShe said, \u2018Come.\u2019\u201d", "He laughed."],
    ),
    ("He said \u2018Fine.\u2019\nShe nodded.", ["He said \u2018Fine.\u2019", "She nodded."]),
    # A quote closing without sentence punctuation never ends the sentence.
    ("\u201cNo,\u201d said he. Then he left.", ["\u201cNo,\u201d said he.", "Then he left."]),
    # An attribution on the same line stays with its speech.
    ("\u201cIs it?\u201d Pierre asked.", ["\u201cIs it?\u201d Pierre asked."]),
    # Abbreviations still hold, after a quote too.
    (
        "\u201cWell?\u201d\n\nDr. Lorrain and Mlle. Sch\u00e9rer came in.",
        ["\u201cWell?\u201d", "Dr. Lorrain and Mlle. Sch\u00e9rer came in."],
    ),
]


class TestClosingQuoteBoundary:
    """A sentence ends after ``.!?`` and a closing quote (audit C2).

    Before the fix the boundary needed the punctuation right before the
    whitespace, so a quoted turn ran into the next paragraph: 136 of the
    Book One pack's 779 sentences crossed a paragraph break.
    """

    def test_the_audit_case_is_two_sentences(self) -> None:
        """``\u201cYes.\u201d`` then a new paragraph is a sentence of its own."""
        assert len(split_into_sentences("\u201cYes.\u201d\n\n\u201cNo,\u201d said he.")) == 2

    def test_each_quote_case(self) -> None:
        """Splits after a closing quote; keeps mid-sentence quotes and attributions joined."""
        for text, expected in _QUOTE_CASES:
            assert split_into_sentences(text) == expected, text

    def test_offsets_slice_to_the_same_sentences(self) -> None:
        """``sentence_offsets`` and ``split_into_sentences`` agree on every quote case."""
        for text, expected in _QUOTE_CASES:
            offsets = split_into_sentences_with_offsets(text)
            assert [text[o["start"] : o["end"]] for o in offsets] == expected, text

    def test_no_turn_runs_into_the_next_paragraph(self) -> None:
        """Two speakers' paragraphs never share a sentence."""
        text = (
            "\u201cAnd what about the Emperor?\u201d\n\n"
            "\u201cHe is well, thank God.\u201d\n\n"
            "\u201cI am glad!\u201d said Anna P\u00e1vlovna."
        )
        sentences = split_into_sentences(text)
        assert len(sentences) == 3
        assert all("\n\n" not in s for s in sentences)


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


class TestSentencesAt:
    """Tests for sentences_at over parse_sent_ref's indices (the citation reading).

    Exactly the cited sentences, no implicit fill-in; ``None`` when any cited
    index is out of bounds or there is no index.
    """

    @staticmethod
    def _sample(n: int = 6) -> list[str]:
        return [f"sentence_{i}" for i in range(1, n + 1)]

    @staticmethod
    def _cited(sent_ref: str, sentences: list[str]) -> list[str] | None:
        return sentences_at(parse_sent_ref(sent_ref) or [], sentences)

    def test_single_reference(self) -> None:
        assert self._cited("S2", self._sample()) == ["sentence_2"]

    def test_range(self) -> None:
        assert self._cited("S2-S4", self._sample()) == [
            "sentence_2",
            "sentence_3",
            "sentence_4",
        ]

    def test_pure_comma_list_does_not_fill_gaps(self) -> None:
        """Regression: ``S3,S5`` previously returned [s3, s4, s5]."""
        assert self._cited("S3,S5", self._sample()) == ["sentence_3", "sentence_5"]

    def test_mixed_range_and_singletons(self) -> None:
        """``S1-S2, S5`` → [s1, s2, s5] (no s3, no s4)."""
        assert self._cited("S1-S2, S5", self._sample()) == [
            "sentence_1",
            "sentence_2",
            "sentence_5",
        ]

    def test_out_of_bounds_returns_none(self) -> None:
        assert self._cited("S5", self._sample(3)) is None

    def test_partially_out_of_bounds_returns_none(self) -> None:
        """If any cited sentence is missing the whole ref is invalid."""
        assert self._cited("S1, S5", self._sample(3)) is None

    def test_no_index_returns_none(self) -> None:
        assert parse_sent_ref("garbage") is None
        assert sentences_at([], self._sample()) is None


def _group(*contents: str) -> tuple[list[dict[str, str]], list[str]]:
    """Return small chunks ``c0..cN`` and the sentences of their joined group text."""
    chunks = [{"id": f"c{i}", "content": text} for i, text in enumerate(contents)]
    return chunks, split_into_sentences(GROUP_TEXT_SEPARATOR.join(contents))


class TestResolveEvidence:
    """``resolve_evidence`` maps a group ``sent_ref`` to sentences and stored chunks."""

    def test_places_each_sentence_in_its_chunk_with_local_index(self) -> None:
        """S2 is the first sentence of chunk 1; S3 its second (group numbering)."""
        chunks, sentences = _group("Anna met Pierre.", "Pierre left. Anna stayed.")
        evidence = resolve_evidence("S1, S3", sentences, chunks)
        assert evidence == [
            {"text": "Anna met Pierre.", "chunk_id": "c0", "local_index": 0},
            {"text": "Anna stayed.", "chunk_id": "c1", "local_index": 1},
        ]

    def test_repeated_sentence_resolves_to_the_cited_instance(self) -> None:
        """Overlap duplicates: the offset of the cited S-number picks the chunk."""
        chunks, sentences = _group("He waited. She came.", "She came. They spoke.")
        assert sentences[1] == sentences[2] == "She came."
        assert resolve_evidence("S2", sentences, chunks)[0]["chunk_id"] == "c0"
        assert resolve_evidence("S3", sentences, chunks)[0]["chunk_id"] == "c1"

    def test_stripped_text_falls_back_to_first_containing_chunk(self) -> None:
        """Numbered text differing from the stored chunks falls back to text search.

        The first chunk containing the sentence wins (overlap duplicates).
        """
        chunks = [
            {"id": "c0", "content": "Page 4\nShe came. They spoke."},
            {"id": "c1", "content": "They spoke. Night fell."},
        ]
        # The model saw the chunks with the page header stripped.
        sentences = split_into_sentences("She came. They spoke.\n\nThey spoke. Night fell.")
        evidence = resolve_evidence("S2, S4", sentences, chunks)
        assert [(e["text"], e["chunk_id"]) for e in evidence] == [
            ("They spoke.", "c0"),
            ("Night fell.", "c1"),
        ]
        assert evidence[1]["local_index"] == 1

    def test_out_of_range_refs_are_skipped(self) -> None:
        """A ref past the group's last sentence is dropped, the rest kept."""
        chunks, sentences = _group("One here. Two here.")
        evidence = resolve_evidence("S2, S9", sentences, chunks)
        assert [e["text"] for e in evidence] == ["Two here."]
        assert resolve_evidence("S9", sentences, chunks) == []
        assert resolve_evidence("garbage", sentences, chunks) == []
        assert resolve_evidence(None, sentences, chunks) == []

    def test_missing_chunk_falls_back_to_text_only(self) -> None:
        """A sentence whose chunk is not supplied keeps its text, no chunk."""
        _chunks, sentences = _group("Anna met Pierre.", "Pierre left.")
        evidence = resolve_evidence("S2", sentences, [{"id": "c0", "content": "Anna met Pierre."}])
        assert evidence == [{"text": "Pierre left.", "chunk_id": None, "local_index": None}]
        assert resolve_evidence("S1", sentences, [])[0]["chunk_id"] is None

    def test_stored_sentence_offsets_drive_the_local_index(self) -> None:
        """``chunk_metadata.sentence_offsets`` is used when present."""
        content = "Anna met Pierre. Pierre left."
        chunk = {
            "id": "c0",
            "content": content,
            "chunk_metadata": {"sentence_offsets": split_into_sentences_with_offsets(content)},
        }
        evidence = resolve_evidence("S2", split_into_sentences(content), [chunk])
        assert evidence[0]["local_index"] == 1


class TestEvidenceHelpers:
    """``evidence_snippet`` and ``merge_evidence``."""

    def test_snippet_keeps_the_citation_chunk_sentences(self) -> None:
        evidence = [
            {"text": "A.", "chunk_id": "c0", "local_index": 0},
            {"text": "B.", "chunk_id": "c1", "local_index": 0},
            {"text": "C.", "chunk_id": "c0", "local_index": 1},
        ]
        assert evidence_snippet(evidence) == "A. B. C."
        assert evidence_snippet(evidence, "c0") == "A. C."
        assert evidence_snippet([{"text": "D.", "chunk_id": None}], "c9") == "D."
        assert evidence_snippet([], "c0") is None

    def test_merge_is_an_ordered_union(self) -> None:
        a = [{"text": "A.", "chunk_id": "c0", "local_index": 0}]
        b = [{"text": "A.", "chunk_id": "c0", "local_index": 0}, {"text": "B.", "chunk_id": "c1"}]
        assert [e["text"] for e in merge_evidence(a, b)] == ["A.", "B."]
        assert merge_evidence(None, None) == []
