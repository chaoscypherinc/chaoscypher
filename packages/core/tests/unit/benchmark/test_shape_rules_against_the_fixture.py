# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The shape rules never contradict the fixture's bands when they fire (spec decision 11).

The expected shape of a question is its band and kind
(:data:`~chaoscypher_core.benchmark.shapes.EXPECTED_SHAPES`): cast_aggregate →
aggregate, relationship_path → path, cross_chapter_join → lookup or path, every
other band → lookup. The rules see the question text, a count of its named
entities, and its relation words in the universal vocabulary (the handler's own
reading, ``graphrag_handlers.question_relations``; the stack's vocabulary needs
a database): the agreement measured here is the rules' floor, the live
router's is ``router_check.py``'s. The relation words are read so the rule they
trigger (a relational lookup between two named entities, never a path) stays
under test.

A gold entity counts as named only when the question writes one of its naming
words, counted as mentions
(:func:`~chaoscypher_core.benchmark.shapes.named_seeds`, which
``router_check.py`` shares). ``gold_entities`` holds the answer's people too:
with every one of them counted, 69 lookup-only questions carry two or more, the
rules must abstain on all of them, and the share they can decide is capped at
0.52.

Fired share: 0.82 (119 of 145: 108 lookup, 6 path, 5 aggregate) on 2026-10-09 after the
review's router fix (mentions counted, relation words a lookup; it was 111: 100 lookup, 6 path,
5 aggregate), the fixture and the holdout; 0.81 (97 of 120) on the fixture alone (the public export).
"""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.benchmark.queries import load_queries
from chaoscypher_core.benchmark.shapes import expected_shapes, named_seeds
from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
    UNIVERSAL_VOCABULARY,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    question_relations,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.question_shape import (
    classify_by_rules,
)


_REPO = Path(__file__).resolve().parents[5]
FIXTURE = (
    _REPO
    / "packages"
    / "cli"
    / "src"
    / "chaoscypher_cli"
    / "benchmark"
    / "data"
    / "datasets"
    / "war_and_peace_book1"
    / "queries.yaml"
)
HOLDOUT = _REPO / "internal" / "benchmarks" / "holdout" / "war_and_peace_book1-holdout.yaml"


def _question_files() -> list[Path]:
    """The fixture, always, and the holdout when it exists (the public export has no ``internal/``)."""
    assert FIXTURE.is_file(), f"the shape rules' fixture is missing: {FIXTURE}"
    return [FIXTURE, *([HOLDOUT] if HOLDOUT.is_file() else [])]


def test_the_rules_agree_with_every_band_they_decide() -> None:
    disagreements: list[str] = []
    fired = 0
    total = 0
    for path in _question_files():
        for q in load_queries(path).queries:
            total += 1
            decision = classify_by_rules(
                q.question,
                named_seeds=named_seeds(q),
                relations=question_relations(q.question, UNIVERSAL_VOCABULARY),
            )
            if decision is None:
                continue
            fired += 1
            if decision.shape.value not in expected_shapes(q):
                disagreements.append(
                    f"{q.id} {q.kind or q.band}: {decision.shape} ({decision.reason})"
                )
    assert not disagreements, "\n".join(disagreements)
    assert fired >= 0.6 * total, f"the rules decided only {fired} of {total}"
