# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LabeledQuery and queries.yaml parser.

The fixture format used by EmbeddingRetrievalDataset and GraphRAGChatDataset.
See the GraphRAG embedding benchmark design notes for
methodology.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, get_args

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
        expect_refusal: True only for out_of_scope queries.
    """

    id: str
    band: Band
    question: str
    gold_entities: list[str] = field(default_factory=list)
    gold_answer: str | None = None
    expect_refusal: bool = False


@dataclass(frozen=True)
class LabeledQuerySet:
    """A versioned set of labeled queries.

    Attributes:
        version: Surfaces on every result row alongside dataset_version.
        queries: Ordered list of labeled queries.
    """

    version: str
    queries: list[LabeledQuery]


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
            )
        )

    return LabeledQuerySet(version=str(raw["version"]), queries=parsed)


__all__ = ["BAND_VALUES", "Band", "LabeledQuery", "LabeledQuerySet", "load_queries"]
