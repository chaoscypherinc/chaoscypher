# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""question_shape: cues, the rules, the router reply, the route."""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.question_shapes import named_mention_count, question_parts, question_tokens
from chaoscypher_core.services.workflows.tools.engine.handlers.question_shape import (
    AGGREGATE_CUES,
    QuestionShape,
    ShapeDecision,
    classify_by_rules,
    parse_router_reply,
    reply_content,
    route,
    shape_cues,
)
from chaoscypher_core.utils.retrieval_faults import collect_retrieval_faults


def test_cues_match_whole_words_and_phrases_folded() -> None:
    cues = shape_cues("How is Dólokhov CONNECTED to Pierre?")
    assert cues["path"] == "connected" and cues["aggregate"] is None
    assert shape_cues("Which of the Rostóv children are named?")["aggregate"] in AGGREGATE_CUES
    assert shape_cues("the disconnected lamp")["path"] is None  # not a whole word


@pytest.mark.parametrize(
    ("query", "named", "relations", "expected"),
    [
        ("How is Dólokhov connected to Pierre?", 2, [], QuestionShape.PATH),
        # a relation word between two named entities is a relational lookup, never a path
        # (until 2026-10-09 it was: 33 of the pack's 34 rules misroutes)
        ("Who did Lise marry?", 2, ["spouse"], QuestionShape.LOOKUP),
        ("What did Pierre's father leave to Pierre?", 2, ["parent"], QuestionShape.LOOKUP),
        ("Which of the Rostóv children are named in the text?", 1, [], QuestionShape.AGGREGATE),
        ("What number of sons does Prince Vasíli have?", 1, [], QuestionShape.AGGREGATE),
        # "how many" is no count cue: the text states this count outright (fixture q013).
        ("How many children had the Countess Rostóva borne?", 1, [], QuestionShape.LOOKUP),
        ("What are the main themes of the book?", 0, [], QuestionShape.GLOBAL),
        ("What did Pierre say at the party?", 1, [], QuestionShape.LOOKUP),
        # Rule 1 comes before rule 4's global abstain: a path wins over a global cue.
        ("What is the central relationship between Pierre and Andrew?", 2, [], QuestionShape.PATH),
    ],
)
def test_the_rules_decide_the_clear_cases(
    query: str, named: int, relations: list[str], expected: QuestionShape
) -> None:
    decision = classify_by_rules(query, named_seeds=named, relations=relations)
    assert decision is not None and decision.shape is expected and decision.source == "rules"


@pytest.mark.parametrize(
    ("query", "named", "relations"),
    [
        ("Dólokhov and Pierre?", 2, []),  # two names, no cue, no relation: review focus 1
        # a relation word and a global cue with named entities: the model decides
        ("Who did Lise marry, in general?", 2, ["spouse"]),
        ("Which guests are connected to Anna Pávlovna?", 1, []),  # aggregate and path cues
        ("What are the main themes around Pierre?", 1, []),  # global cue with a named seed
        ("Which people shape the main themes around Pierre?", 1, []),  # aggregate and global cues
    ],
)
def test_the_rules_abstain_on_the_ambiguous_cases(
    query: str, named: int, relations: list[str]
) -> None:
    assert classify_by_rules(query, named_seeds=named, relations=relations) is None


def test_parse_router_reply_takes_the_first_qualifying_json_object() -> None:
    got = parse_router_reply('Sure. {"shape": "path", "reason": "two people linked"} trailing')
    assert got == ShapeDecision(QuestionShape.PATH, "two people linked", "llm")
    skipped = parse_router_reply('{"note": 1} {"shape": "path", "reason": "x"}')
    assert skipped == ShapeDecision(QuestionShape.PATH, "x", "llm")
    assert parse_router_reply('{"shape": "sideways", "reason": "x"}') is None
    assert parse_router_reply("not json") is None
    assert parse_router_reply('{"shape": "lookup"}') is None  # a reason is required
    # the shape compared trimmed and lowercased: a model's "Path" is path
    assert parse_router_reply('{"shape": " Path ", "reason": "x"}') == ShapeDecision(
        QuestionShape.PATH, "x", "llm"
    )
    assert parse_router_reply('[1] {"shape": "GLOBAL", "reason": "y"}') == ShapeDecision(
        QuestionShape.GLOBAL, "y", "llm"
    )


def test_the_rule_reasons_name_what_fired() -> None:
    decision = classify_by_rules("Who did Lise marry?", named_seeds=2, relations=["spouse"])
    assert decision == ShapeDecision(QuestionShape.LOOKUP, "no cue and spouse", "rules")
    single = classify_by_rules("Who did Lise marry?", named_seeds=1, relations=["spouse"])
    assert single == ShapeDecision(QuestionShape.LOOKUP, "no cue", "rules")


def test_question_tokens_break_at_punctuation_and_compose_accents() -> None:
    assert question_tokens("Pierre, Andrew and Anna Pávlovna's friend?") == [
        "Pierre",
        None,
        "Andrew",
        "and",
        "Anna",
        "Pávlovna's",
        "friend",
    ]
    decomposed = "Bezúkhov"  # NFD: a combining accent
    assert question_tokens(decomposed) == ["Bezúkhov"]


def test_a_mention_is_a_run_of_naming_words_counted_once() -> None:
    """The rules' count: mentions, not the nodes they match, nor each word of a full name."""
    names = {"anna", "pavlovna", "pierre", "andrew", "rostov", "natasha", "nicholas"}.__contains__
    assert named_mention_count(["anna", "pavlovna"], names) == 1
    assert named_mention_count(["pierre", None, "andrew"], names) == 2
    assert named_mention_count(["anna", "pavlovna", None, "anna"], names) == 1  # part, again
    assert named_mention_count(["rostov"], names) == 1  # however many nodes it names
    # two people sharing a surname are two mentions
    assert named_mention_count(["natasha", "rostov", None, "nicholas", "rostov"], names) == 2
    # a word that names nothing breaks a mention
    assert named_mention_count(["pierre", "friend", "andrew"], names) == 2
    assert named_mention_count([], names) == 0


@pytest.mark.asyncio
async def test_route_modes_and_the_fault_on_a_failed_model() -> None:
    rules = ShapeDecision(QuestionShape.AGGREGATE, "which", "rules")

    async def says_path(messages: list[dict[str, Any]], **_: Any) -> dict[str, Any]:
        assert "Question:" in messages[-1]["content"]
        return {"content": '{"shape": "path", "reason": "connected"}'}

    async def broken(messages: list[dict[str, Any]], **_: Any) -> dict[str, Any]:
        raise RuntimeError("model down")

    assert (await route("q", mode="off", rules=rules, chat=says_path)).shape is QuestionShape.LOOKUP
    assert (await route("q", mode="rules", rules=rules, chat=says_path)) == rules
    assert (await route("q", mode="rules_then_llm", rules=rules, chat=says_path)) == rules
    llm = await route("q", mode="rules_then_llm", rules=None, chat=says_path)
    assert llm.shape is QuestionShape.PATH and llm.source == "llm"
    with collect_retrieval_faults() as faults:
        fallback = await route("q", mode="llm", rules=rules, chat=broken)
    assert fallback == rules and faults == ["router"]
    with collect_retrieval_faults() as faults:
        none = await route("q", mode="rules_then_llm", rules=None, chat=None)
    assert none.shape is QuestionShape.LOOKUP and none.source == "default" and faults == []


def test_reply_content_reads_a_dict_or_a_provider_response_and_refuses_anything_else() -> None:
    """A ``ChatCallback`` reply is ``{"content": ...}`` or an object with ``.content``; nothing else."""
    from types import SimpleNamespace

    from chaoscypher_core.exceptions import LLMError
    from chaoscypher_core.models import LLMChatResponse

    assert reply_content({"content": "a", "usage": None}) == "a"
    assert reply_content({"content": None}) == ""
    assert reply_content(LLMChatResponse(content="b", provider="ollama")) == "b"
    assert reply_content(SimpleNamespace(content="c")) == "c"
    for bad in ("text", {"text": "d"}, SimpleNamespace(content=3), None):
        with pytest.raises(LLMError, match=type(bad).__name__):
            reply_content(bad)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "Which guest at Anna Pávlovna's called the execution a political necessity, and what "
            "was that guest's first breach of manners earlier in the evening?",
            [
                "Which guest at Anna Pávlovna's called the execution a political necessity?",
                "what was that guest's first breach of manners earlier in the evening?",
            ],
        ),
        (  # ``and`` before an interrogative, no comma, case folded
            "What nickname was Princess Mary's father given AND What did Prince Andrew say at supper?",
            [
                "What nickname was Princess Mary's father given?",
                "What did Prince Andrew say at supper?",
            ],
        ),
        (  # a semicolon parts too
            "Who commanded the squadron; where was it quartered that winter?",
            ["Who commanded the squadron?", "where was it quartered that winter?"],
        ),
        (  # ``and`` joining two names is no part
            "How are Dólokhov and Pierre connected, and Anatole?",
            ["How are Dólokhov and Pierre connected, and Anatole?"],
        ),
        (  # a short tail stays with its question (PART_MIN_WORDS)
            "Which men in the text are going to the war, and in what capacity each?",
            ["Which men in the text are going to the war, and in what capacity each?"],
        ),
        ("Who did Lise marry?", ["Who did Lise marry?"]),
    ],
)
def test_question_parts_splits_where_and_opens_a_new_question(
    query: str, expected: list[str]
) -> None:
    assert question_parts(query) == expected
