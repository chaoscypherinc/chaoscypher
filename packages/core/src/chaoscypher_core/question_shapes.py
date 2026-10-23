# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The question shapes' names and the levers no shape may set, in one leaf module the settings and the router both read.

``GraphRAGSettings.shape_levers`` is keyed by :data:`SHAPE_NAMES` (its
validator refuses any other key, and any bundle naming a
:data:`PRE_SHAPE_FIELDS` field) and ``question_shape.QuestionShape`` holds the
names as its members (a test pins the two equal). A leaf, so ``settings``
reads them without importing a service.

The shape rules' count of the entities a question names lives here too
(:func:`question_tokens`, :func:`named_mention_count`): the handler counts
the mentions that name a seed, and the benchmark's graph-free proxy
(``benchmark.shapes.named_seeds``) the mentions that name a gold entity, by
the one rule, without the benchmark importing the handler.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from chaoscypher_core.utils.query_text import normalize_query


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


SHAPE_NAMES: tuple[str, ...] = ("lookup", "aggregate", "path", "global")
"""Every question shape ``graphrag_search`` can run with, in the router's order."""

PRE_SHAPE_FIELDS: tuple[str, ...] = (
    "seed_similarity_threshold",
    "vector_overfetch_multiplier",
    "keyword_seeds",
    "max_graph_nodes",
    "ppr_damping",
    "include_unevidenced_edges",
    "router",
)
"""The GraphRAG settings ``graphrag_search`` reads before the question's shape is known.

The seeds' (vector and keyword), PageRank's and the router's own: they run on
the base settings (``GraphRAGToolHandlers.levers``), so a shape bundle setting
one would do nothing, or split the pipeline (``include_unevidenced_edges``
read by PageRank on the base and by the context from the bundle). The settings
refuse a bundle naming one; a guard test pins that every base-settings read of
the handler is one of these.
"""


_QUERY_TOKEN = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)*")
"""A word of a question as written, possessive kept (``Andrew's``)."""


def question_tokens(query: str) -> list[str | None]:
    """The question's words as written, in order, with None where punctuation parts two of them.

    The one tokenisation of a question (composed first,
    :func:`~chaoscypher_core.utils.query_text.normalize_query`: a decomposed
    accent would split a word): the handler's query words read the tokens,
    and the named-mention count reads the breaks too, so "Pierre, Andrew"
    is two mentions while "Anna Pávlovna" is one.
    """
    text = normalize_query(query)
    out: list[str | None] = []
    end = 0
    for match in _QUERY_TOKEN.finditer(text):
        if out and text[end : match.start()].strip():
            out.append(None)
        out.append(match.group())
        end = match.end()
    return out


def named_mention_count(words: Iterable[str | None], names: Callable[[str], bool]) -> int:
    """How many things the question names: its mentions, not the nodes they match (the shape rules' count).

    ``words`` are the question's words in order as match keys, None where a
    mention breaks (punctuation, a function word, a word that names
    nothing); the caller leaves the titles out, so a title neither names nor
    breaks ("Prince Andrew" is Andrew). A mention is a run of consecutive
    words each naming something (``names``), so "Anna Pávlovna" is one and
    "the Rostóvs", naming three Rostóv nodes, is one too. The same mention
    written twice, or part of one written again ("Anna" after "Anna
    Pávlovna"), counts once; two that share only a surname ("Natásha
    Rostóva" and "Nicholas Rostóv") are two.

    Until 2026-10-09 the handler counted the seed nodes the question's words
    named, so one surname naming several nodes inflated the count (54
    lookups abstained live where the text-only count abstained on 14).
    """
    runs: list[frozenset[str]] = []
    run: list[str] = []
    for word in [*words, None]:
        if word is not None and names(word):
            run.append(word)
            continue
        if run:
            runs.append(frozenset(run))
            run = []
    distinct = set(runs)
    return sum(1 for r in distinct if not any(r < other for other in distinct))


PART_CUES: tuple[str, ...] = (
    "what",
    "who",
    "whom",
    "which",
    "where",
    "when",
    "how",
    "why",
    "to whom",
    "in what",
    "by whom",
    "at what",
    "on what",
)
"""The words that open the second part of a two-part question after ``and`` (``..., and what did he say?``)."""

PART_MIN_WORDS = 5
"""A part shorter than this stays with the question (``and in what capacity each?`` searches nothing on its own)."""

_PART_SPLIT = re.compile(
    r"\s*[,;]?\s+and\s+(?=(?:"
    + "|".join(re.escape(c) for c in sorted(PART_CUES, key=len, reverse=True))
    + r")\b)",
    re.IGNORECASE,
)


def question_parts(query: str) -> list[str]:
    """The parts of a multi-part question, or the question alone.

    A question that asks two things at once ("Which guest called the execution a
    political necessity, and what was that guest's first breach of manners?")
    is split where ``and`` opens a new question (:data:`PART_CUES`), and on a
    semicolon; each part keeps the question mark. The chunk search then runs
    for the whole and for each part (``GraphRAGSettings.question_parts``), so
    the second half's chunk is reached when the whole question's embedding
    is dominated by the first half (the fixture's cross-chapter joins: the
    chunk holding the answer to the second half was missing on every model,
    2026-10-10). A part under :data:`PART_MIN_WORDS` words is folded back,
    and a question with one part is returned as written.
    """
    text = normalize_query(query).strip()
    pieces = [piece.strip() for piece in re.split(r";\s*", text) if piece.strip()]
    parts: list[str] = []
    for piece in pieces:
        parts.extend(part.strip() for part in _PART_SPLIT.split(piece) if part.strip())
    if len(parts) < 2:
        return [query]
    kept: list[str] = []
    for part in parts:
        if len(part.split()) < PART_MIN_WORDS and kept:
            kept[-1] = f"{kept[-1]}, and {part}"
        else:
            kept.append(part if part.endswith("?") else f"{part}?")
    return kept if len(kept) >= 2 else [query]


__all__ = [
    "PART_CUES",
    "PART_MIN_WORDS",
    "PRE_SHAPE_FIELDS",
    "SHAPE_NAMES",
    "named_mention_count",
    "question_parts",
    "question_tokens",
]
