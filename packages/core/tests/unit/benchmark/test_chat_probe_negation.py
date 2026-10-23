# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A declared wrong fact inside a negated clause states nothing (scorer v7, Denis 2026-10-08).

The holdout's h008 and h018 forbid strings a correct answer can contain inside
a negation ('not for the name day', 'there is not only one Pétya'). The class
fix is :func:`~chaoscypher_core.benchmark.scorers.chat_probes.negated_span`:
the clause from the nearest preceding negation cue to the next clause boundary
is exempt; a hit anywhere else still fails ``must_not_contain``.
"""

from __future__ import annotations

import pytest

from chaoscypher_core.benchmark.queries import LabeledQuery
from chaoscypher_core.benchmark.scorers import chat_probes as cp


def _verdict(answer: str, *phrases: str) -> tuple[bool, str]:
    """``must_not_contain`` on one answer, the phrases declared as written."""
    q = LabeledQuery(id="h1", band="multi_hop", question="Why?", must_not_contain=list(phrases))
    return cp.must_not_contain({"answer": answer}, q)


def _span(answer: str, phrase: str) -> tuple[int, int] | None:
    """:func:`negated_span` of the phrase's first occurrence in the normalised answer."""
    text = cp.normalise_answer(answer).lines
    start = text.index(phrase)
    return cp.negated_span(text, start, start + len(phrase))


def test_a_plain_hit_still_fails() -> None:
    """No negation anywhere: the wrong fact is stated."""
    ok, why = _verdict("He came to see Pierre.", "came to see Pierre")
    assert not ok and "came to see Pierre" in why and "negated" not in why


@pytest.mark.parametrize(
    "answer",
    [
        "He came on inspection business, not for the name day, really to see Count Bezúkhov.",
        "His business was not the name day.",
        "He never came for the name day.",
        "He didn't come for the name day.",
        "It wasn't for the name day.",
        "Neither for the name day nor for the ball.",
        "He came on business rather than for the name day.",
        "He came on business instead of for the name day.",
        "He cannot have come for the name day.",
        "The visitor said inspection; it was no visit for the name day.",
    ],
)
def test_a_hit_after_a_cue_within_its_clause_passes(answer: str) -> None:
    """Every cue negates the rest of its clause; the phrase is reported as negated."""
    ok, why = _verdict(answer, "for the name day")
    if "for the name day" in cp.normalise_answer(answer).lines:
        assert ok, why
        assert "negated=['for the name day']" in why
    else:  # 'his business was not the name day' does not contain the phrase at all
        assert ok and "negated" not in why


@pytest.mark.parametrize(
    "answer",
    [
        "He did not come for the name day. He came to see Pierre.",
        "He did not come for the name day; he came to see Pierre.",
        "He did not come for the name day: he came to see Pierre.",
        "He did not come for the name day\nHe came to see Pierre",
        "He did not come for the name day - he came to see Pierre.",
        "He did not come for the name day but came to see Pierre.",
        "He did not come for the name day, he came to see Pierre.",
        "He did not come for the name day and he came to see Pierre.",
        "He wanted nothing, never anything except he came to see Pierre.",
        "Not the count; however, he came to see Pierre.",
    ],
)
def test_a_hit_after_a_clause_boundary_still_fails(answer: str) -> None:
    """A negation in the sentence (or clause) before the hit negates nothing in it."""
    ok, why = _verdict(answer, "came to see Pierre")
    assert not ok, why


def test_a_cue_after_the_hit_negates_nothing() -> None:
    """'to see Pierre? No.' asserts first; only a preceding cue counts."""
    assert not _verdict("He came to see Pierre? No.", "came to see Pierre")[0]
    assert not _verdict("He came to see Pierre, which is not stated.", "came to see Pierre")[0]


def test_a_cue_followed_by_no_word_or_an_idiom_negates_nothing() -> None:
    """'No, he came ...' and 'no doubt he came ...' assert what follows."""
    assert not _verdict("No, he came to see Pierre.", "came to see Pierre")[0]
    assert not _verdict("No doubt he came to see Pierre.", "came to see Pierre")[0]
    assert not _verdict("It was none other than Pierre, no other than Pierre.", "than Pierre")[0]


def test_not_only_adds_unless_it_negates_the_phrase_s_own_only() -> None:
    """'not only X' asserts X; 'not only one Pétya' denies 'only one Pétya' (the rule as decided).

    'not only' (and 'not just', 'not merely', 'not simply') is additive: 'he
    came not only to see Pierre but also the count' states 'to see Pierre'.
    When the forbidden phrase itself begins with that word, the cue negates
    the phrase's own claim, so 'there is not only one Pétya' passes 'only
    one Pétya'.
    """
    assert not _verdict("He came not only to see Pierre, but also the count.", "to see Pierre")[0]
    assert not _verdict("He came not just to see Pierre.", "to see Pierre")[0]
    ok, why = _verdict("There is not only one Pétya.", "only one Pétya")
    assert ok and "negated=['only one Pétya']" in why
    ok, _ = _verdict("There is not just one Pétya.", "just one Pétya")
    assert ok
    # the reworded h018 string is not in the right answer at all, negated or not
    ok, why = _verdict("There is not only one Pétya.", "there is only one Pétya")
    assert ok and "negated" not in why


def test_accents_and_folds_are_unchanged() -> None:
    """The negation rule reads the same normalised answer: accents, i/y, possessives, markup."""
    # unaccented answer, accented phrase: still found, still negated
    assert _verdict("There is not only one Petya.", "only one Pétya")[0]
    # i/y fold: 'Vasily' for 'Vasíli', negated and not
    assert _verdict("It was not Prince Vasily.", "Prince Vasíli")[0]
    assert not _verdict("Not the count but Prince Vasily.", "Prince Vasíli")[0]
    # a possessive still states the phrase, and a negation still exempts it
    assert not _verdict("It was at the French ambassador's.", "French ambassador")[0]
    assert _verdict("It was not at the French ambassador's.", "French ambassador")[0]
    # markdown emphasis and citations are read through as before
    assert _verdict("It was **not** *for the name day* [[cite:C1:S2|x.txt]].", "for the name day")[
        0
    ]
    assert not _verdict("It was *for the name day* [[cite:C1:S2|x.txt]].", "for the name day")[0]


def test_the_spelled_lines_keep_flat_positions() -> None:
    """``lines`` is ``flat`` with its line breaks kept, character for character."""
    a = cp.normalise_answer("Not  for the\n\n  name day.\n He came \u201cto see\u201d Pierre ")
    assert a.lines.replace("\n", " ") == a.flat
    assert a.lines == "not for the\nname day.\nhe came to see pierre"


def test_negated_span_runs_from_the_cue_to_the_next_boundary() -> None:
    """The span starts at the governing cue and ends at the clause's end."""
    answer = "Inspection business, not for the name day, really; then home."
    text = cp.normalise_answer(answer).lines
    span = _span(answer, "for the name day")
    assert span is not None
    assert text[span[0] : span[1]] == "not for the name day, really"
    assert _span("He came for the name day.", "for the name day") is None


def test_is_negated_hit_spells_its_words_as_the_transliterated_answer() -> None:
    """With ``transliterate`` the cue and boundary words are i/y-folded too ('they' is 'thei')."""
    fold = cp._transliteration_fold
    text = fold(cp.normalise_answer("It was not Vasily, they said Prince Vasily.").lines)
    first = text.index("vasili")
    second = text.index("prince vasili")
    assert cp.is_negated_hit(text, first, first + 6, transliterate=True)
    assert not cp.is_negated_hit(text, second, second + 13, transliterate=True)
