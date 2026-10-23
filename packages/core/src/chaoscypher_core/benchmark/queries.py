# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LabeledQuery and queries.yaml parser.

The fixture format used by EmbeddingRetrievalDataset and GraphRAGChatDataset.
See the GraphRAG embedding benchmark design notes for
methodology.
"""

from __future__ import annotations

import hashlib
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
    "graph_advantage",
]

BAND_VALUES: frozenset[str] = frozenset(get_args(Band))

BOARD_BANDS: tuple[str, ...] = (
    "factual_single_hop",
    "multi_hop",
    "paraphrase",
    "fine_grained_discrimination",
    "out_of_scope",
)
"""The five bands the chat column and the embedding retrieval board score.

``graph_advantage`` is the leaderboard's retrieval comparison's alone: it is
asked in every chat run but counted only by that section, so adding it to a
fixture moves no published chat score.
"""

KINDS: tuple[str, ...] = (
    "cross_chapter_join",
    "relationship_path",
    "cast_aggregate",
    "disambiguation",
)
"""What a ``graph_advantage`` question needs a graph for.

- ``cross_chapter_join``: the answer joins facts stated in different chapters.
- ``relationship_path``: how two people are connected, through one or two others.
- ``cast_aggregate``: which people share a property or scene.
- ``disambiguation``: two people or places the text keeps apart by a shared
  name or title.
"""


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
        answerable: False when the fixture audit found the retrieved context
            cannot answer the question (chat scorer v4); the chat probes then
            leave it out of verdicts and totals, since no chat model could do
            better than guess. Only an in-scope query may be flagged, and
            never a ``graph_advantage`` one (a rung's miss is the measurement).
        kind: On the ``graph_advantage`` band, one of :data:`KINDS`; None
            elsewhere.
        caveats: Details the question takes for granted that the text does not
            state (the host's name, a first name, 'widowed'). A model that
            gives the answer and honestly notes one of these is missing has
            not declined; the chat probes do not read a sentence naming a
            caveat as a decline (2026-10-03 audit of the real answers).
        evidence: Phrases that carry the asked fact in the text ("next heir").
            When set, an answer counts only if its own retrieved context holds
            one of them: a model that names the right people from memory when
            the passage was never retrieved has not answered from the context
            (2026-10-03 full pass, q032).
        alias_evidence: Per alias of an answer term, phrases its context must hold
            for that alias to count as grounded ("Andrew" only where the context
            says "the wife of Prince Andrew" or "appointed Prince Andrew as his
            aide-de-camp"): a name the context never ties to the asked person is
            from memory (q029, 2026-10-03 full pass).
        roles: Person-to-role bindings for "which one is which" questions: a
            list of ``{"person": [aliases], "roles": [terms]}``. Each role term
            in the answer must follow, nearest, a person who holds it (a term
            may be listed for several people), and every person must get one
            of their roles; swapped or missing capacities fail.
        candidate: True while a ``graph_advantage`` question awaits the keep
            rule (``internal/benchmarks/tools/retrieval_mode_check.py
            --candidates``: kept when any rung retrieves its answer terms,
            dropped only when none does). Scoring ignores it.
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
    # Chat probes (scorer v4): the fixture audit's verdict on the retrieved context.
    answerable: bool = True
    # The retrieval comparison's band: what the question needs a graph for,
    # and whether it still awaits the keep rule.
    kind: str | None = None
    candidate: bool = False
    # Chat probes: question premises the text does not state (see the docstring).
    caveats: list[str] = field(default_factory=list)
    # Chat probes (2026-10-03 full pass): where the fact lives, and who holds which role.
    evidence: list[str] = field(default_factory=list)
    alias_evidence: dict[str, list[str]] = field(default_factory=dict)
    roles: list[dict[str, list[str]]] = field(default_factory=list)


@dataclass(frozen=True)
class LabeledQuerySet:
    """A versioned set of labeled queries.

    Attributes:
        version: Surfaces on every result row alongside dataset_version.
        queries: Ordered list of labeled queries.
        sha256: The sha256 of the file's bytes as :func:`load_queries` parsed
            them, so the content a row records is the content that asked and
            scored it (audit wave 4: the hash was taken from the path when the
            run started, a second read that a mid-sweep edit could slip
            between). None for a set built in code.
    """

    version: str
    queries: list[LabeledQuery]
    sha256: str | None = None


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


def _parse_kind(
    entry: dict[str, Any], band: str, *, answerable: bool, path: Path, i: int
) -> str | None:
    """The ``graph_advantage`` band's ``kind``; None on every other band.

    Raises:
        ValueError: When the band has no kind or an unknown one, when another
            band carries a kind, or when a band question is flagged
            ``answerable: false`` (a rung's miss is what the band measures).
    """
    kind = entry.get("kind")
    if band != "graph_advantage":
        if kind is not None:
            msg = f"{path}: queries[{i}] kind is only for the graph_advantage band"
            raise ValueError(msg)
        return None
    if kind not in KINDS:
        msg = (
            f"{path}: queries[{i}] graph_advantage needs kind, one of {', '.join(KINDS)}"
            f" (got {kind!r})"
        )
        raise ValueError(msg)
    if not answerable:
        msg = f"{path}: queries[{i}] answerable: false is not allowed on graph_advantage"
        raise ValueError(msg)
    return str(kind)


def load_queries(path: Path) -> LabeledQuerySet:  # noqa: C901, PLR0912, PLR0915 - YAML parser walks fields by hand for clear errors
    """Parse a queries.yaml file.

    Raises:
        ValueError: On any structural or semantic violation.
        TypeError: When the top level is not a YAML mapping.
    """
    data = path.read_bytes()
    raw = yaml.safe_load(data.decode("utf-8"))
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
        answerable = bool(entry.get("answerable", True))
        kind = _parse_kind(entry, band, answerable=answerable, path=path, i=i)
        candidate = bool(entry.get("candidate", False))
        caveats = [str(x) for x in entry.get("caveats") or []]
        evidence = [str(x) for x in entry.get("evidence") or []]
        alias_evidence = {
            str(k): [str(v) for v in vs] for k, vs in (entry.get("alias_evidence") or {}).items()
        }
        roles = [
            {k: [str(v) for v in (r.get(k) or [])] for k in ("person", "roles")}
            for r in entry.get("roles") or []
        ]

        if band == "out_of_scope":
            if not answerable:
                msg = f"{path}: queries[{i}] answerable: false is only for in-scope queries"
                raise ValueError(msg)
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
                answerable=answerable,
                kind=kind,
                candidate=candidate,
                caveats=caveats,
                evidence=evidence,
                alias_evidence=alias_evidence,
                roles=roles,
            )
        )

    return LabeledQuerySet(
        version=str(raw["version"]), queries=parsed, sha256=hashlib.sha256(data).hexdigest()
    )


__all__ = [
    "BAND_VALUES",
    "BOARD_BANDS",
    "KINDS",
    "Band",
    "LabeledQuery",
    "LabeledQuerySet",
    "load_queries",
]
