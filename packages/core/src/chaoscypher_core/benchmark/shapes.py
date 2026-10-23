# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The question shapes a fixture question expects, by its ``graph_advantage`` kind.

The one table of what shape (``lookup``, ``aggregate``, ``path``, ``global``:
``QuestionShape``'s values) each labeled question wants: the shape rules'
fixture test checks the rules against it, and the benchmark tools read it too
(``router_check.py``'s agreement, ``chat_miss_classifier.py``'s route miss).
A fired rule that contradicts it is a cue list to fix, never a label to move.
:func:`named_seeds` is the graph-free count of a question's named entities
the rules are scored with, shared by the same test and tool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chaoscypher_core.question_shapes import named_mention_count, question_tokens
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DEFAULT_NAME_CONVENTIONS,
    fold,
)


if TYPE_CHECKING:
    from chaoscypher_core.benchmark.queries import LabeledQuery


EXPECTED_SHAPES: dict[str, frozenset[str]] = {
    "cast_aggregate": frozenset({"aggregate"}),
    "relationship_path": frozenset({"path"}),
    "cross_chapter_join": frozenset({"lookup", "path"}),
    "disambiguation": frozenset({"lookup"}),
}
"""The shape values a ``graph_advantage`` question of each kind may be routed to.

A ``cross_chapter_join`` joins facts through a person, so a walk is as right
as a passage; every other kind has one shape.
"""

_NO_KIND: frozenset[str] = frozenset({"lookup"})

_TITLES = frozenset(fold(t) for t in DEFAULT_NAME_CONVENTIONS.titles)


def expected_shapes(q: LabeledQuery) -> frozenset[str]:
    """The shape values a labeled question may be routed to; ``{"lookup"}`` when it has no kind.

    Args:
        q: A fixture or holdout question.

    Returns:
        :data:`EXPECTED_SHAPES` of its kind, or ``{"lookup"}`` for a question
        outside the ``graph_advantage`` band.
    """
    return EXPECTED_SHAPES.get(q.kind or "", _NO_KIND)


def named_seeds(q: LabeledQuery) -> int:
    """Count the things the question names among its gold entities: its mentions that name one (the rules' count).

    A test and tool proxy for the handler's count
    (``GraphRAGToolHandlers._named_seed_count``) without a graph, by the one
    rule (:func:`~chaoscypher_core.question_shapes.named_mention_count` over
    :func:`~chaoscypher_core.question_shapes.question_tokens`): a mention is
    a run of the question's words each naming something, titles left out,
    so "Anna Pávlovna Schérer" is one and "Pierre and Andrew" two: mentions,
    never the distinct naming words (ruling of 2026-10-09: a three-word name
    counts once). It
    differs from the handler's in exactly what names: a word names when it
    is a folded, non-title word of a gold entity (the handler: a naming
    word of a seed's label or alias, through the domain's surname stems,
    plurals and possessives), and a word breaks a mention when it is under
    four letters and not capitalised (the handler: its function-word list
    too); the titles are the default conventions'. ``gold_entities`` holds
    the answer's people too, so its length would count names the question
    never writes (69 lookup-only questions carry two or more, and the rules
    would abstain on every one).

    Args:
        q: A fixture or holdout question.
    """
    golds = [set(fold(g).split()) - _TITLES for g in q.gold_entities]
    words: list[str | None] = []
    for token in question_tokens(q.question):
        if token is None:
            words.append(None)
            continue
        words.extend(  # a title neither names nor breaks a mention
            word if len(word) >= 4 or token[:1].isupper() else None
            for word in fold(token).split()
            if word not in _TITLES
        )
    return named_mention_count(words, lambda word: any(word in gold for gold in golds))


__all__ = ["EXPECTED_SHAPES", "expected_shapes", "named_seeds"]
