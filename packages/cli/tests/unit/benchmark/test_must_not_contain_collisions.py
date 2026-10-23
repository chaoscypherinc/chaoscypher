# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The book fixture's wrong facts against archived answers (H12 of the 2026-10-06 audit).

Until scorer v6 a ``must_not_contain`` phrase also matched with a plural or a
possessive appended, and q027's 'Vasíli is Borís' was a phrase a right answer
says. Each right answer below failed in an archived run; each wrong one is
what the phrase was written to block.

Wave 2 (the same day): the v6 no-possessive rule passed the wrong fact in its
possessive form ('the fete at the French ambassador's'), so the possessive
matches again and the fixture wrote out the wrong statement wherever the
possessive changes the referent ('niece of Nicholas,' rather than 'niece of
Nicholas', which the right 'niece of Nicholas's father' would meet). The
right possessive answers below are what each rewrite keeps passing.

Wave 3: those punctuation-bounded forms let a citation, a line break, a dash,
a colon, '!' or the end of the answer through. Each is now one phrase ending
with the no-possessive marker ('niece of Nicholas|'); :data:`NO_POSSESSIVE`
lists them with a wrong sentence, and every continuation of that sentence
must fail while its possessive passes. A guard keeps punctuation off every
phrase's ends in every shipped fixture.

Scorer v7 (2026-10-08): a phrase inside a negated clause states nothing, so
:data:`NEGATED_RIGHT` passes, while the archived wrong answers whose negation
is part of the wrong fact (:data:`WRONG`, the v7 block) still fail, and the
holdout's reworded h008 / h018 strings (:data:`HOLDOUT`) keep their wrong
answers and spare the right ones.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from chaoscypher_core.benchmark.queries import LabeledQuery, load_queries
from chaoscypher_core.benchmark.scorers import chat_probes as cp


def _datasets() -> Path:
    """The CLI's shipped dataset directory."""
    import chaoscypher_cli.benchmark as bm

    return Path(bm.__file__).parent / "data" / "datasets"


def _book1() -> dict[str, LabeledQuery]:
    """The shipped war_and_peace_book1 fixture's queries by id."""
    return {
        q.id: q for q in load_queries(_datasets() / "war_and_peace_book1" / "queries.yaml").queries
    }


NO_POSSESSIVE = [
    # (question, the no-possessive phrase, a wrong sentence ending on it, its right possessive)
    ("q027", "Pierre Bezúkhov|", "The godfather is Pierre Bezúkhov", "Pierre Bezúkhov's father"),
    (
        "q029",
        "brother of the young Bolkónski|",
        "Prince Andrew is the brother of the young Bolkónski",
        "the brother of the young Bolkónski's sister",
    ),
    (
        "q063",
        "Old Prince Bolkónski|",
        "The aide-de-camp is Old Prince Bolkónski",
        "old Prince Bolkónski's son",
    ),
    (
        "q096",
        "through Prince Andrew|",
        "She arranged it through Prince Andrew",
        "through Prince Andrew's wife",
    ),
    (
        "q096",
        "through her brother|",
        "She arranged it through her brother",
        "through her brother's wife",
    ),
    (
        "q098",
        "niece of Nicholas|",
        "Sónya is the niece of Nicholas",
        "the niece of Nicholas's father",
    ),
    (
        "q115",
        "by Prince Andrew|",
        "Anna Pávlovna is called Annette by Prince Andrew",
        "called Annette by Prince Andrew's wife",
    ),
    (
        "q130",
        "hosted by Countess Rostóva|",
        "The dinner is hosted by Countess Rostóva",
        "hosted by Countess Rostóva's husband",
    ),
    ("q132", "officer is Nicholas|", "The officer is Nicholas", "the officer is Nicholas's friend"),
    ("q132", "student is Borís|", "The student is Borís", "the student is Borís's friend"),
]
"""Every phrase wave 3 rewrote to the marker, with a wrong sentence and a right possessive."""

CONTINUATIONS = (
    " [[cite:C3:S2|war_and_peace_book1.txt]].",
    "",
    ".",
    "\nThe text says so.",
    " - as the text says.",
    " \u2014 as the text says.",
    ": as the text says.",
    "!",
    ", as the text says.",
    " (as the text says).",
    ";",
    " and nobody else.",
    "** [C3]",
)
"""What may follow a wrong statement; the wave-2 punctuation forms missed most of these."""


RIGHT = [
    # 2026-10-04-p2-union-5090, 2026-10-05-p2-policy-v1/v2-5090 (Qwen3.5 9B), p2-v10-c-5090,
    # p3-v3-cleandesc-5090 (Gemma 4 31B): six fails on 'Vasíli is Borís'
    (
        "q027",
        "The son for whom Princess Drubetskáya petitioned Prince Vasíli is Borís "
        "[[cite:C5:S8|war_and_peace_book1.txt]], and his godfather is Count Cyril "
        "Vladímirovich Bezúkhov.",
    ),
    # 2026-10-06-p3-v3-qfirst-b-5090 (Qwen3.5 9B): failed on 'niece of Nicholas'
    ("q098", "Answer: Sónya is the niece of Nicholas’s father (the count) and his cousin."),  # noqa: RUF001 - the archived curly apostrophe
    # 2026-10-05-p2-policy-v1-5090 (Qwen3.5 9B): failed on 'Anatole, Prince Vasíli'
    ("q022", "Anna Pávlovna was arranging a match involving Anatole, Prince Vasíli's son."),
    # 2026-10-05-p2-u-v8-3090 (Gemma 4 26B): failed on 'brother of the young Bolkónski'
    (
        "q029",
        "The brother of the young Bolkónski's sister is the young Bolkónski himself, who is "
        "Prince Andrew.",
    ),
    # wave 2: a right answer through a possessive, for every phrase rewritten whole
    ("q027", "Borís's godfather is Count Bezúkhov, Pierre Bezúkhov's father."),
    ("q063", "Prince Andrew, old Prince Bolkónski's son, is going as Kutúzov's aide-de-camp."),
    ("q096", "Through Prince Andrew's wife, the little princess: her brother's wife Lise."),
    ("q115", "Anna Pávlovna, called Annette by Prince Andrew's wife; and Anna Mikháylovna."),
    ("q130", "Count Bezúkhov is dying; the dinner is hosted by Countess Rostóva's husband."),
    ("q132", "The officer is Nicholas's friend Borís; the student is Borís's friend Nicholas."),
]

WRONG = [
    ("q027", "Prince Vasíli is Borís’s godfather."),  # noqa: RUF001 - intentional curly apostrophe
    ("q098", "Sónya is the niece of Nicholas."),
    # 2026-10-03-chat-probes-naive-v2 (Qwen3.8 27B): the decline the phrase was written for
    (
        "q075",
        "The text does not specify a separate squadron commander beyond Schubert's role as "
        "colonel of the regiment.",
    ),
    # 2026-10-03-chat-probes-hybrid-v2 (Qwen3.6 35B-A3B): the count's care given to Vasíli
    ("q097", "Prince Vasíli has been looking after Pierre's education."),
    # wave 2: the wrong fact in its possessive form, which the v6 no-possessive rule passed
    ("q085", "It was a Thursday, and the fete was at the French ambassador's."),
    ("q014", "Colonel Denísov's regiment, the Pávlograd Hussars."),
    ("q114", "Prince Vasíli's son Hippolyte's friends: Pierre and Dólokhov."),
    # the archived wrong forms each rewrite still catches
    ("q098", "Sónya is the niece of Nicholas Rostóv [[cite:C1|S2]]."),
    (
        "q098",
        "The text describes their relationship as familial, with Sónya being the niece of "
        "Nicholas, indicating a direct kinship link.",
    ),
    (
        "q029",
        "Prince Andrew is described as the brother of the young Bolkónski, who married Lise "
        "Meinen.",
    ),
    ("q063", "Old Prince Bolkónski, the King of Prussia, rejoins as Kutúzov's aide-de-camp."),
    ("q115", "Mlle Schérer is referred to as 'Annette' by Prince Andrew, as the graph shows."),
    ("q130", "The dinner is being hosted by **Countess\u202fRostóva** (the Rostóvs' mother)."),
    ("q132", "The officer is Nicholas and the student is Borís."),
    # scorer v7: archived wrong answers whose negation is the wrong fact, or does not reach it
    # 2026-10-03-chat-probes-hybrid-v2 (Gemma 4 26B): 'only' after the comma asserts its clause
    (
        "q028",
        "Anatole Kurágin's father managed to hush up his son's affair, but the context does not "
        "state what happened to Anatole, only that his father was ordered out of Petersburg.",
    ),
    # 2026-10-03-chat-probes-hybrid-v2 (Qwen3.8 27B): the phrase is a denial; the cue before it
    # is a quoted "I don't like him"
    (
        "q049",
        'However, the statement "I don\'t like him" was not made by Anna Pávlovna but by the '
        "elderly lady.",
    ),
    # 2026-10-03-chat-probes-naive-v2 (Qwen3.5 9B): the wrong decline, marked '!'
    (
        "q126",
        "The context does not contain information identifying which Prince Bolkónski is going "
        "to the war.",
    ),
    # 2026-10-05-p2-u-order-b-5090 (Qwen3.5 9B): the apposition is stated inside a negation
    (
        "q023",
        "The retrieved context does not contain information about a specific position waiting "
        "for the Rostóvs' eldest son (Borís) before he chose the army.",
    ),
]

NEGATED_RIGHT = [
    # 2026-10-03-chat-probes-v2 (Qwen3 30B Instruct): the one archived verdict v7 moves
    (
        "q028",
        "The text does not explicitly state that Anatole was sent back; rather, it was "
        "Bezúkhov's son who was sent back to Moscow.",
    ),
    ("q098", "Sónya is not the niece of Nicholas; she is the niece of his father."),
    ("q085", "The fete was at Anna Pávlovna's, never at the French ambassador's."),
]
"""Right answers that name a wrong fact only to deny it (scorer v7)."""


@pytest.mark.parametrize(("qid", "phrase", "wrong", "_right"), NO_POSSESSIVE)
@pytest.mark.parametrize("after", CONTINUATIONS)
def test_every_continuation_of_a_rewritten_wrong_fact_fails(
    qid: str, phrase: str, wrong: str, _right: str, after: str
) -> None:
    """Cited, at the end, before a line break, a dash, a colon or '!': the wrong fact is stated."""
    q = _book1()[qid]
    assert phrase in q.must_not_contain
    ok, why = cp.must_not_contain({"answer": wrong + after}, q)
    assert not ok and phrase in why, (wrong + after, why)


@pytest.mark.parametrize(("qid", "phrase", "_wrong", "right"), NO_POSSESSIVE)
def test_the_possessive_of_a_rewritten_wrong_fact_passes(
    qid: str, phrase: str, _wrong: str, right: str
) -> None:
    """The marker spares the last word's possessive, straight or curly."""
    q = _book1()[qid]
    for answer in (f"It was {right}.", f"It was {right.replace(chr(39), chr(0x2019))}."):
        ok, why = cp.must_not_contain({"answer": answer}, q)
        assert phrase not in why, (answer, why)


def _shipped_fixtures() -> list[Path]:
    """Every question file a run can score: the CLI's datasets and the holdout set.

    The reference packs carry byte copies of the dataset fixture (sync_pack_fixture.py, whose
    test compares them). The holdout is skipped on the public export (no ``internal/``).
    """
    files = sorted(_datasets().glob("*/queries.yaml"))
    holdout = Path(__file__).resolve().parents[5] / "internal" / "benchmarks" / "holdout"
    if holdout.is_dir():
        files += sorted(holdout.glob("*.yaml"))
    return files


def test_no_shipped_phrase_is_bounded_by_punctuation() -> None:
    """Wave 3: a phrase starts and ends with a word (the marker aside), in every fixture."""
    found: list[str] = []
    for path in _shipped_fixtures():
        for entry in yaml.safe_load(path.read_text(encoding="utf-8"))["queries"]:
            for phrase in entry.get("must_not_contain") or []:
                problems = cp.phrase_problems(str(phrase))
                if problems:
                    found.append(f"{path.name} {entry['id']} {phrase!r}: {problems}")
    assert not found, "\n".join(found)
    assert (
        any("holdout" in p.name for p in _shipped_fixtures())
        or not (Path(__file__).resolve().parents[5] / "internal").is_dir()
    )


@pytest.mark.parametrize(("qid", "answer"), RIGHT)
def test_a_right_answer_states_no_wrong_fact(qid: str, answer: str) -> None:
    """The archived right answers pass ``must_not_contain`` on the shipped fixture."""
    ok, why = cp.must_not_contain({"answer": answer}, _book1()[qid])
    assert ok, why


@pytest.mark.parametrize(("qid", "answer"), WRONG)
def test_the_wrong_fact_is_still_caught(qid: str, answer: str) -> None:
    """Narrowing the match lost none of the wrong answers the phrases were written for."""
    ok, why = cp.must_not_contain({"answer": answer}, _book1()[qid])
    assert not ok, why


@pytest.mark.parametrize(("qid", "answer"), NEGATED_RIGHT)
def test_a_wrong_fact_named_only_to_deny_it_passes(qid: str, answer: str) -> None:
    """Scorer v7: inside a negated clause the phrase is reported as negated, not stated."""
    ok, why = cp.must_not_contain({"answer": answer}, _book1()[qid])
    assert ok and "negated=" in why, why


def test_the_shipped_fixtures_mark_no_phrase_that_needs_none() -> None:
    """'!' is for a wrong decline with its negation outside the phrase, not a polar phrase."""
    found = [
        f"{path.name} {entry['id']} {phrase!r}"
        for path in _shipped_fixtures()
        for entry in yaml.safe_load(path.read_text(encoding="utf-8"))["queries"]
        for phrase in entry.get("must_not_contain") or []
        if str(phrase).startswith(cp.STATED_WHEN_NEGATED_MARKER)
        and cp.carries_its_own_polarity(str(phrase)[1:])
    ]
    assert not found, found


_HOLDOUT = (
    Path(__file__).resolve().parents[5]
    / "internal"
    / "benchmarks"
    / "holdout"
    / "war_and_peace_book1-holdout.yaml"
)

HOLDOUT = [
    # (question, the reworded phrase, a wrong answer that states it, right answers that must pass)
    (
        "h008",
        "his business was the name day",
        "Prince Vasíli said his business was the name day; the real reason was the count.",
        [
            "He came on inspection business, not for the name day, really to see Count Bezúkhov.",
            "His business was not the name day but some inspection business.",
        ],
    ),
    (
        "h008",
        "came to see Pierre|",
        "Anna Mikháylovna said he really came to see Pierre.",
        [
            "Inspection business; but he really came to see Pierre's father, Count Bezúkhov.",
            "He never came to see Pierre: he came to see Count Cyril, hearing how ill he was.",
        ],
    ),
    (
        "h018",
        "Pétya is Nicholas|",
        "Pétya is Nicholas Rostóv, the count's son.",
        [
            "Pétya is Nicholas's younger brother, the count's youngest boy; the other is Pierre.",
            "Pierre and little Pétya: Pétya is Nicholas' little brother.",
        ],
    ),
    (
        "h018",
        "there is only one Pétya",
        "There is only one Pétya in the text: the count's youngest boy.",
        [
            "There is not only one Pétya: Pierre and little Pétya, the count's youngest boy.",
            "It is not true that there is only one Pétya; Pierre is greeted as Pétya.",
        ],
    ),
    (
        "h018",
        "the two are Pierre and Nicholas|",
        "The two are Pierre and Nicholas Rostóv.",
        [
            "The two are Pierre and Nicholas's little brother Pétya, the youngest boy.",
            "Pierre and little Pétya, not Nicholas.",
        ],
    ),
]
"""The holdout's reworded strings (Denis, 2026-10-08), each with a wrong and right answers."""


@pytest.mark.skipif(not _HOLDOUT.is_file(), reason="the holdout set is not in the public tree")
@pytest.mark.parametrize(("qid", "phrase", "wrong", "rights"), HOLDOUT)
def test_the_reworded_holdout_strings_catch_the_wrong_answer_only(
    qid: str, phrase: str, wrong: str, rights: list[str]
) -> None:
    """Each reworded string is in the frozen file, fails its wrong answer and passes the right ones."""
    q = {x.id: x for x in load_queries(_HOLDOUT).queries}[qid]
    assert phrase in q.must_not_contain
    ok, why = cp.must_not_contain({"answer": wrong}, q)
    assert not ok and phrase in why, why
    for right in rights:
        ok, why = cp.must_not_contain({"answer": right}, q)
        assert ok, (right, why)
