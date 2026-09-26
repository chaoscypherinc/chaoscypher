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
    assert cp.answers_from_graph(_rec("The researcher was Leonard Kleinrock."), q)[0]
    ok, why = cp.answers_from_graph(_rec("The researcher was Douglas Engelbart at UCLA."), q)
    assert not ok and "Leonard Kleinrock" in why
    first = _q(question="What was the first message?", gold_entities=["Charley Kline"])
    assert cp.answers_from_graph(_rec("The first message was 'lo'."), _replace(first, ["lo"]))[0]
    assert not cp.answers_from_graph(
        _rec("The first message was 'login'."), _replace(first, ["lo"])
    )[0]
    # every term is required
    two = _q(answer_terms=["TCP/IP", "1983"])
    assert cp.answers_from_graph(_rec("TCP/IP replaced it in 1983."), two)[0]
    assert not cp.answers_from_graph(_rec("TCP/IP replaced it."), two)[0]


def _replace(q: LabeledQuery, terms: list[str | tuple[str, ...]]) -> LabeledQuery:
    return replace(q, answer_terms=terms)


SISTER = _q(
    question="Who does Anatole try to seduce?",
    gold_entities=["Mary Bolkonskaya"],
    answer_terms=[("Mary", "Andrew's sister")],
)


def test_answers_from_graph_alias_group_passes_on_second_member() -> None:
    """An any-of group is satisfied by its second member alone."""
    assert cp.answers_from_graph(_rec("He courts Prince Andrew's sister."), SISTER)[0]


def test_answers_from_graph_alias_member_needs_the_whole_phrase() -> None:
    """A group member matches as a whole phrase, never by one word of it."""
    assert not cp.answers_from_graph(_rec("He courts his sister."), SISTER)[0]
    assert not cp.answers_from_graph(_rec("He courts the prince's sister."), SISTER)[0]
    lise = _replace(SISTER, [("Lise", "little princess")])
    assert not cp.answers_from_graph(_rec("It was the princess."), lise)[0]
    assert not cp.answers_from_graph(_rec("It was Princess Mary Bolk\u00f3nskaya."), lise)[0]
    assert cp.answers_from_graph(_rec("It was the youthful little princess."), lise)[0]
    assert cp.answers_from_graph(_rec("Lise, his wife."), lise)[0]


def test_answers_from_graph_single_word_alias_is_word_bounded() -> None:
    """A one-word member does not match a longer word that starts with it."""
    q = _replace(SISTER, [("Bolk\u00f3nski",)])
    assert not cp.answers_from_graph(_rec("Mary Bolk\u00f3nskaya."), q)[0]
    assert cp.answers_from_graph(_rec("Old Prince Bolk\u00f3nski."), q)[0]


def test_answers_from_graph_plain_term_stays_loose() -> None:
    """A plain string term still accepts any significant word of it."""
    q = _replace(SISTER, ["Andrew's sister"])
    assert cp.answers_from_graph(_rec("He courts his sister."), q)[0]


def test_answers_from_graph_alias_group_fails_when_no_member_named() -> None:
    """No member named: the group fails and is reported pipe-joined."""
    ok, why = cp.answers_from_graph(_rec("He courts Natasha."), SISTER)
    assert not ok
    assert "Mary|Andrew's sister" in why


def test_answers_from_graph_curly_apostrophe_matches_straight_alias() -> None:
    """'Andrew\u2019s' in the answer matches an alias spelled with a straight apostrophe."""
    assert cp.answers_from_graph(_rec("He courts Prince Andrew\u2019s sister."), SISTER)[0]
    # a plain possessive term, straightened the same way
    possessive = _replace(SISTER, ["Andrew's"])
    assert cp.answers_from_graph(_rec("It was Prince Andrew\u2019s estate."), possessive)[0]
    assert not cp.answers_from_graph(_rec("It was the old prince's estate."), possessive)[0]


def test_answers_from_graph_mixed_string_and_group_terms() -> None:
    """A plain string term beside a group is still required."""
    q = _replace(SISTER, ["Kur\u00e1gin", ("Mary", "Andrew's sister")])
    assert cp.answers_from_graph(_rec("Anatole Kuragin courts Mary."), q)[0]
    ok, why = cp.answers_from_graph(_rec("Anatole courts Mary."), q)
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
