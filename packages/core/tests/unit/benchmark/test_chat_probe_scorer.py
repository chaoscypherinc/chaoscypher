# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat probe checks and their aggregation."""

from __future__ import annotations

from dataclasses import replace

from chaoscypher_core.benchmark.queries import LabeledQuery, LabeledQuerySet
from chaoscypher_core.benchmark.scorers import chat_probes as cp
from chaoscypher_core.benchmark.types import RawOutput


CONTEXT = (
    "- Bolt, Beranek and Newman: the firm that built the IMP\n"
    "- Robert Kahn: BBN engineer, later ARPA\n"
    "- Vinton Cerf: UCLA, then Stanford\n"
    "- IMP: Honeywell DDP-516"
)


def _q(**over):
    base = {
        "id": "q1",
        "band": "factual_single_hop",
        "question": "Who built the IMP?",
        "gold_entities": ["Bolt, Beranek and Newman"],
    }
    base.update(over)
    return LabeledQuery(**base)


def _rec(answer: str, **over):
    r = {
        "query_id": "q1",
        "band": "factual_single_hop",
        "answer": answer,
        "finish_reason": "stop",
        "context_text": CONTEXT,
    }
    r.update(over)
    return r


def test_answers_from_graph_accepts_a_significant_word_of_the_name() -> None:
    """'BBN' is not in the gold name, but 'Newman' is."""
    ok, _ = cp.answers_from_graph(_rec("It was built by Newman's firm."), _q())
    assert ok
    ok, why = cp.answers_from_graph(_rec("Honeywell built it."), _q())
    assert not ok and "Bolt" in why


def test_no_unsupported_names_flags_names_outside_the_context() -> None:
    """A correct answer padded with training-data names fails; sentence-initial words do not."""
    ok, _ = cp.no_unsupported_names(_rec("The IMP was built by Bolt, Beranek and Newman."), _q())
    assert ok
    ok, why = cp.no_unsupported_names(_rec("BBN, founded by Leo Beranek at MIT, built it."), _q())
    assert (not ok and "Leo Beranek" in why) or "MIT" in why


def test_leads_with_gold_orders_the_confusable_pair() -> None:
    """Explaining the contrast is fine; answering with the wrong one is not."""
    q = _q(
        band="fine_grained_discrimination",
        gold_entities=["Robert Kahn"],
        wrong_entities=["Vinton Cerf"],
    )
    assert cp.leads_with_gold(_rec("Robert Kahn was on the BBN team; Vinton Cerf was not."), q)[0]
    assert not cp.leads_with_gold(_rec("Vinton Cerf, not Robert Kahn."), q)[0]
    assert not cp.leads_with_gold(_rec("Vinton Cerf built it."), q)[0]
    assert cp.leads_with_gold(_rec("Kahn."), _q(band="fine_grained_discrimination"))[
        0
    ]  # nothing declared


def test_leads_with_gold_orders_surname_only_answers() -> None:
    """A surname alone places a name, as ``answers_from_graph`` accepts it (q041 shape).

    Matching only the whole name read 'Cerf ... Kahn' as "confusable entity
    absent" and passed an answer that leads with the wrong person.
    """
    q = _q(
        band="fine_grained_discrimination",
        gold_entities=["Robert Kahn"],
        wrong_entities=["Vinton Cerf"],
    )
    ok, why = cp.leads_with_gold(_rec("Cerf was on the BBN team; Kahn joined later."), q)
    assert not ok, why
    assert cp.leads_with_gold(_rec("Kahn was on the BBN team; Cerf joined later."), q)[0]
    # Word-bounded: 'Kahnweiler' is not 'Kahn'.
    assert not cp.leads_with_gold(_rec("Kahnweiler and Cerf."), q)[0]


def test_declines_when_unsupported_needs_both_signals() -> None:
    """No decoy asserted and a decline phrase present."""
    q = _q(band="out_of_scope", gold_entities=[], decoy_entities=["Raytheon"])
    assert cp.declines_when_unsupported(_rec("The sources do not mention who acquired BBN."), q)[0]
    assert not cp.declines_when_unsupported(_rec("Raytheon acquired BBN in 2009."), q)[0]
    assert not cp.declines_when_unsupported(_rec("It is not in the sources, but Raytheon did."), q)[
        0
    ]
    assert not cp.declines_when_unsupported(_rec("BBN was acquired later."), q)[0]


def test_declines_when_unsupported_accepts_every_common_wording() -> None:
    """Declines worded as 'does not provide/specify/name', 'don't say/give', 'not present'.

    Each is a real answer that scorer v2 failed though it declined and asserted
    no decoy (Opus 5.5, Mistral Small 3.2, Gemma 4 E4B, GPT-OSS, Muse runs).
    """
    q = _q(band="out_of_scope", gold_entities=[], decoy_entities=["Raytheon"])
    for answer in (
        "The sources don't say which estate the author wrote this novel on.",
        "The sources don\u2019t give the name of the estate.",
        "The context does not provide the name of the son.",
        "The context does not specify who commanded the squadron.",
        "The retrieved context does not name a squadron commander.",
        "The information about the marriage in 1862 is not present in the sources.",
    ):
        assert cp.declines_when_unsupported(_rec(answer), q)[0], answer
    # A decoy still fails however politely it is wrapped.
    assert not cp.declines_when_unsupported(
        _rec("The sources don't say, but Raytheon acquired it."), q
    )[0]


def test_no_unsupported_names_ignores_sentence_openers() -> None:
    """Capitalised discourse words at a sentence start are not names."""
    for answer in (
        "BBN built it. Afterwards the IMP shipped.",
        "Beyond that, BBN built it.",
        "Despite the delay, BBN built it. Actually, Newman led it.",
    ):
        ok, why = cp.no_unsupported_names(_rec(answer), _q())
        assert ok, why


def test_a_plain_word_opening_a_sentence_is_not_a_name() -> None:
    """'Additional' and 'Described' open sentences (Qwen3 14B, Qwen3.5 9B, 2026-10-04); no list holds them."""
    for answer in (
        "BBN built it. Additional descriptions confirm the firm.",
        'BBN built it.\n* Described as "the firm" in the text.',
        'The firm was "Bolt, Beranek and Newman." Additional sources agree.',
    ):
        ok, why = cp.no_unsupported_names(_rec(answer), _q())
        assert ok, (answer, why)
    # Still names: a word with no English ending, one the answer capitalises
    # mid-sentence too, an accented one, a bold one.
    for answer, name in (
        ("Raytheon bought BBN.", "Raytheon"),
        ("Collected works came. It was Collected that paid.", "Collected"),
        ("Hélène built it.", "Hélène"),
        ("**Raytheon** built it.", "Raytheon"),
        ("BBN built it, and Raytheon bought it.", "Raytheon"),
    ):
        ok, why = cp.no_unsupported_names(_rec(answer), _q())
        assert not ok and name in why, (answer, why)


def test_a_common_english_word_opening_a_sentence_is_not_a_name() -> None:
    """Wave 2: 'State', 'Even', 'Claims', 'Found', 'Doc' opened leaked-reasoning sentences."""
    for answer in (
        "BBN built it. State that it is not in the sources.",
        "Even if he were, BBN built it.",
        "Claims about the founder are not supported. BBN built it.",
        "Found in the context: BBN built it.",
        "Doc says BBN built it.",
        # not on the list, but the same answer writes it in lower case
        "Ergo the firm built it. BBN was ergo the builder.",
    ):
        ok, why = cp.no_unsupported_names(_rec(answer), _q())
        assert ok, (answer, why)
    # still names: mid-sentence, before a capital, or never written in lower case
    for answer, name in (
        ("BBN built it, and State Street bought it.", "State"),
        ("Ergo built it.", "Ergo"),
        ("Stepán said BBN built it.", "Stepán"),
    ):
        ok, why = cp.no_unsupported_names(_rec(answer), _q())
        assert not ok and name in why, (answer, why)


def test_an_english_word_opening_a_sentence_cell_or_label_value_is_not_a_name() -> None:
    """'Your app', 'Send a request', 'Authentication is' open sentences (the Ollama docs review, 2026-10-23).

    The opener rule now reads a determiner, a preposition or a sentence adverb from a
    closed list, the -tion and -sion endings, an imperative (a word followed by an
    article or a pronoun, which a name does not take), a word after an opening bracket
    or quote, the first word of a table cell, and the text after a label: a bold one of
    up to six words, as a label line is, or a label word such as 'Answer:'.
    """
    for answer in (
        "Your firm was BBN. Within the firm, Kahn led. Any other firm came later. Next, it shipped.",
        "BBN built it.\n2. Locate the IMP.\n3. Send a message.\n4. Place this at UCLA.",
        "1. **Ensure the IMP is set up**: Make sure BBN built it.",
        "Answer: Adjust the IMP at BBN.",
        "BBN built it.\n[Supporting context: the IMP]\n(Optional step) BBN again.",
        "| Firm | Engineer |\n|---|---|\n| BBN | Typical builder of the IMP |",
        "BBN built it. Authentication is handled by Kahn.",
    ):
        ok, why = cp.no_unsupported_names(_rec(answer), _q())
        assert ok, (answer, why)
    # still names: a name before a verb or a preposition, a name the answer capitalises
    # mid-sentence, a name inside a bracket, a name alone in a cell, a long bold lead
    for answer, name in (
        ("BBN built it. Raytheon bought the firm.", "Raytheon"),
        ("BBN built it. Raytheon in Waltham bought the firm.", "Raytheon"),
        ("BBN built it. Locate the IMP. It was Locate that paid.", "Locate"),
        ("BBN built it (Raytheon bought it).", "Raytheon"),
        ("| Firm | Engineer |\n|---|---|\n| Raytheon | Kahn |", "Raytheon"),
        ("1. **Kahn led the firm that built the IMP**: Raytheon came later.", "Raytheon"),
    ):
        ok, why = cp.no_unsupported_names(_rec(answer), _q())
        assert not ok and name in why, (answer, why)


def test_an_english_opener_may_take_a_comma() -> None:
    """'Alternatively, you can', 'Concretely, BBN' open sentences (the Ollama docs review, 2026-10-23).

    The opener rule wanted a lower-case word straight after the opening word; a comma
    between them is as common ('Alternatively,', 'Concretely,', 'Globally,'). The word
    must still be English by the opener rule's tests, so a name before a comma stays one.
    """
    for answer in (
        "Alternatively, BBN built it.",
        "BBN built it. Concretely, the firm shipped the IMP. Globally, Kahn led.",
        "BBN built it. Ergo, the firm shipped the IMP; it was ergo the builder.",
    ):
        ok, why = cp.no_unsupported_names(_rec(answer), _q())
        assert ok, (answer, why)
    for answer, name in (
        ("BBN built it. Raytheon, the buyer, paid.", "Raytheon"),
        ("BBN built it. Ergo, the firm shipped the IMP.", "Ergo"),
        ("BBN built it. Alternatively, Raytheon did.", "Raytheon"),
    ):
        ok, why = cp.no_unsupported_names(_rec(answer), _q())
        assert not ok and name in why, (answer, why)


def test_an_imperative_may_take_to() -> None:
    """'Navigate to the tab', 'Points to your server' open sentences (the Ollama docs review, 2026-10-23).

    A verb in the imperative takes 'to' as often as an article ('Navigate to', 'Switch
    to', 'Points to', 'Applies to'), and so does an adjective that opens a comparison
    ('Similar to'). A name that opens a sentence before 'to' is read the same way, the
    rule's cost; a name before any other word stays a name.
    """
    for answer in (
        "BBN built it.\n2. Navigate to the IMP.",
        "- **Base**: Points to your IMP at BBN.",
        "- **Scope**: Applies to individual IMPs at BBN.",
    ):
        ok, why = cp.no_unsupported_names(_rec(answer), _q())
        assert ok, (answer, why)
    for answer, name in (
        ("BBN built it. Navigate bought the firm.", "Navigate"),
        ("BBN built it. Raytheon of Waltham paid.", "Raytheon"),
    ):
        ok, why = cp.no_unsupported_names(_rec(answer), _q())
        assert not ok and name in why, (answer, why)


def test_an_aggregate_answer_lists_no_member_the_gold_excludes() -> None:
    """'Both connect to WhatsApp, Telegram and Signal' over-includes (the Ollama docs review, 2026-10-23).

    ``wrong_members`` are what a list question's gold leaves out (the neighbouring guides,
    the platforms only one of two tools has); ``holders`` are the entities a "which do
    both have" question asks about. A wrong member fails the answer when it stands in the
    answer's set: in a clause, or a list (its lead and items), that also names a gold
    member. It does not when that clause, item, heading or list names one holder only
    ('Kahn also worked at BBN', 'Its sites include ...' after it), when it is negated or
    quoted, or when no gold member stands beside it.
    """
    q = _q(
        question="Which sites did both Kahn and Cerf visit?",
        gold_entities=["Kahn", "Cerf"],
        answer_terms=["UCLA", "Stanford"],
        wrong_members=["BBN", "ARPA"],
        holders=[("Kahn", "Robert Kahn"), ("Cerf",)],
    )
    for answer in (
        "Both visited UCLA and Stanford. Kahn also worked at BBN and ARPA, while Cerf did not.",
        "Both visited:\n- UCLA\n- Stanford\n\n**Kahn:**\n- BBN\n- ARPA",
        "UCLA and Stanford, not BBN.",
        "Kahn knew UCLA, Stanford and BBN. It also lists ARPA beside UCLA. Both: UCLA, Stanford.",
        "Both visited UCLA and Stanford; \u201cvisits ran to UCLA, Stanford, BBN and ARPA in those"
        " years\u201d, the log says.",
        "- **Kahn**: UCLA, Stanford, BBN\n- **Cerf**: UCLA, Stanford\n\nBoth: UCLA and Stanford.",
        "Both visited UCLA and Stanford. BBN was elsewhere.",
        # a conclusion opens a clause of its own after one holder's list
        "Kahn knew UCLA, Stanford and BBN, so both knew UCLA and Stanford.",
        "Kahn knew UCLA, Stanford and BBN; Cerf knew UCLA and Stanford, and therefore both share two.",
    ):
        ok, why = cp.no_wrong_members(_rec(answer), q)
        assert ok, (answer, why)
    for answer, member in (
        ("Both visited UCLA, Stanford and BBN.", "BBN"),
        ("Kahn and Cerf both visited:\n- UCLA\n- Stanford\n- ARPA", "ARPA"),
        ("Both visited:\n1. **UCLA**: both did.\n2. **BBN**: Kahn did.", "BBN"),
        ("The sites they share are UCLA, Stanford and BBN.", "BBN"),
        ("Kahn and Cerf knew a lot, so both visited UCLA, Stanford and BBN.", "BBN"),
    ):
        ok, why = cp.no_wrong_members(_rec(answer), q)
        assert not ok and member in why, (answer, why)
    ok, why = cp.no_wrong_members(_rec("UCLA, Stanford and BBN."), _q())
    assert ok and why == "no wrong members declared", why


def test_a_decoy_is_matched_word_bounded() -> None:
    """Wave 2: decoy 'Anna' is not asserted by 'Annette'; its possessive still is."""
    q = _q(band="out_of_scope", gold_entities=[], decoy_entities=["Anna"], expect_refusal=True)
    ok, why = cp.declines_when_unsupported(
        _rec("The sources do not say; only Annette is named."), q
    )
    assert ok, why
    ok, why = cp.declines_when_unsupported(_rec("The sources do not say; Anna's guess."), q)
    assert not ok and "Anna" in why


def test_one_sentence_splitter_for_names_and_declines() -> None:
    """A closing quote ends a sentence before a capital; 'etc.)', a possessive and initials do not."""
    assert cp._sentences('It says "cousin to Nicholas." Additional lines follow.') == [
        'It says "cousin to Nicholas."',
        "Additional lines follow.",
    ]
    # folded text has no capitals, so a closing mark never ends a sentence there
    assert len(cp._sentences('the relations (e.g. "x", etc.) do not say. "i bet!" - but no.')) == 2
    assert cp._sentences(
        "Count Orlov's. However, it was not. It was J. C. R. Licklider. Dr. Lorrain came."
    ) == [
        "Count Orlov's.",
        "However, it was not.",
        "It was J. C. R. Licklider.",
        "Dr. Lorrain came.",
    ]
    assert cp._sentences("Answer\n* bullet one. Two!") == ["Answer", "bullet one.", "Two!"]


def test_no_ends_a_sentence_unless_a_number_follows() -> None:
    """'No. The hosted API ...' answers no (the Ollama docs review, 2026-10-23).

    Until then 'No.' was always the abbreviation of 'number', so 'No. The', 'No. If' and
    'No. According' were one capitalised run and an unsupported name. Before a digit or a
    '#' it still abbreviates ('No. 5'), and a name after the answer 'No.' is still a name.
    """
    assert cp._sentences("No. The firm built it. It was No. 5, not No. #6.") == [
        "No.",
        "The firm built it.",
        "It was No. 5, not No. #6.",
    ]
    for answer in (
        "No. The IMP was built by BBN.",
        "No. According to the context, BBN built it.",
        "No. If anything, BBN built it.",
    ):
        ok, why = cp.no_unsupported_names(_rec(answer), _q())
        assert ok, (answer, why)
    ok, why = cp.no_unsupported_names(_rec("No. Raytheon built it."), _q())
    assert not ok and "Raytheon" in why


def test_scorer_gates_on_completion_and_weights_tiers() -> None:
    """Same shape as the extraction probes: verdicts, section rates, tier-weighted headline."""
    qs = LabeledQuerySet(
        version="1",
        queries=[
            _q(id="a", band="factual_single_hop"),
            _q(id="b", band="out_of_scope", gold_entities=[], decoy_entities=[]),
            _q(id="c", band="multi_hop"),
        ],
    )
    out = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras={
            "per_query": [
                _rec("Bolt, Beranek and Newman built it.", query_id="a"),
                _rec("That is not in the sources.", query_id="b"),
                _rec("Bolt, Beranek and Newman", query_id="c", finish_reason="length"),
            ]
        },
    )
    res = cp.ChatProbeScorer().score(out, qs)
    by_id = {v["id"]: v for v in res.metrics["verdicts"]}
    assert by_id["a"]["passed"] and by_id["b"]["passed"] and not by_id["c"]["passed"]
    assert by_id["c"]["tier"] == "medium" and by_id["b"]["tier"] == "hard"
    # easy 1 + hard 3 passed out of 1 + 3 + 2
    assert res.headline_score == (4 / 6) * 100
    assert res.metrics["probes_passed"] == 2 and res.metrics["probes_total"] == 3


def test_answers_from_graph_does_not_require_the_question_subject() -> None:
    """'ARPANET' is in the question; the answer only has to supply the company."""
    q = _q(
        question="Who built the ARPANET switching equipment?",
        gold_entities=["Bolt, Beranek and Newman", "ARPANET"],
    )
    assert cp.answers_from_graph(_rec("Bolt, Beranek and Newman built it."), q)[0]
    assert not cp.answers_from_graph(_rec("Honeywell built it for the ARPANET."), q)[0]


def test_no_unsupported_names_ignores_trailing_punctuation() -> None:
    """'ARPA.' at the end of a sentence is ARPA."""
    ok, why = cp.no_unsupported_names(_rec("Robert Kahn was at BBN and later at ARPA."), _q())
    assert ok, why


def test_mentions_handles_acronyms_both_ways() -> None:
    """TCP/IP as 'TCP and IP', SRI for Stanford Research Institute, UCLA spelled out."""
    assert cp.answers_from_graph(_rec("TCP and IP replaced NCP."), _q(gold_entities=["TCP/IP"]))[0]
    assert cp.answers_from_graph(
        _rec("The nodes were UCLA and SRI."), _q(gold_entities=["Stanford Research Institute"])
    )[0]
    assert cp.answers_from_graph(
        _rec("His lab was at the University of California at Los Angeles."),
        _q(gold_entities=["UCLA"]),
    )[0]
    assert not cp.answers_from_graph(_rec("His lab was at Stanford."), _q(gold_entities=["UCLA"]))[
        0
    ]


def test_answers_from_graph_scores_curated_answer_terms() -> None:
    """A concise correct answer passes on answer_terms; extra gold entities are not required."""
    q = _q(
        question="Which researcher led the lab at the first node?",
        gold_entities=["Leonard Kleinrock", "UCLA"],
        answer_terms=["Leonard Kleinrock"],
    )
    assert cp.answers_from_graph(_srec("The researcher was Leonard Kleinrock."), q)[0]
    ok, why = cp.answers_from_graph(_srec("The researcher was Douglas Engelbart at UCLA."), q)
    assert not ok and "Leonard Kleinrock" in why
    first = _q(question="What was the first message?", gold_entities=["Charley Kline"])
    assert cp.answers_from_graph(_srec("The first message was 'lo'."), _replace(first, ["lo"]))[0]
    assert not cp.answers_from_graph(
        _srec("The first message was 'login'."), _replace(first, ["lo"])
    )[0]
    # every term is required
    two = _q(answer_terms=["TCP/IP", "1983"])
    assert cp.answers_from_graph(_srec("TCP/IP replaced it in 1983."), two)[0]
    assert not cp.answers_from_graph(_srec("TCP/IP replaced it."), two)[0]


def _replace(q: LabeledQuery, terms: list[str | tuple[str, ...]]) -> LabeledQuery:
    return replace(q, answer_terms=terms)


# Scorer v5 requires every answer term to be in the retrieved context, so the
# term-matching tests hand the record a context that holds what they test.
SUPPORT = (
    CONTEXT + "\n- Leonard Kleinrock at UCLA sent the first message, 'lo', to Charley Kline;"
    " TCP/IP replaced it in 1983.\n- Anatole Kur\u00e1gin, Prince Vas\u00edli's son, courts"
    " Princess Mary, Prince Andrew\u2019s sister, at Bald Hills; Lise, the little princess,"
    " is his wife; old Prince Bolk\u00f3nski is their father. Prince Andrew's estate."
)


def _srec(answer: str, **over):
    return _rec(answer, context_text=SUPPORT, **over)


SISTER = _q(
    question="Who does Anatole try to seduce?",
    gold_entities=["Mary Bolkonskaya"],
    answer_terms=[("Mary", "Andrew's sister")],
)


def test_answers_from_graph_alias_group_passes_on_second_member() -> None:
    """An any-of group is satisfied by its second member alone."""
    assert cp.answers_from_graph(_srec("He courts Prince Andrew's sister."), SISTER)[0]


def test_answers_from_graph_alias_member_needs_the_whole_phrase() -> None:
    """A group member matches as a whole phrase, never by one word of it."""
    assert not cp.answers_from_graph(_srec("He courts his sister."), SISTER)[0]
    assert not cp.answers_from_graph(_srec("He courts the prince's sister."), SISTER)[0]
    lise = _replace(SISTER, [("Lise", "little princess")])
    assert not cp.answers_from_graph(_srec("It was the princess."), lise)[0]
    assert not cp.answers_from_graph(_srec("It was Princess Mary Bolk\u00f3nskaya."), lise)[0]
    assert cp.answers_from_graph(_srec("It was the youthful little princess."), lise)[0]
    assert cp.answers_from_graph(_srec("Lise, his wife."), lise)[0]


def test_answers_from_graph_single_word_alias_is_word_bounded() -> None:
    """A one-word member does not match a longer word that starts with it."""
    q = _replace(SISTER, [("Bolk\u00f3nski",)])
    assert not cp.answers_from_graph(_srec("Mary Bolk\u00f3nskaya."), q)[0]
    assert cp.answers_from_graph(_srec("Old Prince Bolk\u00f3nski."), q)[0]


def test_answers_from_graph_single_word_plain_term_stays_loose_multi_word_needs_the_phrase() -> (
    None
):
    """A one-word plain term accepts its plural or possessive; a multi-word plain term is a
    whole phrase (scorer v5: 'four thousand rubles' met 'forty thousand' on a shared word).
    """
    q = _replace(SISTER, ["Kur\u00e1gin"])
    assert cp.answers_from_graph(_srec("The Kuragins' son."), q)[0]
    q = _replace(SISTER, ["Andrew's sister"])
    assert not cp.answers_from_graph(_srec("He courts his sister."), q)[0]
    assert cp.answers_from_graph(_srec("He courts Andrew\u2019s sister."), q)[0]


def test_answers_from_graph_alias_group_fails_when_no_member_named() -> None:
    """No member named: the group fails and is reported pipe-joined."""
    ok, why = cp.answers_from_graph(_srec("He courts Natasha."), SISTER)
    assert not ok
    assert "Mary|Andrew's sister" in why


def test_answers_from_graph_curly_apostrophe_matches_straight_alias() -> None:
    """'Andrew\u2019s' in the answer matches an alias spelled with a straight apostrophe."""
    assert cp.answers_from_graph(_srec("He courts Prince Andrew\u2019s sister."), SISTER)[0]
    # a plain possessive term, straightened the same way
    possessive = _replace(SISTER, ["Andrew's"])
    assert cp.answers_from_graph(_srec("It was Prince Andrew\u2019s estate."), possessive)[0]
    assert not cp.answers_from_graph(_srec("It was the old prince's estate."), possessive)[0]


def test_answers_from_graph_mixed_string_and_group_terms() -> None:
    """A plain string term beside a group is still required."""
    q = _replace(SISTER, ["Kur\u00e1gin", ("Mary", "Andrew's sister")])
    assert cp.answers_from_graph(_srec("Anatole Kuragin courts Mary."), q)[0]
    ok, why = cp.answers_from_graph(_srec("Anatole courts Mary."), q)
    assert not ok
    assert "Kur\u00e1gin" in why
    assert "Mary|" not in why


def test_answers_from_graph_falls_back_to_gold_entities_without_answer_terms() -> None:
    """No answer_terms: the old rule - gold entities the question does not name."""
    q = _q(gold_entities=["Leonard Kleinrock", "UCLA"], question="Who led the lab?")
    assert not cp.answers_from_graph(_rec("Leonard Kleinrock."), q)[0]
    assert cp.answers_from_graph(_rec("Leonard Kleinrock, at UCLA."), q)[0]


def test_arpa_is_not_mentioned_by_arpanet() -> None:
    """Whole-name containment is word-bounded: 'arpa' inside 'arpanet' is no mention."""
    assert not cp._mentions("which agency funded the development of arpanet?", "ARPA")
    assert cp._mentions("it was funded by arpa.", "ARPA")
    assert cp._mentions('he named them "packets."', "packet")
    q = _q(
        question="Which agency funded the development of ARPANET?",
        gold_entities=["ARPA", "ARPANET"],
    )
    # ARPA is not the question's subject, so an answer without it fails
    assert not cp.answers_from_graph(_rec("The Department of Defense funded ARPANET."), q)[0]
    assert cp.answers_from_graph(_rec("ARPA funded it."), q)[0]


def test_candidate_names_do_not_span_sentences() -> None:
    """'...at ARPA. The text notes...' yields ARPA, not 'ARPA. The'; initials stay intact."""
    ok, why = cp.no_unsupported_names(
        _rec("Robert Kahn was at BBN and later at ARPA. The text notes his role."), _q()
    )
    assert ok, why
    names = cp._candidate_names("It was J. C. R. Licklider. Then came Taylor.")
    assert "J. C. R. Licklider" in names and "Taylor" in names
    assert not any("Licklider. Then" in n for n in names)


NUMBERS_CONTEXT = (
    "The IMP was a Honeywell DDP-516 minicomputer. FTP was specified by Abhay Bhushan "
    "in RFC 114. Robert Kahn and Vinton Cerf shared the 2004 award. By 1971 the network "
    "had grown to fifteen nodes.\n[CHUNK C1 | corpus.txt]\n[S1] Four nodes by 1969."
)


def test_no_unsupported_numbers_passes_numbers_the_context_holds() -> None:
    """Identifiers ('RFC 114', 'DDP-516'), years, number words, list markers are all fine."""
    q = _q(question="Which RFC specified FTP?")
    rec = _rec(
        "1. FTP was specified in RFC 114.\n2. The IMP was a DDP-516.\n"
        "3. Kahn and Cerf shared the 2004 award; there were 15 nodes by 1971 [S1].",
        context_text=NUMBERS_CONTEXT,
    )
    ok, why = cp.no_unsupported_numbers(rec, q)
    assert ok, why


def test_no_unsupported_numbers_flags_a_wrong_year() -> None:
    """Right names, wrong year: the names check passes, this one does not."""
    q = _q(question="When did Kahn and Cerf share the award?")
    rec = _rec("Kahn and Cerf shared the 2005 award.", context_text=NUMBERS_CONTEXT)
    assert cp.no_unsupported_names(rec, q)[0]
    ok, why = cp.no_unsupported_numbers(rec, q)
    assert not ok and "2005" in why
    # a number only in the question is supported; part of a longer number is not
    q2 = _q(question="Was it 2005?")
    assert cp.no_unsupported_numbers(rec, q2)[0]
    ok, why = cp.no_unsupported_numbers(_rec("It was RFC 11.", context_text=NUMBERS_CONTEXT), q)
    assert not ok and "11" in why


def test_no_unsupported_numbers_ignores_citation_markers_and_decades() -> None:
    """'[S1]' is not the number 1; '1960s' is checked as 1960."""
    q = _q(question="How many nodes?")
    assert cp.no_unsupported_numbers(_rec("Four [S1][C1].", context_text=NUMBERS_CONTEXT), q)[0]
    ok, why = cp.no_unsupported_numbers(_rec("In the 1960s.", context_text=NUMBERS_CONTEXT), q)
    assert not ok and "1960" in why
    ok, _ = cp.no_unsupported_numbers(_rec("In the 1960s.", context_text="early 1960s"), q)
    assert ok


def test_must_not_contain_fails_on_a_declared_wrong_fact() -> None:
    """Whole-phrase match, word-bounded; an empty list passes."""
    q = _q(must_not_contain=["Network Control Protocol", "1963"])
    assert cp.must_not_contain(_rec("NCP was the Network Control Program."), q)[0]
    ok, why = cp.must_not_contain(_rec("NCP was the Network Control Protocol."), q)
    assert not ok and "Network Control Protocol" in why
    assert not cp.must_not_contain(_rec("He wrote it in 1963."), q)[0]
    assert cp.must_not_contain(_rec("He wrote it in 19634."), q)[0]  # word-bounded
    assert cp.must_not_contain(_rec("Anything at all."), _q())[0]


def test_new_checks_are_registered_per_band() -> None:
    """Numbers run on every in-scope band; must_not_contain on every band."""
    bands = cp.ChatProbeScorer.CHECKS_BY_BAND
    for band, checks in bands.items():
        assert "must_not_contain" in checks
        assert ("no_unsupported_numbers" in checks) == (band != "out_of_scope")
    assert set(cp.ChatProbeScorer.CHECKS) >= {"no_unsupported_numbers", "must_not_contain"}


def test_no_unsupported_names_strips_possessives_and_plurals() -> None:
    """Possessive "Kahn's" is Kahn; plural 'IMPs' is IMP."""
    ok, why = cp.no_unsupported_names(
        _rec("Kahn's role was on the IMPs and the IMP's software."), _q()
    )
    assert ok, why


def test_no_unsupported_names_skips_headings_and_labels() -> None:
    """Formatting is capitalised for layout: headings, bold labels, 'Label:' lines."""
    answer = (
        "## Summary\n"
        "**Step-by-Step Explanation:**\n"
        "Mentions:\n"
        "Here's the answer.\n"
        "- **Company Identification:** Bolt, Beranek and Newman built the IMP.\n"
        "* Proposal by Kahn: the IMP.\n"
        "Note: the IMP."
    )
    ok, why = cp.no_unsupported_names(_rec(answer), _q())
    assert ok, why
    # a label does not hide a name in the claim after it
    ok, why = cp.no_unsupported_names(_rec("- **Builder:** Tim Berners-Lee built it."), _q())
    assert not ok and "Tim Berners-Lee" in why


def test_acronym_expansion_across_a_comma() -> None:
    """'University of California, Los Angeles' satisfies UCLA."""
    q = _q(gold_entities=["UCLA"], answer_terms=["UCLA"])
    assert cp.answers_from_graph(_rec("It was the University of California, Los Angeles."), q)[0]
    assert not cp.answers_from_graph(_rec("It was the University of California."), q)[0]


def test_no_unsupported_names_accepts_expansions_and_coined_acronyms() -> None:
    """An expansion of a context acronym, and an acronym for a supported phrase, are not inventions."""
    ctx = (
        "Kleinrock wrote his dissertation at MIT. Nodes: University of California at Santa Barbara."
    )
    rec = _rec(
        "At the Massachusetts Institute of Technology (MIT); "
        "the University of California, Santa Barbara (UCSB).",
        context_text=ctx,
    )
    ok, why = cp.no_unsupported_names(rec, _q())
    assert ok, why
    # an acronym for a phrase the context does not support is still flagged
    ok, why = cp.no_unsupported_names(_rec("The Augmentation Research Center (ARC)."), _q())
    assert not ok and "Augmentation Research Center" in why


def test_no_unsupported_names_word_rule_covers_a_partial_phrase() -> None:
    """'Technology' passes when the context spells out Massachusetts Institute of Technology."""
    rec = _rec(
        "He studied Technology.",
        context_text="He was at the Massachusetts Institute of Technology.",
    )
    ok, why = cp.no_unsupported_names(rec, _q())
    assert ok, why


def test_alias_of_form_x_of_y_tolerates_a_short_gap() -> None:
    """Opus 5.5, q022: 'the father of the "prodigal son Anatole"' names Anatole's father.

    An alias-group member ``X of Y`` matches with up to four words between X
    and Y (scorer v4); a comma break ('the Rostov boys, Anatole') does not.
    """
    q = _q(band="paraphrase", answer_terms=[("Vasíli", "Anatole's father", "father of Anatole")])
    ok, _ = cp.answers_from_graph(
        _srec('It was the prince: the father of the "prodigal son Anatole" she mentioned.'), q
    )
    assert ok
    ok, _ = cp.answers_from_graph(_srec("He is the father of Anatole."), q)
    assert ok
    ok, why = cp.answers_from_graph(_srec("He is the father of the Rostov boys, Anatole."), q)
    assert not ok and "father of Anatole" in why


def test_declines_when_unsupported_recognises_patterned_wordings() -> None:
    """Declines the v3 phrase list missed are caught by patterns (scorer v4)."""
    q = _q(band="out_of_scope", gold_entities=[], decoy_entities=["Yasnaya"], expect_refusal=True)
    for text in (
        "Nowhere in the passages is the estate named.",
        "The retrieved text is not explicit about the estate.",
        "The excerpts never identify the estate.",
        "There is no indication in these sources of where it was written.",
        "That detail is absent from the retrieved context.",
        "I can't tell from these passages where it was written.",
    ):
        ok, why = cp.declines_when_unsupported(_rec(text), q)
        assert ok, (text, why)
    ok, _ = cp.declines_when_unsupported(
        _rec("He wrote it at Yasnaya; the text does not say more."), q
    )
    assert not ok
    # A hedge followed by a labelled answer asserts that answer (Qwen3 8B, q075).
    hedged = "There is no explicit mention of a squadron commander.\n\n**Answer:** Schubert."
    assert not cp.declines_when_unsupported(_rec(hedged), q)[0]
    assert cp.declines_when_unsupported(_rec("**Answer:** It is not in the sources."), q)[0]
    # A stray label with no word in it does not undo the decline (a local model's 'Answer: /think').
    stray = (
        "The context does not mention the battle, so it is not in the sources.\n\nAnswer: /think"
    )
    assert cp.declines_when_unsupported(_rec(stray), q)[0]


def test_scorer_skips_questions_flagged_unanswerable() -> None:
    """A question the retrieved context cannot answer is left out of verdicts and totals."""
    fixture = LabeledQuerySet(version="1.0", queries=[_q(id="q1"), _q(id="q2", answerable=False)])
    out = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras={
            "per_query": [
                _rec("Newman's firm.", query_id="q1"),
                _rec("Nobody.", query_id="q2"),
            ]
        },
    )
    res = cp.ChatProbeScorer().score(out, fixture)
    assert res.metrics["probes_total"] == 1 and res.metrics["probes_passed"] == 1
    assert [v["id"] for v in res.metrics["verdicts"]] == ["q1"]
    assert res.metrics["excluded"] == ["q2"]
    assert res.metrics["chat_probe_scorer_version"] == cp.CHAT_PROBE_SCORER_VERSION


# ---------------------------------------------------------------------------
# Citation accuracy (reported, never gating) and ignore_answerable
# ---------------------------------------------------------------------------


def _chunk(alias: str, *sentences: str) -> str:
    """A chunk as the context renders it: fenced, headed, sentence-numbered."""
    body = "\n".join(f"[S{i + 1}] {s}" for i, s in enumerate(sentences))
    return (
        f"<untrusted_document>\n[CHUNK {alias} | war_and_peace_book1.txt]\n{body}\n"
        "</untrusted_document>"
    )


# The chunk layout of the 2026-10-01 runs' q022 and q002 contexts, abridged.
BOOK_CONTEXT = "\n".join(
    [
        "- Anna Pávlovna Schérer",
        "- Prince Vasíli Kurágin parent_of Anatole",
        _chunk(
            "C0",
            "Anna Pávlovna meditated.",
            "“Have you never thought of marrying your prodigal son Anatole?” she asked.",
        ),
        _chunk(
            "C1",
            "“Arrange that affair for me and I shall always be your most devoted slave-slafe with an f,” said Prince Vasíli.",
        ),
        _chunk("C2", "Anna Pávlovna wished both to rebuke him and to console him."),
        _chunk(
            "C3",
            "It was in July, 1805, and the speaker was the well-known Anna Pávlovna Schérer, maid of honor and favorite of the Empress Márya Fëdorovna.",
        ),
    ]
)


def _book_rec(answer: str, **over: object) -> dict[str, object]:
    return _rec(answer, context_text=BOOK_CONTEXT, **over)


def test_cited_chunks_parses_the_shapes_models_actually_use() -> None:
    """Every citation shape in the archived runs, ranges expanded, S-labels ignored, order kept."""
    cases = {
        "kissed her hand [C0][C1]. The context calls him only the prince [C2][C8].": [
            "C0",
            "C1",
            "C2",
            "C8",
        ],
        'Source: [CHUNK C1 | war_and_peace_book1.txt] [S2] "It was in July, 1805."': ["C1"],
        'This is explicitly stated: "she had had twelve" (chunk C1).': ["C1"],
        'the text mentions "Annette" (Chunk C4), "Anna Pávlovna" (Chunk C5).': ["C4", "C5"],
        "All references (e.g., Borís mentioned in C0, C4, and C9) are statements.": [
            "C0",
            "C4",
            "C9",
        ],
        "The retrieved context (chunks C0\u2013C9) does not mention it.": [
            f"C{i}" for i in range(10)
        ],
        "Dólokhov (CHUNK C2, CHUNK C8). Also (from CHUNK C3) and (Chunks C1, C2).": [
            "C2",
            "C8",
            "C3",
            "C1",
        ],
        "In **[CHUNK C1]**, a speaker says so; see [CHUNK C3 | war_and_peace_book1.txt S4].": [
            "C1",
            "C3",
        ],
        "The vitamin C3 complex and section 2.1 are not citations.": [],
        "Not in the sources.": [],
    }
    for answer, expected in cases.items():
        assert cp.cited_chunks(answer) == expected, answer


def test_context_chunks_splits_the_rendered_context_by_header() -> None:
    chunks = cp.context_chunks(BOOK_CONTEXT)
    assert list(chunks) == ["C0", "C1", "C2", "C3"]
    assert "prodigal son Anatole" in chunks["C0"]
    assert "slave-slafe" in chunks["C1"] and "prodigal" not in chunks["C1"]
    assert cp.context_chunks("- Kutuzov\n- Bagration serves under Kutuzov") == {}


def test_a_citation_of_a_chunk_holding_an_answer_term_is_correct() -> None:
    """Opus 5.5, q022: '[C0][C1]' where C1 holds 'Vasíli' and C0 holds 'Anatole' (the father-of alias)."""
    q = _q(band="paraphrase", answer_terms=[("Vasíli", "Anatole's father", "father of Anatole")])
    ok, why = cp.cites_the_answer(
        _book_rec(
            'the father of the "prodigal son Anatole". He would always be her "most devoted '
            'slave-slafe with an f," and then kissed her hand [C0][C1].'
        ),
        q,
    )
    assert ok is True and "C1" in why


def test_a_citation_quoting_the_header_counts_and_a_wrong_chunk_fails() -> None:
    """Qwen3.5 27B, q002 cites the header verbatim; Qwen3 8B's shape cites a chunk without the term."""
    q = _q(band="factual_single_hop", answer_terms=["Fëdorovna"])
    ok, _ = cp.cites_the_answer(
        _book_rec(
            "She was maid of honor of the **Empress Márya Fëdorovna**.\n\nSource: "
            "[CHUNK C3 | war_and_peace_book1.txt] [S1]"
        ),
        q,
    )
    assert ok is True
    ok, why = cp.cites_the_answer(_book_rec("Empress Márya Fëdorovna (chunk C1)."), q)
    assert ok is False and "C1" in why
    # a label the context never rendered is a wrong citation too
    ok, why = cp.cites_the_answer(_book_rec("Empress Márya Fëdorovna [C7]."), q)
    assert ok is False and "C7" in why


def test_an_uncited_answer_and_an_out_of_scope_answer_are_not_judged() -> None:
    q = _q(band="factual_single_hop", answer_terms=["Fëdorovna"])
    ok, why = cp.cites_the_answer(_book_rec("Empress Márya Fëdorovna."), q)
    assert ok is None and "uncited" in why
    oos = _q(
        band="out_of_scope", gold_entities=[], decoy_entities=["Austerlitz"], expect_refusal=True
    )
    ok, why = cp.cites_the_answer(_book_rec("That is not in the sources [C0]."), oos)
    assert ok is None and "out of scope" in why


def test_scorer_reports_citation_accuracy_without_moving_any_verdict() -> None:
    """Per question a citation state; per row cited, correct and the accuracy; passes unchanged."""
    fixture = LabeledQuerySet(
        version="1.0",
        queries=[
            _q(id="a", band="factual_single_hop", answer_terms=["Fëdorovna"]),
            _q(id="b", band="factual_single_hop", answer_terms=["Fëdorovna"]),
            _q(id="c", band="factual_single_hop", answer_terms=["Fëdorovna"]),
            _q(
                id="d",
                band="out_of_scope",
                gold_entities=[],
                decoy_entities=[],
                expect_refusal=True,
            ),
        ],
    )
    records = [
        _book_rec("Empress Márya Fëdorovna [C3].", query_id="a"),  # correct
        _book_rec("Empress Márya Fëdorovna (chunk C1).", query_id="b"),  # wrong chunk
        _book_rec("Empress Márya Fëdorovna.", query_id="c"),  # uncited
        _book_rec("That is not in the sources [C0].", query_id="d"),  # not judged
    ]
    out = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras={"per_query": records},
    )
    res = cp.ChatProbeScorer().score(out, fixture)
    by_id = {v["id"]: v for v in res.metrics["verdicts"]}
    assert [by_id[k]["citation"] for k in "abcd"] == ["correct", "wrong", "uncited", None]
    assert all(by_id[k]["passed"] for k in "abcd"), by_id
    assert any(d.startswith("CITE cites_the_answer") for d in by_id["b"]["details"])
    assert not any(d.startswith("FAIL cites_the_answer") for d in by_id["b"]["details"])
    assert res.metrics["citations_cited"] == 2 and res.metrics["citations_correct"] == 1
    assert res.metrics["citation_accuracy"] == 50.0
    assert res.metrics["probes_passed"] == 4

    nothing_cited = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras={"per_query": records[2:]},
    )
    res = cp.ChatProbeScorer().score(
        nothing_cited, LabeledQuerySet(version="1.0", queries=fixture.queries[2:])
    )
    assert res.metrics["citation_accuracy"] is None


def test_ignore_answerable_scores_the_flagged_questions_for_the_comparison() -> None:
    """The retrieval comparison scores every question: a rung's miss is its measurement."""
    fixture = LabeledQuerySet(
        version="1.0",
        queries=[_q(id="q1"), _q(id="q2", answerable=False)],
    )
    out = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras={
            "per_query": [_rec("Newman's firm.", query_id="q1"), _rec("Nobody.", query_id="q2")]
        },
    )
    res = cp.ChatProbeScorer(ignore_answerable=True).score(out, fixture)
    assert [v["id"] for v in res.metrics["verdicts"]] == ["q1", "q2"]
    assert res.metrics["excluded"] == []
    assert res.metrics["probes_total"] == 2 and res.metrics["probes_passed"] == 1
    default = cp.ChatProbeScorer().score(out, fixture)
    assert default.metrics["excluded"] == ["q2"] and default.metrics["probes_total"] == 1


# ---------------------------------------------------------------------------
# scorer v5: the audit of 2026-10-03 over the 21-model runs
# ---------------------------------------------------------------------------

BOOK = (
    "[CHUNK C0 | war_and_peace_book1.txt]\n[S1] He\u2019s a cornet in the Horse Guards, said "
    "Kar\u00e1gin. [S2] Borís was to be an officer. [S3] Count Rostóv gave forty thousand "
    "rubles; eighty people came to dinner, and the little princess, Lise, was there.\n"
)


def _brec(answer: str, **over):
    return _rec(answer, context_text=BOOK, **over)


def test_citation_markup_is_not_a_name_or_a_number() -> None:
    """Sentence labels, ranges and cite offsets failed correct answers on the 2026-10-03 runs."""
    q = _q(answer_terms=["cornet"])
    for answer in (
        "He was a cornet [[cite:C0:S1|war_and_peace_book1.txt]].",
        "He was a cornet [C0, S10-S12].",
        "He was a cornet (chunk C0, S1-S3).",
        "He was a cornet. Citation: C0:5",
        "He was a cornet [KG:1].",
        "Knowledge-graph entry:\nHe was a cornet [C0:S1].",
    ):
        assert cp.no_unsupported_names(_brec(answer), q)[0], answer
        assert cp.no_unsupported_numbers(_brec(answer), q)[0], answer


def test_quoted_contractions_and_accented_names_are_not_invented_names() -> None:
    """'He\u2019s' quoted from the text and 'Kar\u00e1gin' were flagged; 'Furthermore' too."""
    q = _q(answer_terms=["cornet"])
    for answer in (
        "He\u2019s a cornet, the text says. Furthermore, Kar\u00e1gin said so. Among others.",
        "It's a cornet. Moreover, Whether or not. [End of answer]",
    ):
        ok, why = cp.no_unsupported_names(_brec(answer), q)
        assert ok, why
    ok, why = cp.no_unsupported_names(_brec("Kar\u00e1gin met Dólokhov."), q)
    assert not ok and "D\u00f3lokhov" in why


def test_a_number_is_supported_by_its_words_up_to_the_thousands() -> None:
    """'80 people' against 'eighty', '40,000' against 'forty thousand' (scorer v5)."""
    q = _q(answer_terms=["cornet"])
    assert cp.no_unsupported_numbers(_brec("80 people came; he gave 40,000 rubles."), q)[0]
    ok, why = cp.no_unsupported_numbers(_brec("90 people came."), q)
    assert not ok and "90" in why
    assert cp._number_in_words(25) == "twenty-five"
    assert cp._number_in_words(500) == "five hundred"
    assert cp._number_in_words(40000) == "forty thousand"


def test_an_in_scope_answer_that_declines_in_its_conclusion_fails() -> None:
    """'The sources do not say, but she is called the niece' answers nothing (scorer v5)."""
    q = _q(answer_terms=[("Lise", "little princess")])
    ok, why = cp.answers_from_graph(
        _brec("The little princess is mentioned, but the sources do not say who she is."), q
    )
    assert not ok and "declines" in why
    ok, why = cp.answers_from_graph(_brec("Answer: not in the sources (the little princess)."), q)
    assert not ok and "declines" in why
    # a hedge that still answers is an answer: the conclusion names the term
    ok, why = cp.answers_from_graph(
        _brec("The text does not state her title. However, she is Lise, the little princess [C0]."),
        q,
    )
    assert ok, why
    ok, why = cp.answers_from_graph(
        _brec("The sources do not give her age, but the little princess is Lise."), q
    )
    assert ok, why
    # a concession after the decline is still a decline (the verification round, 2026-10-03)
    ok, why = cp.answers_from_graph(
        _brec("The sources do not say who she is, though the little princess is mentioned."), q
    )
    assert not ok and "declines" in why
    ok, why = cp.answers_from_graph(
        _brec("The text does not say whether Lise is the little princess."), q
    )
    assert not ok and "declines" in why
    assert cp.declines("these passages contain no such battle.")


def test_an_answer_term_absent_from_the_context_does_not_count() -> None:
    """A right answer from memory is not a grounded answer (scorer v5)."""
    q = _q(answer_terms=["cornet"])
    ok, why = cp.answers_from_graph(_rec("He was a cornet."), q)  # CONTEXT has no cornet
    assert not ok and "not in the retrieved context" in why
    assert cp.answers_from_graph(_brec("He was a cornet."), q)[0]
    # a stem of the term in the context is enough ('retired' against 'retire')
    assert cp._term_in_support(("retired", "left the service"), "he wished to retire soon")


def test_no_other_entity_in_the_context_is_an_assertion_not_a_decline() -> None:
    """'No other entity in the context fits' asserts an answer (q075, Qwen3 8B)."""
    assert not cp.declines("the commander was schubert. no other entity in the context fits.")
    assert cp.declines("the commander is not named in the context.")


def test_an_out_of_scope_decline_that_then_guesses_fails() -> None:
    """'The sources do not name him; it was presumably Prince Vasíli' asserts an answer (v5)."""
    q = _q(
        band="out_of_scope", gold_entities=[], decoy_entities=["Rostopchín"], expect_refusal=True
    )
    ok, why = cp.declines_when_unsupported(
        _rec("The sources do not name him; it was presumably Prince Vasíli."), q
    )
    assert not ok and "speculates" in why
    assert cp.declines_when_unsupported(_rec("The sources do not name the governor."), q)[0]


def test_parenthetical_caveats_and_leaked_reasoning_are_not_declines_or_guesses() -> None:
    """Real answers, 2026-10-03: '(the text does not name it)' is a caveat, 'if I assume' in
    leaked reasoning and a '(likely X)' aside are not guesses; 'presumably X' is.
    """
    q = _q(answer_terms=["cornet"])
    ok, why = cp.answers_from_graph(
        _brec("He became a cornet (the text does not name the regiment) [C0]."), q
    )
    assert ok, why
    oos = _q(
        band="out_of_scope", gold_entities=[], decoy_entities=["Rostopchín"], expect_refusal=True
    )
    for answer in (
        "The context does not name the estate. I can cite C0 if I assume the label applies.",
        "The context does not mention it; Prince Vasíli (likely Prince Hippolyte) appears elsewhere.",
    ):
        assert cp.declines_when_unsupported(_rec(answer), oos)[0], answer
    assert not cp.declines_when_unsupported(
        _rec("The sources do not say; it was probably Prince Vasíli."), oos
    )[0]


def test_caveats_hyphens_and_closing_caveats() -> None:
    """2026-10-03 real answers: a decline about a premise the text does not state is a caveat;
    a non-breaking hyphen matches; a closing caveat after a quoted answer is not a decline; a
    decline followed by a sentence that merely mentions a term still declines.
    """
    q = replace(_q(answer_terms=["cornet"]), caveats=["regiment's name"])
    ok, why = cp.answers_from_graph(
        _brec("The regiment's name is not given in the sources; he was a cornet [C0]."), q
    )
    assert ok, why
    q2 = _q(answer_terms=[("forty thousand", "40,000")])
    assert cp.answers_from_graph(
        _brec("He gave forty\u2011thousand... forty thousand rubles."), q2
    )[0]
    q3 = _q(answer_terms=["cornet"])
    ok, why = cp.answers_from_graph(
        _brec(
            "The text identifies what he became:\n\n"
            '* "He is a cornet, and a fine one, in the Horse Guards now" [C0]\n\n'
            "The text does not show the scene at the door."
        ),
        q3,
    )
    assert ok, why
    ok, why = cp.answers_from_graph(
        _rec(
            "The text does not describe him further, but it is Dr. Lorrain.",
            context_text="Dr. Lorrain came from Petersburg.",
        ),
        _q(answer_terms=["Lorrain"]),
    )
    assert ok, why
    ok, why = cp.answers_from_graph(
        _brec("The sources do not say what he became. A cornet is mentioned at C0."), q3
    )
    assert not ok and "declines" in why


# ---------------------------------------------------------------------------
# 2026-10-03 full pass over every real answer
# ---------------------------------------------------------------------------


def test_roles_bind_each_capacity_to_its_own_person() -> None:
    """A swapped or missing capacity fails; a role two people hold may follow either."""
    q = replace(
        _q(answer_terms=["Andrew"]),
        roles=[
            {"person": ["Andrew"], "roles": ["aide-de-camp", "officer"]},
            {"person": ["Nicholas"], "roles": ["hussar", "cadet"]},
            {"person": ["Borís"], "roles": ["Guards", "officer"]},
        ],
    )
    good = "Andrew goes as an aide-de-camp, Nicholas as a hussar cadet, Borís in the Guards."
    assert cp.binds_roles(_rec(good), q)[0]
    ok, why = cp.binds_roles(
        _rec("Andrew as a hussar, Nicholas as aide-de-camp, Borís in the Guards."), q
    )
    assert not ok and "misbound" in why
    ok, why = cp.binds_roles(
        _rec("Nicholas joins as an officer; Andrew as aide-de-camp; Borís in the Guards."), q
    )
    assert not ok
    ok, why = cp.binds_roles(_rec("Andrew is the aide-de-camp and Nicholas a hussar."), q)
    assert not ok and "Borís" in why


def test_evidence_and_alias_evidence_ground_the_asked_fact() -> None:
    """A right name the context never ties to the asked fact is from memory."""
    q = replace(_q(answer_terms=["Kutuzov"]), evidence=["aide-de-camp of Kutuzov"])
    ctx = "He is an aide-de-camp of Kutuzov's."
    assert cp.answers_from_graph(_rec("Kutuzov.", context_text=ctx), q)[0]
    ok, why = cp.answers_from_graph(_rec("Kutuzov.", context_text="Kutuzov was there."), q)
    assert not ok and "evidence" in why
    q2 = replace(
        _q(answer_terms=[("Andrew", "young Bolkonski")]),
        alias_evidence={"Andrew": ["wife of Prince Andrew"]},
    )
    ctx2 = "Prince Andrew looked at them. Lise, young Bolkonski's wife, smiled."
    ok, why = cp.answers_from_graph(_rec("Prince Andrew.", context_text=ctx2), q2)
    assert not ok and "not in the retrieved context" in why
    assert cp.answers_from_graph(_rec("Young Bolkonski.", context_text=ctx2), q2)[0]


def test_names_titles_typos_and_transliterations() -> None:
    """A title alone supports nothing; a same-answer typo, i/y and a long transliteration pass."""
    q = _q(answer_terms=["Kuragin"])
    ctx = "Prince Vasili Kuragin and the Countess came from Petersbourg."
    ok, why = cp.no_unsupported_names(_rec("Countess Rastoptchin was there.", context_text=ctx), q)
    assert not ok and "Rastoptchin" in why
    assert cp.no_unsupported_names(_rec("Karagin, that is Kuragin, came.", context_text=ctx), q)[0]
    assert not cp.no_unsupported_names(_rec("Karagin came.", context_text=ctx), q)[0]
    assert cp.no_unsupported_names(_rec("Prince Vasily's son came.", context_text=ctx), q)[0]
    assert cp.no_unsupported_names(_rec("They came from Petersburg.", context_text=ctx), q)[0]
    assert cp.no_unsupported_names(
        _rec("As in the English phrase. Reference: C0.", context_text=ctx), q
    )[0]


def test_leaked_reasoning_is_not_scored_and_the_conclusion_leads() -> None:
    """Only the text after the last </think> is the answer; a contrast first is judged by its conclusion."""
    fixture = LabeledQuerySet(version="1.0", queries=[_q(id="q1", answer_terms=["Newman"])])
    out = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras={
            "per_query": [_rec("Filename State Text 2 </think> Newman's firm.", query_id="q1")]
        },
    )
    assert cp.ChatProbeScorer().score(out, fixture).metrics["probes_passed"] == 1
    fg = _q(
        band="fine_grained_discrimination", gold_entities=["Nicholas"], wrong_entities=["Boris"]
    )
    assert cp.leads_with_gold(_rec("Boris is tall and fair. The short one is Nicholas."), fg)[0]
    assert not cp.leads_with_gold(
        _rec("Boris is the short one, and Nicholas is tall. So: Boris."), fg
    )[0]


# ---------------------------------------------------------------------------
# 2026-10-04 review of the pinned Claude rows: spelling, disowned terms, quoted wrong facts
# ---------------------------------------------------------------------------


def test_a_long_name_one_letter_off_is_the_name_unless_the_context_uses_that_spelling() -> None:
    q = _q(question="Where was Borís to join the Guards?", answer_terms=["Radzivílov"])
    ctx = "Borís was to join them on the march to Radzivílov."
    ok, _ = cp.answers_from_graph(_rec("On the march to Radziválov.", context_text=ctx), q)
    assert ok  # a substitution in a ten-letter name the context never spells that way
    one_more = _q(question="What did Borís call her?", answer_terms=[("Ilyníchna",)])
    ok, _ = cp.answers_from_graph(
        _rec("He called her Natálya Ilynínchna.", context_text="Natálya Ilyníchna behaves well."),
        one_more,
    )
    assert ok  # one inserted letter
    minister = _q(question="Which minister?", answer_terms=["Rumyántsov"])
    ok, why = cp.answers_from_graph(
        _rec("Count Rumyántsev.", context_text="the Rumyántsovs' theatricals; Count Rumyántsev"),
        minister,
    )
    assert not ok and "missing" in why  # the text names Rumyántsev too: another person
    short = _q(question="Which family?", answer_terms=["Kurágin"])
    ok, _ = cp.answers_from_graph(_rec("The Karágins.", context_text="Prince Kurágin."), short)
    assert not ok  # seven letters: a substitution is another family


def test_a_term_named_only_to_be_taken_back_is_not_answered() -> None:
    q = _q(
        band="multi_hop",
        question="Which women are called Annette, and by whom?",
        answer_terms=["Pávlovna", "Mikháylovna"],
    )
    ctx = "Anna Pávlovna; Anna Mikháylovna helps the countess. 'Annette', said the countess."
    hedged = (
        "Anna Pávlovna is called Annette by her guests. The graph describes Anna Mikháylovna "
        "as the countess's friend, so she is probably this Annette, but the sources do not say so directly."
    )
    ok, why = cp.answers_from_graph(_rec(hedged, context_text=ctx), q)
    assert not ok and "declines" in why
    caveat = (
        "Anna Pávlovna is called Annette by her guests, and Anna Mikháylovna by the countess. "
        "Anna Mikháylovna is the countess's friend, but the context does not say how old she is."
    )
    ok, _ = cp.answers_from_graph(_rec(caveat, context_text=ctx), q)
    assert ok  # a caveat about another detail, the term asserted before


def test_quote_marks_do_not_hide_a_declared_wrong_fact() -> None:
    q = _q(must_not_contain=["as Princess Mary Bolkónskaya"])
    ok, why = cp.must_not_contain(_rec('The graph lists her as "Princess Mary Bolkónskaya".'), q)
    assert not ok and "Princess Mary" in why


# ---------------------------------------------------------------------------
# scorer v6 (2026-10-06 pipeline audit, H12 and H15)
# ---------------------------------------------------------------------------


def test_must_not_contain_matches_a_phrase_in_its_possessive_form() -> None:
    """Wave 2: 'the French ambassador's' states 'French ambassador' (q085), straight or curly."""
    q = _q(must_not_contain=["French ambassador", "Denísov"])
    for answer in (
        "It was a Thursday, and the fete was at the French ambassador's.",
        "It was a Thursday, and the fete was at the French ambassador’s.",  # noqa: RUF001 - curly
        "Colonel Denísov's regiment.",
    ):
        ok, why = cp.must_not_contain(_rec(answer), q)
        assert not ok, (answer, why)
    assert cp.must_not_contain(_rec("The fete was at the English ambassador's."), q)[0]


def test_the_no_possessive_marker_keeps_the_right_possessive_passing() -> None:
    """q098, wave 3: 'niece of Nicholas|' fails every continuation but the possessive."""
    q = _q(must_not_contain=["niece of Nicholas|"])
    right = "Answer: Sónya is the niece of Nicholas's father (the count) and his cousin."
    assert cp.must_not_contain(_rec(right), q)[0]
    assert cp.must_not_contain(_rec(right.replace("'", "’")), q)[0]  # noqa: RUF001 - curly too
    assert cp.must_not_contain(_rec("She is the niece of Nicholas' father."), q)[0]
    for wrong in (
        "Sónya is the niece of Nicholas.",
        "Sónya is the niece of Nicholas",
        "Sónya is the niece of Nicholas [[cite:C1:S2|war_and_peace_book1.txt]].",
        "Sónya is the niece of Nicholas\nand lives with them.",
        "Sónya is the niece of Nicholas - his kin.",
        "Sónya is the niece of Nicholas: his kin.",
        "Sónya is the niece of Nicholas!",
        "the niece of Nicholas Rostov, his kin",
        "the niece of\nNicholas, his kin",
    ):
        ok, why = cp.must_not_contain(_rec(wrong), q)
        assert not ok and "niece of Nicholas" in why, wrong
    # without the marker the possessive states the phrase (the wave-2 rule, still the default)
    assert not cp.must_not_contain(_rec(right), _q(must_not_contain=["niece of Nicholas"]))[0]


def test_punctuation_at_a_phrase_end_bounds_nothing() -> None:
    """Wave 3: a trailing ',' or '(' is dropped, so it cannot let any continuation escape."""
    for phrase in ("hosted by Countess Rostóva (", "hosted by Countess Rostóva,"):
        q = _q(must_not_contain=[phrase])
        for answer in (
            "It is hosted by **Countess Rostóva** (the mother).",
            "It is hosted by Countess Rostóva [[cite:C0:S2]].",
            "It is hosted by Countess Rostóva",
        ):
            assert not cp.must_not_contain(_rec(answer), q)[0], (phrase, answer)
    assert cp.phrase_problems("hosted by Countess Rostóva (") != []
    assert cp.phrase_problems("niece of Nicholas,") != []
    assert cp.phrase_problems("hosted by Countess Rostóva|") == []


def test_punctuation_inside_a_phrase_is_matched_as_written() -> None:
    """A comma is not a space: 'Sónya and Borís' is not stated by 'Sónya, and Borís and Natásha'.

    Tolerating it moved q118 on two right archived answers ('Nicholas and Sónya, and Borís
    and Natásha'), so inner punctuation stays literal; whitespace between words is any run.
    """
    q = _q(must_not_contain=["Sónya and Borís", "his son, Borís"])
    assert cp.must_not_contain(
        _rec("The couples were Nicholas and Sónya, and Borís and Natásha."), q
    )[0]
    assert cp.must_not_contain(_rec("He asked for his son Borís."), q)[0]
    assert not cp.must_not_contain(_rec("He asked for his son,  Borís."), q)[0]
    assert not cp.must_not_contain(_rec("Sónya and\nBorís sat apart."), q)[0]


def test_a_phrase_problem_names_each_fault() -> None:
    """The fixture guard's reasons: an end that is not a word, a misplaced marker, no words."""
    assert cp.phrase_problems("his son, Borís") == []
    assert cp.phrase_problems("little princess' father") == []
    assert cp.phrase_problems("(Princess Mary)")[0].startswith("punctuation or space at an end")
    assert any("anywhere but at the end" in p for p in cp.phrase_problems("a|b"))
    assert cp.phrase_problems("|") == ["no words"]
    assert cp.phrase_problems("his  son") == ["words not separated by single spaces"]
    assert cp.wrong_fact_words("Niece of Nicholas|") == (("niece", "of", "nicholas"), True)


def test_must_not_contain_does_not_append_a_plural() -> None:
    """A phrase is matched as written: 'his brother' is not stated by 'his brothers'."""
    q = _q(must_not_contain=["his brother"])
    assert cp.must_not_contain(_rec("Both of his brothers stayed home."), q)[0]
    assert not cp.must_not_contain(_rec("He is his brother."), q)[0]


def test_must_not_contain_matches_a_possessive_the_fixture_writes_out() -> None:
    """A wrong fact that needs the possessive names it: 'Vasíli is Borís's godfather' (q027)."""
    q = _q(must_not_contain=["Vasíli is Borís's godfather"])
    assert not cp.must_not_contain(_rec("Prince Vasíli is Borís’s godfather."), q)[0]  # noqa: RUF001 - curly
    right = (
        "The son for whom Princess Drubetskáya petitioned Prince Vasíli is Borís, and his "
        "godfather is Count Cyril Vladímirovich Bezúkhov."
    )
    assert cp.must_not_contain(_rec(right), q)[0]


def test_names_are_supported_by_whole_words_only() -> None:
    """'Ivan' is not in 'Ivánovna' (H15); a possessive or the context's plural still supports."""
    ctx = "Márfa Ivánovna and the Rostóvs. Kahn's IMP."
    ok, why = cp.no_unsupported_names(_rec("Her father was Ivan.", context_text=ctx), _q())
    assert not ok and "Ivan" in why
    for answer in ("Márfa Ivánovna came.", "The Rostóv house.", "Kahn built it."):
        ok, why = cp.no_unsupported_names(_rec(answer, context_text=ctx), _q())
        assert ok, (answer, why)
    assert not cp._word_supported("ivan", "ivanovna")
    assert cp._word_supported("kahn's", "kahn")
    assert cp._word_supported("rostov", "the rostovs")


def test_other_is_not_a_name() -> None:
    """'Other' opened sentences and was supported only by 'mother' until scorer v6."""
    ok, why = cp.no_unsupported_names(
        _rec("Other children: none. Others are not named.", context_text="Kahn's mother."), _q()
    )
    assert ok, why


def test_answer_terms_fold_i_and_y_as_the_names_check_does() -> None:
    """'Vasily' names the term 'Vasíli' (H15); the names check accepts the same spelling."""
    ctx = "Prince Vasíli Kurágin lent the money at Radzivílov."
    q = _q(question="Who lent it?", answer_terms=["Vasíli"])
    rec = _rec("Prince Vasily Kuragin lent it at Radziwilov.", context_text=ctx)
    ok, why = cp.answers_from_graph(rec, q)
    assert ok, why
    ok, why = cp.no_unsupported_names(rec, q)
    assert ok, why
    alias = _q(question="Who lent it?", answer_terms=[("Vasíli", "the prince")])
    assert cp.answers_from_graph(rec, alias)[0]


def test_one_transliteration_fold_serves_terms_and_names() -> None:
    """The names check and the answer terms read i/y and w/v through the same fold."""
    pairs = [("vasily", "vasili"), ("radziwilov", "radzivilov"), ("yakov", "iakov")]
    for answer_word, context_word in pairs:
        assert cp._transliteration_fold(answer_word) == cp._transliteration_fold(context_word)
        assert cp._iy_variant_supported(answer_word, f"the {context_word} estate")
        assert cp._term_matches(f"the {answer_word} estate", context_word, answer_word)
    # a name's own final s is kept: 'boris' was 'bori' under rstrip("'s")
    assert cp._iy_variant_supported("borys", "boris came")


# --- audit wave 4: one answer normaliser for every check ---------------------------------


WAVE4_CTX = "The officer Nicholas Rostóv rode out. Prince Vasíli Kurágin stayed; Borís waited."


def test_the_normaliser_reads_through_every_markup_form() -> None:
    """Emphasis, code marks, citations, odd spaces and line breaks are gone; i/y folds."""
    a = cp.normalise_answer(
        "The officer is *Nicholas* [[cite:C1:S2|war_and_peace_book1.txt]],\u00a0\n"
        "**Prince** _Vasily_ `x` snake_case \u201cquoted\u201d Nicholas\u2019s"
    )
    assert "*" not in a.text and "`" not in a.text and "[[cite" not in a.text
    assert "snake_case" in a.text  # an underscore inside a word stays
    assert "\n" in a.folded  # the sentence splitter still sees the line break
    assert a.flat == ("the officer is nicholas , prince vasily x snake_case quoted nicholas's")
    assert a.transliterated == a.flat.replace("vasily", "vasili")


def test_a_number_and_its_unit_are_one_quantity_however_spaced() -> None:
    """GPT-OSS and Granite write '8\u202fK' and '64\u202f000' (the Ollama docs review, 2026-10-23).

    A narrow or plain space between a number and a unit sign ('K', 'k', 'M', 'G', '%', '+')
    and a narrow space between digit groups are typography, so '8 K' meets the term '8K'.
    """
    flat = cp.normalise_answer(
        "Set it to 8\u202fK tokens, or 64\u202fk; 64\u202f000 is 64 K, 75 % remains, 70 + tools."
    ).flat
    assert flat == "set it to 8k tokens, or 64k; 64000 is 64k, 75% remains, 70+ tools."
    # a word after the number, a sum, and a plain-spaced list of numbers stay apart
    kept = cp.normalise_answer("8 Kbps, 2 + 2, ports 11434 434 and 8 Mistral models").flat
    assert kept == "8 kbps, 2 + 2, ports 11434 434 and 8 mistral models"
    q = _q(answer_terms=[("8K", "8192")])
    assert cp.answers_from_graph(
        _rec("The default is 8\u202fK tokens [C0].", context_text="The default is 8K tokens."), q
    )[0]


def test_a_size_in_k_supports_the_number_it_writes() -> None:
    """'64k' in the docs is the 65536 an answer sets (the Ollama docs review, 2026-10-23).

    Models wrote 'OLLAMA_CONTEXT_LENGTH=65536' or '64000' where the context writes
    only '64k'; a size in k supports its binary and its decimal thousands.
    """
    q = _q(question="How do you set the context length?")
    context = "Set the context length to at least 64k tokens; small GPUs get 4K and 32k."
    for answer in (
        "Run OLLAMA_CONTEXT_LENGTH=65536 ollama serve.",
        "Set it to 65,536 tokens, or 4096 and 32768 on smaller cards.",
        "Run OLLAMA_CONTEXT_LENGTH=64000 ollama serve; 4000 and 32,000 on smaller cards.",
    ):
        ok, why = cp.no_unsupported_numbers(_rec(answer, context_text=context), q)
        assert ok, (answer, why)
    # another multiple, another size, and another unit stay unsupported
    for answer, number, ctx in (
        ("Set it to 64001.", "64001", context),
        ("Set it to 65537.", "65537", context),
        ("Set it to 131072.", "131072", context),
        ("It sends 8192 bits.", "8192", "8kb/s"),
    ):
        ok, why = cp.no_unsupported_numbers(_rec(answer, context_text=ctx), q)
        assert not ok and number in why, (answer, why)


def test_a_version_after_v_supports_the_number_without_it() -> None:
    """'v0.40.5' in the docs is the 'version 0.40.5' an answer writes (the Ollama docs review, 2026-10-23)."""
    q = _q(question="Which version is needed?")
    context = "Requires Ollama v0.40.5 or later; added in v0.13.3. Xcode v26.0, macOS Sonoma (v14)."
    for answer in (
        "You need version 0.40.5 or later.",
        "It was added in Ollama 0.13.3.",
        "Use Xcode 26.0 on macOS Sonoma (version 14).",
    ):
        ok, why = cp.no_unsupported_numbers(_rec(answer, context_text=context), q)
        assert ok, (answer, why)
    # a 'v' inside a word is no version prefix, and part of a version is not the version
    for answer, number, ctx in (
        ("It is model 14.", "14", "the llama3v14 build"),
        ("Use 0.40.", "0.40", context),
    ):
        ok, why = cp.no_unsupported_numbers(_rec(answer, context_text=ctx), q)
        assert not ok and number in why, (answer, why)


def test_generic_acronyms_keys_and_placeholders_are_not_names() -> None:
    """'URL', 'Ctrl + Shift + Esc' and 'VARIABLE_NAME' name nothing (the Ollama docs review, 2026-10-23).

    A short closed list of computing acronyms and keyboard keys, and an identifier made only
    of placeholder words; a real variable, another acronym and a mixed identifier stay names.
    'API' joined the list once a hyphenated compound was read by its capitalised parts:
    'OpenAI-compatible API' keeps 'OpenAI' as its supported word, and an unsupported head
    still fails it.
    """
    for answer in (
        "BBN built it; open its URL with the CLI or GUI, as JSON over HTTP or HTTPS.",
        "BBN built it behind an API, an IMP-compatible API.",
        "BBN built it; press Ctrl + Shift + Esc, then Alt + Tab, Enter or Cmd.",
        "BBN built it with VARIABLE_NAME=value, MY_VAR, YOUR_VARIABLE, KEY and VAR=value.",
    ):
        ok, why = cp.no_unsupported_names(_rec(answer), _q())
        assert ok, (answer, why)
    for answer, name in (
        ("BBN built it; set OLLAMA_HOST.", "OLLAMA_HOST"),
        ("BBN built it over TCP.", "TCP"),
        ("BBN built it behind a Raytheon-compatible API.", "Raytheon-compatible"),
        ("BBN built it; set MY_HOST.", "MY_HOST"),
    ):
        ok, why = cp.no_unsupported_names(_rec(answer), _q())
        assert not ok and name in why, (answer, why)


def test_a_hyphenated_compound_is_supported_by_its_capitalised_parts() -> None:
    """'OpenAI-compatible' is supported by 'OpenAI' (the Ollama docs review, 2026-10-23).

    A lower-case part of a hyphenated word is an English word ('-compatible', '-specific',
    '-based'), so only the capitalised parts are names to find; an unsupported head, and
    a compound of capitalised parts the context does not write, stay names.
    """
    for answer in (
        "BBN built an IMP-compatible, Honeywell-based machine.",
        "The UCLA-specific link ran on a DDP-516; it was ARPA-funded.",
    ):
        ok, why = cp.no_unsupported_names(_rec(answer), _q())
        assert ok, (answer, why)
    for answer, name in (
        ("BBN built a Raytheon-compatible machine.", "Raytheon-compatible"),
        ("BBN closed a Honeywell-Raytheon deal.", "Honeywell-Raytheon"),
    ):
        ok, why = cp.no_unsupported_names(_rec(answer), _q())
        assert not ok and name in why, (answer, why)


def test_must_not_contain_reads_single_emphasis_backticks_and_underscores() -> None:
    """'The officer is *Nicholas*' states 'officer is Nicholas' (it escaped until wave 4)."""
    q = _q(question="Who is the officer?", must_not_contain=["officer is Nicholas|"])
    for form in ("*Nicholas*", "`Nicholas`", "_Nicholas_", "**Nicholas**", "Nicholas\u00a0"):
        ok, why = cp.must_not_contain(_rec(f"The officer is {form}.", context_text=WAVE4_CTX), q)
        assert not ok, (form, why)
    ok, _ = cp.must_not_contain(_rec("The officer is *Nicholas's* friend."), q)
    assert ok  # the no-possessive marker still spares the possessive


def test_must_not_contain_folds_i_and_y_as_the_other_checks_do() -> None:
    """'Prince Vasily is Boris's godfather' states 'Vasíli is Borís's godfather'."""
    q = _q(question="Who is Borís's godfather?", must_not_contain=["Vasíli is Borís's godfather"])
    ok, why = cp.must_not_contain(_rec("Prince Vasily is Boris's godfather."), q)
    assert not ok, why
    ok, _ = cp.must_not_contain(_rec("Prince Vasily is not named as his godfather."), q)
    assert ok


def test_decoys_the_confusable_and_roles_fold_i_and_y() -> None:
    """The out-of-scope decoy, the fine-grained confusable and the role binding use the fold."""
    oos = _q(
        band="out_of_scope",
        question="Who bought the estate?",
        gold_entities=[],
        expect_refusal=True,
        decoy_entities=["Vasíli"],
    )
    ok, why = cp.declines_when_unsupported(_rec("The sources do not say; ask Vasily."), oos)
    assert not ok and "Vasíli" in why
    fine = _q(
        band="fine_grained_discrimination",
        question="Who stayed?",
        gold_entities=["Vasíli Kurágin"],
        wrong_entities=["Nicholas Rostóv"],
    )
    ok, why = cp.leads_with_gold(
        _rec("*Vasily* stayed, not Nicholas.", context_text=WAVE4_CTX), fine
    )
    assert ok, why


def test_every_check_reads_the_answer_through_the_normaliser() -> None:
    """A guard on the real checks: none reads ``rec["answer"]`` itself (only the citation report does)."""
    import inspect

    for name, check in cp.ChatProbeScorer.CHECKS.items():
        source = inspect.getsource(check)
        assert 'rec.get("answer")' not in source and 'rec["answer"]' not in source, name
    assert 'rec.get("answer")' in inspect.getsource(cp.cites_the_answer)


def test_the_ii_fold_leaves_roman_numerals_alone() -> None:
    """-ii folds to -i on a name ('Vasilii' = 'Vasíli'), never on a numeral ('XVII' is not 'XVI'; wave 6)."""
    from chaoscypher_core.benchmark.scorers.chat_probes import _transliteration_fold

    assert _transliteration_fold("vasilii") == "vasili"
    assert _transliteration_fold("vasiliy") == "vasili"
    assert _transliteration_fold("louis xvii") == "louis xvii"
    assert _transliteration_fold("alexander ii") == "alexander ii"
    assert _transliteration_fold("hawaii") == "havai"  # a word: w→v and -ii→-i like any other


def test_context_holds_terms_ignores_a_bare_word_beside_specific_members() -> None:
    """A single lowercase member of a group with specific members matches nothing on its own."""
    context = "He greeted the aunt and spoke of Her Majesty's health. Pierre bowed."
    assert (
        cp.context_holds_terms(
            context, ["Pierre", ["aunt", "left the aunt", "before she had finished"]]
        )
        is False
    )
    assert (
        cp.context_holds_terms(
            context + " He left the aunt.", ["Pierre", ("aunt", "left the aunt")]
        )
        is True
    )
    # a group of bare words only keeps them, and a lone string is its own group
    assert cp.context_holds_terms(context, [["aunt", "uncle"]]) is True
    assert cp.context_holds_terms(context, ["aunt"]) is True
    assert cp.context_holds_terms(context, ["Countess Rostóva"]) is False


def test_context_holds_terms_reads_numbers_compounds_and_curly_apostrophes() -> None:
    """A number or a hyphenated word is no bare member; a curly apostrophe is a straight one."""
    context = "I have only one twenty-five-ruble note, said the widow of Lord Edric\u2019s brother."
    assert cp.context_holds_terms(context, [("twenty-five", "twenty five", "25")]) is True
    assert cp.context_holds_terms("It cost 25 rubles.", [("twenty-five", "25")]) is True
    assert cp.context_holds_terms("She had fifty rubles.", [("fifty", "50")]) is True
    assert cp.context_holds_terms(context, [("Lady Tamsin", "Edric's brother")]) is True
    assert cp.context_holds_terms(context, [("Lady Tamsin", "brother")]) is False


def test_context_holds_terms_gates_an_alias_on_its_evidence_phrases() -> None:
    """A name the question file ties to evidence counts only where a phrase of that evidence is written (q029's shape)."""
    gate = {"Andrew": ["the wife of Prince Andrew", "appointed Prince Andrew as his aide-de-camp"]}
    terms = [["Andrew", "young Bolkónski"]]
    assert (
        cp.context_holds_terms("Prince Andrew rose and left.", terms, alias_evidence=gate) is False
    )
    assert (
        cp.context_holds_terms("She is the wife of Prince Andrew.", terms, alias_evidence=gate)
        is True
    )
    assert cp.context_holds_terms("The young Bolkónski rose.", terms, alias_evidence=gate) is True
