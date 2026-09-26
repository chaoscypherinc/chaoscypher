# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage for load_queries error branches not hit by test_queries.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_cli.benchmark.queries import load_queries


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "queries.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_top_level_not_mapping_raises_type_error(tmp_path: Path) -> None:
    p = _write(tmp_path, "- a\n- b\n")
    with pytest.raises(TypeError, match="must be a YAML mapping"):
        load_queries(p)


def test_missing_top_level_keys_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "version: '1.0'\n")
    with pytest.raises(ValueError, match="missing required top-level keys"):
        load_queries(p)


def test_query_entry_not_mapping_raises_type_error(tmp_path: Path) -> None:
    p = _write(tmp_path, "version: '1.0'\nqueries:\n  - just-a-string\n")
    with pytest.raises(TypeError, match=r"queries\[0\] is not a mapping"):
        load_queries(p)


def test_query_missing_required_field_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "version: '1.0'\nqueries:\n  - {id: q1, band: out_of_scope}\n",
    )
    with pytest.raises(ValueError, match="missing required field 'question'"):
        load_queries(p)


def test_out_of_scope_rejects_gold_answer(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "version: '1.0'\n"
        "queries:\n"
        "  - id: q1\n"
        "    band: out_of_scope\n"
        "    question: q?\n"
        "    gold_answer: nope\n"
        "    expect_refusal: true\n",
    )
    with pytest.raises(ValueError, match="out_of_scope must not set gold_answer"):
        load_queries(p)


def test_out_of_scope_requires_expect_refusal(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "version: '1.0'\nqueries:\n  - id: q1\n    band: out_of_scope\n    question: q?\n",
    )
    with pytest.raises(ValueError, match="must set expect_refusal: true"):
        load_queries(p)


def test_in_scope_rejects_expect_refusal(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "version: '1.0'\n"
        "queries:\n"
        "  - id: q1\n"
        "    band: factual_single_hop\n"
        "    question: q?\n"
        "    gold_entities: [X]\n"
        "    gold_answer: a\n"
        "    expect_refusal: true\n",
    )
    with pytest.raises(ValueError, match="expect_refusal only allowed on out_of_scope"):
        load_queries(p)


def test_answer_terms_round_trip(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "version: '1.0'\n"
        "queries:\n"
        "  - id: q1\n"
        "    band: factual_single_hop\n"
        "    question: What was the first message?\n"
        "    gold_entities: [ARPANET, Charley Kline]\n"
        "    answer_terms: [lo]\n"
        "    gold_answer: lo\n"
        "  - id: q2\n"
        "    band: factual_single_hop\n"
        "    question: Who?\n"
        "    gold_entities: [X]\n"
        "    gold_answer: X\n",
    )
    qs = load_queries(p)
    assert qs.queries[0].answer_terms == ["lo"]
    assert qs.queries[0].gold_entities == ["ARPANET", "Charley Kline"]
    assert qs.queries[1].answer_terms == []


def _terms_fixture(tmp_path: Path, terms: str) -> Path:
    return _write(
        tmp_path,
        "version: '1.0'\n"
        "queries:\n"
        "  - id: q1\n"
        "    band: factual_single_hop\n"
        "    question: Who?\n"
        "    gold_entities: [X]\n"
        "    gold_answer: X\n"
        "  - id: q2\n"
        "    band: factual_single_hop\n"
        "    question: Whom does Anatole court?\n"
        "    gold_entities: [Mary]\n"
        "    gold_answer: Mary\n"
        f"    answer_terms: {terms}\n",
    )


def test_answer_terms_list_item_becomes_alias_tuple(tmp_path: Path) -> None:
    p = _terms_fixture(tmp_path, '[Kur\u00e1gin, [Mary, "Andrew\'s sister"]]')
    assert load_queries(p).queries[1].answer_terms == ["Kur\u00e1gin", ("Mary", "Andrew's sister")]


@pytest.mark.parametrize(
    ("terms", "match"),
    [
        ("[Kuragin, []]", r"queries\[1\] answer_terms has an empty alias group"),
        ("[[Mary, [Andrew]]]", r"queries\[1\] answer_terms alias groups must not be nested"),
        ("[[Mary, 1805]]", r"queries\[1\] answer_terms alias group member 1805 is not a string"),
    ],
)
def test_answer_terms_bad_alias_group_raises(tmp_path: Path, terms: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        load_queries(_terms_fixture(tmp_path, terms))


def test_every_shipped_queries_fixture_loads() -> None:
    import chaoscypher_cli.benchmark as bench

    fixtures = sorted((Path(bench.__file__).parent / "data" / "datasets").glob("*/queries.yaml"))
    assert fixtures
    for f in fixtures:
        for q in load_queries(f).queries:
            assert all(isinstance(t, (str, tuple)) for t in q.answer_terms)


def test_shipped_fixture_has_answer_terms_on_every_in_scope_query() -> None:
    import chaoscypher_cli.benchmark as bm

    path = (
        Path(bm.__file__).parent / "data" / "datasets" / "tech_encyclopedia_tiny" / "queries.yaml"
    )
    qs = load_queries(path)
    in_scope = [q for q in qs.queries if q.band != "out_of_scope"]
    assert len(in_scope) == 68
    missing = [q.id for q in in_scope if not q.answer_terms]
    assert not missing, missing
    assert all(not q.answer_terms for q in qs.queries if q.band == "out_of_scope")


def _shipped_fixture_dir() -> Path:
    import chaoscypher_cli.benchmark as bm

    return Path(bm.__file__).parent / "data" / "datasets" / "tech_encyclopedia_tiny"


def test_shipped_fixture_is_labelled_for_the_chat_probes() -> None:
    """>= 80 queries; every band carries the fields its checks need."""
    qs = load_queries(_shipped_fixture_dir() / "queries.yaml")
    assert len(qs.queries) >= 80
    assert [q.id for q in qs.queries] == [f"q{i:03d}" for i in range(1, len(qs.queries) + 1)]
    in_scope = [q for q in qs.queries if q.band != "out_of_scope"]
    assert all(q.answer_terms for q in in_scope)
    oos = [q for q in qs.queries if q.band == "out_of_scope"]
    # q047 and q049 predate decoys: nothing famous to tempt the model with
    assert [q.id for q in oos if not q.decoy_entities] == ["q047", "q049"]
    fine = [q for q in qs.queries if q.band == "fine_grained_discrimination"]
    # q044 asks for both employers, so neither is the confusable one
    assert [q.id for q in fine if not q.wrong_entities] == ["q044"]
    assert sum(1 for q in qs.queries if q.must_not_contain) >= 20
    # a model echoing the question must not trip its own wrong-fact list
    echoed = [
        (q.id, t) for q in qs.queries for t in q.must_not_contain if t.lower() in q.question.lower()
    ]
    assert not echoed, echoed


def test_must_not_contain_round_trips(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "version: '1.0'\n"
        "queries:\n"
        "  - id: q1\n"
        "    band: factual_single_hop\n"
        "    question: What was NCP?\n"
        "    gold_entities: [NCP]\n"
        "    answer_terms: [Program]\n"
        "    must_not_contain: [Network Control Protocol, 1963]\n"
        "    gold_answer: The Network Control Program.\n"
        "  - id: q2\n"
        "    band: out_of_scope\n"
        "    question: Who?\n"
        "    expect_refusal: true\n",
    )
    qs = load_queries(p)
    assert qs.queries[0].must_not_contain == ["Network Control Protocol", "1963"]
    assert qs.queries[1].must_not_contain == []


def test_gold_answers_pass_their_own_chat_checks() -> None:
    """Scored against the whole corpus, every gold answer passes its own terms and numbers.

    Guards the fixture against self-contradiction: an answer term the gold
    answer lacks, a must_not_contain term it states, or a number that is not
    in the corpus.
    """
    from chaoscypher_core.benchmark.scorers import chat_probes as cp

    fixture = _shipped_fixture_dir()
    corpus = (fixture / "tech_encyclopedia_tiny.txt").read_text(encoding="utf-8")
    failures = []
    for q in load_queries(fixture / "queries.yaml").queries:
        if q.band == "out_of_scope":
            continue
        rec = {"answer": q.gold_answer, "finish_reason": "stop", "context_text": corpus}
        for check in (cp.answers_from_graph, cp.must_not_contain, cp.no_unsupported_numbers):
            ok, why = check(rec, q)
            if not ok:
                failures.append(f"{q.id} {check.__name__}: {why}")
    assert not failures, failures


# ---------------------------------------------------------------------------
# war_and_peace_book1: a corpus much larger than one question's retrieval window
# ---------------------------------------------------------------------------


def _book1_dir() -> Path:
    """The shipped war_and_peace_book1 dataset directory."""
    import chaoscypher_cli.benchmark as bm

    return Path(bm.__file__).parent / "data" / "datasets" / "war_and_peace_book1"


def _book1_corpus() -> str:
    """The Book One corpus text."""
    return (_book1_dir() / "war_and_peace_book1.txt").read_text(encoding="utf-8")


def test_book1_corpus_is_far_larger_than_a_retrieval_window() -> None:
    """90k-130k chars: retrieval returns a few thousand, so it cannot return the whole text."""
    assert 90_000 <= len(_book1_corpus()) <= 130_000


def test_book1_fixture_is_labelled_for_the_chat_probes() -> None:
    """80 contiguous queries in the planned band mix; every band has the fields its checks need."""
    from collections import Counter

    qs = load_queries(_book1_dir() / "queries.yaml")
    assert len(qs.queries) == 80
    assert [q.id for q in qs.queries] == [f"q{i:03d}" for i in range(1, 81)]
    assert Counter(q.band for q in qs.queries) == {
        "factual_single_hop": 15,
        "paraphrase": 10,
        "multi_hop": 20,
        "fine_grained_discrimination": 20,
        "out_of_scope": 15,
    }
    assert all(q.answer_terms for q in qs.queries if q.band != "out_of_scope")
    assert all(q.decoy_entities for q in qs.queries if q.band == "out_of_scope")
    fine = [q for q in qs.queries if q.band == "fine_grained_discrimination"]
    assert all(q.wrong_entities for q in fine)
    traps = [q for q in qs.queries if q.band == "factual_single_hop" and q.must_not_contain]
    assert len(traps) == 5
    # a model echoing the question must not trip its own wrong-fact list
    echoed = [
        (q.id, t) for q in qs.queries for t in q.must_not_contain if t.lower() in q.question.lower()
    ]
    assert not echoed, echoed


def test_book1_decoys_are_not_in_the_corpus() -> None:
    """An out-of-scope question is only out of scope if its tempting answer is absent."""
    from chaoscypher_core.benchmark.scorers.probes import _fold

    corpus = _fold(_book1_corpus())
    qs = load_queries(_book1_dir() / "queries.yaml")
    present = [
        (q.id, d)
        for q in qs.queries
        if q.band == "out_of_scope"
        for d in q.decoy_entities
        if _fold(d) in corpus
    ]
    assert not present, present


def test_book1_gold_answers_pass_their_own_chat_checks() -> None:
    """Scored against the whole corpus, every gold answer passes its own checks.

    Fine-grained gold answers must also lead with the gold entity, or the
    fixture would fail its own right answer.
    """
    from chaoscypher_core.benchmark.scorers import chat_probes as cp

    corpus = _book1_corpus()
    failures = []
    for q in load_queries(_book1_dir() / "queries.yaml").queries:
        if q.band == "out_of_scope":
            continue
        rec = {"answer": q.gold_answer, "finish_reason": "stop", "context_text": corpus}
        checks = [cp.answers_from_graph, cp.must_not_contain, cp.no_unsupported_numbers]
        if q.band == "fine_grained_discrimination":
            checks.append(cp.leads_with_gold)
        for check in checks:
            ok, why = check(rec, q)
            if not ok:
                failures.append(f"{q.id} {check.__name__}: {why}")
    assert not failures, failures
