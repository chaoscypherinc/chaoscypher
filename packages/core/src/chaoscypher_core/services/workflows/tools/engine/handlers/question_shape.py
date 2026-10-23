# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The shape of a question, as ``graphrag_search`` decides it: lookup, aggregate, path or global.

Rules first (free, deterministic: the question's named entities and cue words), the
chat model only when the rules abstain (``GraphRAGSettings.router``). The shape sets
the retrieval's budget and rendering (``shape_levers``), never whether the graph is
used.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import Any, Literal

import structlog

from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.services.sources.engine.deduplication.name_identity import fold
from chaoscypher_core.utils.retrieval_faults import record_retrieval_fault


logger = structlog.get_logger(__name__)

ChatCallback = Callable[..., Awaitable[dict[str, Any]]]
"""A tool's chat model: ``await chat(messages, temperature=..., max_tokens=...)`` returning ``{"content": str, ...}``.

What the tool executor hands its handlers (the shape router, the summarize
tool); the producers build one with
:mod:`~chaoscypher_core.services.workflows.tools.engine.chat_callbacks`.
Its reply is read with :func:`reply_content`.
"""


def reply_content(reply: object) -> str:
    """The text of a chat model's reply: a ``{"content": ...}`` dict's, or a provider response's ``.content``.

    The one reading of a :data:`ChatCallback` reply. A provider's
    ``LLMChatResponse`` is accepted too: until 2026-10-09 the Neuron worker's
    callback returned one, and ``reply.get("content")`` failed every router
    and summarize call there. A None content is the empty text.

    Raises:
        LLMError: When the reply is neither (a string, a dict without
            ``content``, a content that is not text), naming the reply's type.
    """
    if isinstance(reply, dict):
        content = reply.get("content", _MISSING)
    else:
        content = getattr(reply, "content", _MISSING)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    msg = (
        f"A chat callback answered a {type(reply).__name__}, not a {{'content': str}} dict "
        "or a response with a text content"
    )
    raise LLMError(msg, code="LLM_REPLY_SHAPE", details={"reply_type": type(reply).__name__})


_MISSING = object()
"""What :func:`reply_content` reads when the reply carries no ``content`` at all."""


class QuestionShape(StrEnum):
    """The kind of answer a question wants from the graph."""

    LOOKUP = "lookup"
    AGGREGATE = "aggregate"
    PATH = "path"
    GLOBAL = "global"


@dataclass(frozen=True)
class ShapeDecision:
    """A routed shape: the shape, why, and who decided it (``rules``, ``llm`` or ``default``)."""

    shape: QuestionShape
    reason: str
    source: Literal["rules", "llm", "default"]


# "which X" over a plural noun asks for a list; "which of the X" and a bare "which" pick one
# (the fixture's fine_grained and disambiguation questions), so neither is a cue here.
AGGREGATE_CUES: frozenset[str] = frozenset(
    {
        "every",
        "list",
        "name the",
        "names of",
        "everyone",
        "anyone",
        "guests",
        "which people",
        "which men",
        "which women",
        "which characters",
        "which guests",
        "which couples",
        "which two",
        "which three",
        "are named",
        "are officers",
    }
)
"""Folded words and two-word phrases of a question that asks for everyone or everything sharing a property."""

COUNT_CUES: frozenset[str] = frozenset({"number of"})
"""Folded phrases of a question that counts a list (an aggregate).

Not ``how many``: the text often states the count outright ("How many children
had the Countess Rostóva borne?" is a lookup of "twelve").
"""

PATH_CUES: frozenset[str] = frozenset(
    {
        "connected",
        "connection",
        "related",
        "relationship",
        "relation",
        "linked",
        "link",
        "between",
        "how is",
        "how are",
        "through whom",
        "tie",
        "ties",
    }
)
"""Folded words and two-word phrases of a question that asks how two named things are connected."""

GLOBAL_CUES: frozenset[str] = frozenset(
    {
        "theme",
        "themes",
        "overall",
        "summarize",
        "summary",
        "factions",
        "groups",
        "story about",
        "in general",
        "central",
        "what happens",
    }
)
"""Folded words and two-word phrases of a question about the big picture across the whole text."""

ROUTER_MAX_TOKENS = 60
"""The router reply's token cap: one short JSON object."""

REASON_CHARS = 160
"""The longest router reason kept; a longer one is cut."""

SHAPE_ROUTER_PROMPT = (
    "Classify the question into exactly one shape. Reply with one JSON object and nothing else:\n"
    '{{"shape": "<lookup|aggregate|path|global>", "reason": "<ten words at most>"}}\n'
    "Shapes: lookup = a fact about one thing, answered by a passage; aggregate = everyone or "
    "everything that shares a property, relation or scene (a list); path = how two named things "
    "are connected to each other; global = the big picture across the whole text (themes, "
    "factions, what happens overall).\n"
    "Question: {query}"
)
"""The router prompt, formatted with ``{query}``: the model answers ``{"shape", "reason"}``."""

ROUTER_OFF_REASON = "router off"
"""The ``shape_reason`` of a lookup by default because ``GraphRAGSettings.router`` is ``off``.

A question routed so was never routed, so a miss on it is no route miss
(``chat_miss_classifier.py`` reads this reason).
"""

_SHAPE_VALUES: frozenset[str] = frozenset(s.value for s in QuestionShape)


def _match(folded_words: list[str], cues: frozenset[str]) -> str | None:
    """Return the first cue (a word or a two-word phrase) the folded words contain, else None."""
    singles = set(folded_words)
    pairs = {f"{a} {b}" for a, b in pairwise(folded_words)}
    for cue in sorted(cues):  # sorted: a stable answer when several match
        if cue in singles or cue in pairs:
            return cue
    return None


def shape_cues(query: str) -> dict[str, str | None]:
    """Return the cue each shape found in the question, by shape name (``count`` is aggregate's second list).

    Matching is on the question's folded whole words (:func:`fold`'s words,
    hyphenated words whole) and its two-word phrases.
    """
    words = fold(query).split()
    return {
        "aggregate": _match(words, AGGREGATE_CUES),
        "count": _match(words, COUNT_CUES),
        "path": _match(words, PATH_CUES),
        "global": _match(words, GLOBAL_CUES),
    }


def classify_by_rules(
    query: str, *, named_seeds: int, relations: Sequence[str]
) -> ShapeDecision | None:
    """Return the rules' shape for a question, or None when they abstain.

    In order, the first that fires wins:

    1. path: two named entities and a path cue;
    2. aggregate: a named entity and an aggregate or count cue, and no global cue;
    3. global: no named entity and a global cue;
    4. abstain: an aggregate and a path cue together (rules 1 and 2 both
       firing, checked first); a global cue with a named entity and no path
       (with an aggregate cue too, the model decides); any other cue no rule
       above takes (a path cue with one named entity, an aggregate cue with
       none); or two named entities with no cue and no relation word;
    5. lookup: no cue, and at most one named entity or a relation word (a
       relational lookup: "Who did Lise marry?", "Pierre's father").

    A relation word never makes a path (until 2026-10-09 it did: 33 of the
    pack's 34 rules misroutes were relational lookups such as "Pierre's
    father" routed as "how are they connected"); the relations remain the
    aggregate block's families.

    Args:
        query: The question.
        named_seeds: How many things the question names among its seeds
            (``question_shapes.named_mention_count``).
        relations: Relation words the question names (``spouse``), if any
            (``graphrag_handlers.question_relations``).
    """
    cues = shape_cues(query)
    aggregate_cue = cues["aggregate"] or cues["count"]
    path_cue = cues["path"]
    global_cue = cues["global"]
    if aggregate_cue and path_cue:  # rules 1 and 2 would both fire: abstain
        return None
    if named_seeds >= 2 and path_cue:
        return ShapeDecision(QuestionShape.PATH, f"two named entities and {path_cue}", "rules")
    if named_seeds >= 1 and aggregate_cue and not global_cue:
        return ShapeDecision(
            QuestionShape.AGGREGATE, f"a named entity and {aggregate_cue}", "rules"
        )
    if named_seeds == 0 and global_cue:
        return ShapeDecision(QuestionShape.GLOBAL, f"no named entity and {global_cue}", "rules")
    if global_cue or aggregate_cue or path_cue or (named_seeds >= 2 and not relations):
        return None
    reason = f"no cue and {relations[0]}" if named_seeds >= 2 else "no cue"
    return ShapeDecision(QuestionShape.LOOKUP, reason, "rules")


def parse_router_reply(text: str) -> ShapeDecision | None:
    """Return the model's ``{"shape", "reason"}`` as a decision: the first JSON object in the text that qualifies.

    An object qualifies with a known ``shape`` (compared trimmed and
    lowercased: ``"Path"`` is ``path``) and a string ``reason``; one that
    does not (``{"note": 1}``) is skipped. None when no object qualifies;
    the reason is cut to :data:`REASON_CHARS`.
    """
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, end = decoder.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        if isinstance(obj, dict):
            shape, reason = obj.get("shape"), obj.get("reason")
            shape = shape.strip().lower() if isinstance(shape, str) else None
            if shape in _SHAPE_VALUES and isinstance(reason, str):
                return ShapeDecision(QuestionShape(shape), reason.strip()[:REASON_CHARS], "llm")
        start = text.find("{", end)
    return None


async def route(
    query: str, *, mode: str, rules: ShapeDecision | None, chat: ChatCallback | None
) -> ShapeDecision:
    """Return the shape ``graphrag_search`` runs with, under the router mode.

    ``off`` is lookup; ``rules`` is the rules' decision or lookup; ``llm`` asks
    the model and ``rules_then_llm`` asks it only when the rules abstained. A
    model that fails or answers nothing usable falls back to the rules or
    lookup and records the ``router`` retrieval fault; no model at all (the
    benchmark's rules-only runs) is lookup without a fault.

    Args:
        query: The question.
        mode: ``GraphRAGSettings.router``.
        rules: :func:`classify_by_rules`' decision, None when it abstained.
        chat: The chat model, None when there is none.
    """
    default = ShapeDecision(
        QuestionShape.LOOKUP, ROUTER_OFF_REASON if mode == "off" else "no decision", "default"
    )
    if mode == "off":
        return default
    if mode == "rules" or (mode == "rules_then_llm" and rules is not None):
        return rules or default
    if chat is None:
        return rules or ShapeDecision(QuestionShape.LOOKUP, "no router model", "default")
    try:
        reply = await chat(
            [{"role": "user", "content": SHAPE_ROUTER_PROMPT.format(query=query)}],
            temperature=0.0,
            max_tokens=ROUTER_MAX_TOKENS,
        )
        decision = parse_router_reply(reply_content(reply))
    except Exception:
        logger.warning("shape_router_failed", exc_info=True)
        decision = None
    if decision is None:
        record_retrieval_fault("router")
        return rules or default
    return decision


__all__ = [
    "AGGREGATE_CUES",
    "COUNT_CUES",
    "GLOBAL_CUES",
    "PATH_CUES",
    "REASON_CHARS",
    "ROUTER_MAX_TOKENS",
    "ROUTER_OFF_REASON",
    "SHAPE_ROUTER_PROMPT",
    "ChatCallback",
    "QuestionShape",
    "ShapeDecision",
    "classify_by_rules",
    "parse_router_reply",
    "reply_content",
    "route",
    "shape_cues",
]
