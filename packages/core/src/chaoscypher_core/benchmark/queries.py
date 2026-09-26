# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LabeledQuery and queries.yaml parser.

The fixture format used by EmbeddingRetrievalDataset and GraphRAGChatDataset.
See the GraphRAG embedding benchmark design notes for
methodology.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, get_args

import yaml


if TYPE_CHECKING:
    from pathlib import Path


Band = Literal[
    "factual_single_hop",
    "multi_hop",
    "paraphrase",
    "fine_grained_discrimination",
    "out_of_scope",
]

BAND_VALUES: frozenset[str] = frozenset(get_args(Band))


@dataclass(frozen=True)
class LabeledQuery:
    """One labeled query for the embedding / chat fixtures.

    Attributes:
        id: Stable identifier (e.g. "q001"); surfaces in result rows.
        band: Difficulty band, one of BAND_VALUES.
        question: Natural-language query text.
        gold_entities: Canonical entity names for in-scope queries.
            Empty list for out_of_scope.
        gold_answer: Reference answer for in-scope queries; None otherwise.
        answer_terms: What a correct, concise answer must contain (one term
            per thing the question asks for); the chat probes score these.
            A term is a string, or a tuple of aliases any one of which
            satisfies it (a YAML list item such as ``[Mary, "Andrew's sister"]``).
            Empty means fall back to ``gold_entities``.
        must_not_contain: Wrong facts a correct answer never states (the
            famous wrong year, the tempting wrong join); the chat probes fail
            an answer that contains any of them as a whole phrase.
        expect_refusal: True only for out_of_scope queries.
    """

    id: str
    band: Band
    question: str
    gold_entities: list[str] = field(default_factory=list)
    gold_answer: str | None = None
    expect_refusal: bool = False
    # Chat probes (2026-09-25): the confusable entity a fine-grained question
    # must not lead with, and the world-knowledge answer an out-of-scope
    # question tempts a model into asserting.
    wrong_entities: list[str] = field(default_factory=list)
    decoy_entities: list[str] = field(default_factory=list)
    # Chat probes: curated answer terms; gold_entities stay the retrieval labels.
    answer_terms: list[str | tuple[str, ...]] = field(default_factory=list)
    # Chat probes: wrong facts the answer must not state.
    must_not_contain: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LabeledQuerySet:
    """A versioned set of labeled queries.

    Attributes:
        version: Surfaces on every result row alongside dataset_version.
        queries: Ordered list of labeled queries.
    """

    version: str
    queries: list[LabeledQuery]


def _parse_answer_terms(raw: list[Any], path: Path, i: int) -> list[str | tuple[str, ...]]:
    """Parse ``answer_terms``: each item a string, or a non-empty list of alias strings.

    A list item is an any-of group and becomes a tuple.

    Raises:
        ValueError: On an empty group, a nested list, or a non-string group member.
    """
    terms: list[str | tuple[str, ...]] = []
    for item in raw:
        if not isinstance(item, list):
            terms.append(str(item))
            continue
        if not item:
            msg = f"{path}: queries[{i}] answer_terms has an empty alias group"
            raise ValueError(msg)
        for member in item:
            if isinstance(member, list):
                msg = f"{path}: queries[{i}] answer_terms alias groups must not be nested"
                raise ValueError(msg)  # noqa: TRY004 - a fixture content error, like the rest of the loader
            if not isinstance(member, str):
                msg = (
                    f"{path}: queries[{i}] answer_terms alias group member "
                    f"{member!r} is not a string"
                )
                raise ValueError(msg)  # noqa: TRY004 - a fixture content error, like the rest of the loader
        terms.append(tuple(item))
    return terms


def load_queries(path: Path) -> LabeledQuerySet:  # noqa: C901, PLR0912 - YAML parser walks fields by hand for clear errors
    """Parse a queries.yaml file.

    Raises:
        ValueError: On any structural or semantic violation.
        TypeError: When the top level is not a YAML mapping.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"{path}: queries.yaml must be a YAML mapping"
        raise TypeError(msg)
    if "version" not in raw or "queries" not in raw:
        msg = f"{path}: missing required top-level keys 'version' and/or 'queries'"
        raise ValueError(msg)
    raw_queries = raw["queries"]
    if not isinstance(raw_queries, list) or not raw_queries:
        msg = f"{path}: 'queries' must be a non-empty list"
        raise ValueError(msg)

    parsed: list[LabeledQuery] = []
    for i, entry in enumerate(raw_queries):
        if not isinstance(entry, dict):
            msg = f"{path}: queries[{i}] is not a mapping"
            raise TypeError(msg)
        for required in ("id", "band", "question"):
            if required not in entry:
                msg = f"{path}: queries[{i}] missing required field '{required}'"
                raise ValueError(msg)
        band = str(entry["band"])
        if band not in BAND_VALUES:
            msg = f"{path}: queries[{i}] unknown band '{band}'"
            raise ValueError(msg)

        gold_entities = entry.get("gold_entities") or []
        gold_answer = entry.get("gold_answer")
        expect_refusal = bool(entry.get("expect_refusal", False))
        wrong_entities = [str(x) for x in entry.get("wrong_entities") or []]
        decoy_entities = [str(x) for x in entry.get("decoy_entities") or []]
        answer_terms = _parse_answer_terms(entry.get("answer_terms") or [], path, i)
        must_not = [str(x) for x in entry.get("must_not_contain") or []]

        if band == "out_of_scope":
            if gold_entities:
                msg = f"{path}: queries[{i}] out_of_scope must not set gold_entities"
                raise ValueError(msg)
            if gold_answer is not None:
                msg = f"{path}: queries[{i}] out_of_scope must not set gold_answer"
                raise ValueError(msg)
            if not expect_refusal:
                msg = f"{path}: queries[{i}] out_of_scope must set expect_refusal: true"
                raise ValueError(msg)
        else:
            if not gold_entities:
                msg = f"{path}: queries[{i}] in-scope query missing 'gold_entities'"
                raise ValueError(msg)
            if gold_answer is None:
                msg = f"{path}: queries[{i}] in-scope query missing 'gold_answer'"
                raise ValueError(msg)
            if expect_refusal:
                msg = f"{path}: queries[{i}] expect_refusal only allowed on out_of_scope"
                raise ValueError(msg)

        parsed.append(
            LabeledQuery(
                id=str(entry["id"]),
                band=band,  # type: ignore[arg-type]  # narrowed by BAND_VALUES membership check above
                question=str(entry["question"]),
                gold_entities=[str(g) for g in gold_entities],
                gold_answer=str(gold_answer) if gold_answer is not None else None,
                expect_refusal=expect_refusal,
                wrong_entities=wrong_entities,
                decoy_entities=decoy_entities,
                answer_terms=answer_terms,
                must_not_contain=must_not,
            )
        )

    return LabeledQuerySet(version=str(raw["version"]), queries=parsed)


__all__ = ["BAND_VALUES", "Band", "LabeledQuery", "LabeledQuerySet", "load_queries"]
