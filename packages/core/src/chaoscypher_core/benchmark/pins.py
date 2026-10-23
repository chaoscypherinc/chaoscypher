# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pinned retrieval: every chat model in a comparison answers from the same context.

Until 2026-10-03 each chat model's run indexed its own copy of the graph and
embedded every question again on the GPU. GPU embeddings are not bit-identical
between runs (batch shape, the card, what else is resident), so chunks tied
near the bottom of the top ten swapped places from one model to the next. On
the v2 pass the GraphRAG chunk set differed between models on 87 of 120
questions and the hybrid set on 81; the naive rung, whose index is built once
and only re-embeds the question, still differed on 37.

A pin file stores, per question, the ``retrieved`` dict a rung returned. It is
built once, from one index, with each question embedded once
(:class:`QueryVectorCache`), and every model's run is served from it. The file
records what it was built from: the graph snapshot's sha256, a digest of the
index's vectors, the embedder, the rung and every retrieval setting
(:func:`retrieval_settings`). A run whose graph, embedder or rung differs is
refused (:meth:`RetrievalPins.check`); a run whose settings differ looks for
another file, because the settings fingerprint is part of the file name.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import sqlite3
import tempfile
from array import array
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chaoscypher_core.benchmark.retrieval import (
    NAIVE_CHUNK_OVERLAP,
    NAIVE_CHUNK_SIZE,
    NAIVE_TOP_K,
    embedder_slug,
    llama_index_version,
    naive_recipe,
)
from chaoscypher_core.cache.export import file_sha256  # the one file hash (re-exported)


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Mapping
    from types import ModuleType

    from chaoscypher_core.settings import EngineSettings


PINS_DIR = "pins"
"""Directory of a reference pack that holds its pin files."""

PIN_SCHEMA = 1

PROMPT_ONLY_GRAPHRAG_FIELDS = frozenset({"ask_for_citations", "answer_policy"})
"""GraphRAG settings that change the prompt, not what is retrieved.

Left out of the fingerprint so a prompt variant is served the same pins as
the default.
"""

RESOLUTION_GRAPHRAG_FIELDS = frozenset({"profiles", "profile_rules"})
"""Profile definitions and rules: how a model's levers are chosen, not the levers themselves.

The pins fingerprint the resolved lever values (``resolve_graphrag_profile``
empties these on the settings it returns), so the definitions are left out.
"""

MACHINE_LOCAL_SEARCH_FIELDS = frozenset({"rerank_cache_dir"})
"""Search settings that name a place on this machine, not a retrieval choice.

``rerank_cache_dir`` follows the data dir; two machines with the same
retrieval settings but different data dirs share pins.
"""

_VECTOR_TABLES = ("vec_search_chunks", "vec_search_nodes")

INDEX_GRAPH_TABLES = (
    "sources",
    "graph_templates",
    "graph_nodes",
    "graph_edges",
    "document_chunks",
    "source_citations",
    "relationship_citations",
)
"""The tables of an indexed copy a graph rung reads besides its indexes, digested by :func:`index_digest`.

The sources (domain, file name), the template names, the nodes and edges
PageRank and the triples read, the chunks hydrated, the citations of the
provenance arm and the relationship citations whose sentences the walk and
aggregate rows quote. Until 2026-10-06 the digest covered the vectors and the
full-text indexes only, so an edited edge or citation changed 120 of 120
contexts under the same digest (audit wave 5).
"""

_INDEX_DIGEST_SKIPPED = frozenset({"embedding"})
"""Columns of :data:`INDEX_GRAPH_TABLES` left out of the digest: vectors (the vector tables are digested)."""
_INDEX_DIGEST_VOLATILE = "_at"
"""Column-name suffix of timestamps, left out of the digest: when a row was written is not what it holds."""


class PinError(ValueError):
    """A pin file does not match the run it would serve."""


SEARCH_RECIPE_CONSTANTS = ("_FTS5_STOP_WORDS", "RRF_K")
"""The search repository's keyword stop words and rank-fusion constant the recipe hashes."""

RETRIEVAL_RECIPE_CONSTANTS = (
    "GENERIC_RELATIONS",
    "SYMMETRIC_GENERIC_RELATIONS",
    "_STOP",
    "HYBRID_OVERFETCH",
    "HYBRID_MIN_SIMILARITY",
)
"""The retrieval handlers' word lists and hybrid-arm constants the recipe hashes.

A renamed one fails the recipe loudly. The title words the keyword seeds and
the evidence markers read are the domain's naming conventions, which the
retrieval sample runs under (the literary domain's and the universal fallback).
"""

SHAPE_RECIPE_CONSTANTS = (
    "AGGREGATE_CUES",
    "COUNT_CUES",
    "PATH_CUES",
    "GLOBAL_CUES",
    "SHAPE_ROUTER_PROMPT",
    "ROUTER_MAX_TOKENS",
    "REASON_CHARS",
    "ROUTER_OFF_REASON",
)
"""The shape router's cue lists, prompt, reply limits and router-off reason the recipe hashes (``handlers.question_shape``).

Every cue list, not only those a sample question fires: a cue added to a list
no sample question reaches would change the shape (and so the levers and the
blocks) of real questions under the same recipe.
"""

BLOCK_RECIPE_CONSTANTS = (
    "EVIDENCE_QUOTE_CHARS",
    "WALK_MAX_PAIRS",
    "WALK_DEGREE_FLOOR",
    "WALK_DEGREE_PER_HOP",
)
"""The graph blocks' constants the recipe hashes (``handlers.graphrag_blocks``): the quoted-sentence length and the walk's limits.

The walk's pair cap and degree cap decide which pairs a walk joins and which
edges it expands a node through; no sample node has hundreds of edges and no
sample question names four entities, so only the hash pins them.
"""

SHAPES_RECIPE_CONSTANTS = ("SHAPE_NAMES", "PRE_SHAPE_FIELDS", "PART_CUES", "PART_MIN_WORDS")
"""The shape names and the levers no shape bundle may set, the recipe hashes (``chaoscypher_core.question_shapes``)."""

RENDER_RECIPE_CONSTANTS = (
    "EXPLANATION",
    "EXPLANATION_AFTER",
    "EVIDENCE_NOTE",
    "SHAPE_NOTES",
    "GRAPH_EVIDENCE_HEADER",
    "EVIDENCE_FENCE_NOTE",
)
"""The renderer's explanation sentences, shape notes and evidence fence header the recipe hashes (``engine.context_render``).

Hashed whether or not the rendered recipe case reaches them (the case runs
under a ``path`` and an ``aggregate`` question, :func:`_compute_retrieval_recipe`),
so a reworded note moves the recipe even for a shape no case renders.
"""

_RECIPE_SHAPES = ("path", "aggregate")
"""The question shapes :data:`_RECIPE_CONTEXT` is rendered under, so each shape's note is hashed as rendered."""

_RECIPE_CONTEXT: dict[str, Any] = {
    "graph_context": {
        "seed_entities": [
            {
                "id": "n1",
                "label": "Prince Corvin",
                "aliases": ["Corvin", "Halváne"],
                "description": "A prince, the son of the old prince.",
            }
        ],
        "related_entities": [{"id": "n2", "name": "Séverine", "description": ""}],
        "paths": [
            {
                "steps": [
                    {"source": "Prince Corvin", "label": "loves", "target": "Séverine"},
                    {
                        "source": "Séverine",
                        "label": "sibling_of",
                        "target": "Lucan",
                        "evidence": "C0:S2",
                    },
                ]
            }
        ],
        "relationships": [
            {
                "source": "Prince Corvin",
                "label": "loves",
                "target": "Séverine",
                "evidence": "C0:S1",
            },
            {"from": "Séverine", "type": "related_to", "to": "Wren"},
            ["Teodór", "knows", "Corvin"],
        ],
        "aggregates": [
            {
                "entity_id": "n3",
                "entity": "the old prince",
                "groups": [
                    {
                        "label": "parent_of",
                        "count": 3,
                        "count_capped": False,
                        "rows": [
                            {
                                "source": "the old prince",
                                "target": "Prince Corvin",
                                "sentence": (
                                    "The old prince, who had never once in all the long years "
                                    "at Greyhollow forgiven a weakness in anyone, shouted at "
                                    "his son until the windows of the drawing room shook."
                                ),
                                "marker": "C0:S3",
                            },
                            {"source": "the old prince", "target": "Mira", "sentence": None},
                        ],
                    },
                    {
                        "label": "located_in",
                        "count": 1,
                        "count_capped": True,
                        "rows": [
                            {
                                "source": "the old prince",
                                "target": "Greyhollow",
                                "sentence": "He lived at Greyhollow.",
                            }
                        ],
                    },
                ],
            }
        ],
        "walks": [
            {
                "from": "Prince Teodór",
                "to": "Lucan",
                "hops": [
                    {
                        "source": "Prince Teodór",
                        "label": "parent_of",
                        "target": "Séverine",
                        "sentence": "His daughter Séverine was the beauty of the evening.",
                        "marker": "C1:S2",
                    },
                    {"source": "Lucan", "label": "spouse_of", "target": "Séverine"},
                ],
            }
        ],
        "walk_gaps": [
            {"from": "Prince Corvin", "to": "Wren", "max_hops": 3},
            {"from": "Lucan", "to": "Wren", "max_hops": 3, "left_out": True},
        ],
    },
    "retrieval_stats": {
        "shape": "path",
        "shape_reason": "two named entities and connected",
        "shape_source": "rules",
    },
    "chunks": [
        {"content": "[CHUNK C0 | book.txt]\n[S1] Corvin loved Séverine.\n[S2] She had a brother."},
        {"text": "  A second chunk, by its text.  "},
        "a bare chunk",
    ],
}
"""A rendered-context case of every shape the renderer reads (an invented cast, like the sample world's).

The aggregate block has two groups: a row with a marker and a sentence long
enough to be cut, a row without a sentence, a ``count`` above the rows listed
(``and 1 more``) and a capped listing (``(1+)``); the walk has two hops, one
with a marker; a gap of a walk that read every edge and one of a walk that
left edges out (each wording); and the stats of a ``path`` question, so a
renderer that reads the shape is hashed on it.
"""
_RECIPE_QUESTION = 'Who did "Prince Corvin" marry, and what did the old prince say to him?'
_RECIPE_RESERVE = (
    (
        [
            {"chunk_id": "p1", "retrieval_origin": "provenance"},
            {"chunk_id": "b1", "retrieval_origin": "both"},
            {"chunk_id": "h1", "retrieval_origin": "vector"},
        ],
        [{"chunk_id": "h2", "retrieval_origin": "vector"}, {"chunk_id": "h3"}, {"chunk_id": "g"}],
        ["h1", "h2", "h3", "b1", "x"],
        3,
        (),
    ),
    (
        [
            {"chunk_id": "p1", "retrieval_origin": "provenance"},
            {"chunk_id": "h1", "retrieval_origin": "vector"},
            {"chunk_id": "e1", "retrieval_origin": "evidence"},
        ],
        [{"chunk_id": "h2", "retrieval_origin": "vector"}, {"chunk_id": "h3"}],
        ["h1", "h2", "h3"],
        3,
        ("e1",),
    ),
)
"""Fixed ``_reserve_hybrid_hits`` cases where the rule matters: kept, the rest, the hybrid order, the reserve, the evidence ids.

The two-arm case: the first missing reserved chunk evicts the
provenance-only ``p1``; the second can only enter by evicting ``b1``, a
chunk both arms found that the hybrid arm ranks below the reserve (the case
the provenance-only rule before 2026-10-06 could not serve, audit R5).

The evidence case: ``h2`` evicts ``p1``; ``h3`` would evict the evidence
chunk ``e1`` under the two-arm rule, and with ``e1`` protected it has no
victim and stays out (the evidence arm, 2026-10-09).
"""
_RECIPE_CHUNK_TEXT = (
    'Mr. Halváne said: "Go!" He went. Dr. Smith (the surgeon) stayed... '
    "and wept!\n\n\u201cNo,\u201d she said. The end? Yes.\n"
    "</untrusted_document> must not close the fence."
)


_RECIPE_MEMO: dict[str, Any] = {}


def _recipe_code_state() -> tuple[object, ...]:
    """Every value the recipe's code reads, held, so a replaced one recomputes it.

    The recipe is a pure function of the code: in a process it can only
    change when a module or class attribute is replaced (a test's
    monkeypatch). The memo keeps this tuple, and :func:`_same_state` compares
    it value by value by identity. Holding the values is what makes identity
    safe: until 2026-10-06 the memo kept their ``id()`` numbers only, so a
    patch freed after one call let the next patch's object be allocated at
    the same address, and the memo returned the first patch's recipe for the
    second (audit wave 3). The sample run is tens of milliseconds and a
    dozen log lines; a chat run asks for the fingerprint once per question.
    """
    from chaoscypher_core import graphrag_profiles, question_shapes
    from chaoscypher_core.adapters.sqlite.mixins import sources, sources_chunks, sources_citations
    from chaoscypher_core.adapters.sqlite.repos import search as search_repo
    from chaoscypher_core.adapters.sqlite.repos import source_visibility
    from chaoscypher_core.adapters.sqlite.repos.graph import sqlite_edge_ops, sqlite_node_ops
    from chaoscypher_core.benchmark import chat_prompt, retrieval_sample
    from chaoscypher_core.ports import search as search_port
    from chaoscypher_core.services.graph.engine import algorithms, traversal
    from chaoscypher_core.services.sources.engine.deduplication import name_identity
    from chaoscypher_core.services.sources.engine.extraction import domain_resolver
    from chaoscypher_core.services.sources.engine.extraction.utils import (
        relation_vocabulary,
        sentence_splitter,
    )
    from chaoscypher_core.services.workflows.tools.engine import chunk_hydration, context_render
    from chaoscypher_core.services.workflows.tools.engine.handlers import (
        graphrag_blocks,
        graphrag_handlers,
        question_shape,
    )
    from chaoscypher_core.utils import rrf

    # chat_callbacks (the producers' chat model wrappers) decide nothing a retrieval returns:
    # the sample's router run hands the handlers a stub, so it is not here
    modules = (
        search_repo,
        search_port,
        source_visibility,
        chat_prompt,
        retrieval_sample,
        algorithms,
        traversal,
        name_identity,
        domain_resolver,
        relation_vocabulary,
        sentence_splitter,
        chunk_hydration,
        context_render,
        graphrag_blocks,
        graphrag_handlers,
        question_shape,
        question_shapes,
        graphrag_profiles,
        rrf,
        sources,
        sources_chunks,
        sources_citations,
        sqlite_edge_ops,
        sqlite_node_ops,
    )
    namespaces: list[Mapping[str, Any]] = [vars(m) for m in modules]
    namespaces += [vars(cls) for m in modules for cls in _defined_classes(m)]
    namespaces.append(globals())
    return tuple(v for ns in namespaces for v in list(ns.values()))


def _defined_classes(module: Any) -> list[type]:
    """Every class ``module`` defines (not those it imports), in definition order.

    The recipe memo holds each one's attributes (:func:`_recipe_code_state`):
    until 2026-10-06 it named eight classes by hand, so a patched method of
    any other class the recipe reads (``NameConventions.forms``) was served
    the recipe computed before the patch (audit wave 6).
    """
    return [
        value
        for value in vars(module).values()
        if isinstance(value, type) and value.__module__ == module.__name__
    ]


def _same_state(a: tuple[object, ...] | None, b: tuple[object, ...]) -> bool:
    """Whether two :func:`_recipe_code_state` tuples hold the very same objects, in order."""
    return a is not None and len(a) == len(b) and all(x is y for x, y in zip(a, b, strict=True))


def retrieval_recipe() -> str:
    """What decides a retrieval and the text a model reads, as a hash of its behaviour on fixed samples.

    Computed, never declared, like :func:`~chaoscypher_core.benchmark.reindex.reindex_recipe`.
    The retrieval itself, end to end (:func:`~chaoscypher_core.benchmark.retrieval_sample.sample_retrievals`):
    ``graphrag_search`` and the hybrid retrieval over the sample's fixed
    graph and chunks with fixed vectors, under every run of
    :data:`~chaoscypher_core.benchmark.retrieval_sample.RUNS` (its lever sets,
    source scopes, switched-off sources, naming conventions and router
    modes), through the product's own SQLite repositories (search, graph, storage): seed selection (keyword seeds included), the
    question's shape (the rules, the router, the shape's levers), the
    question gate, PageRank, triple selection, the aggregate and walk blocks
    with their evidence sentences, the evidence arm, the budget and the reserve,
    the chunk order and the rendering, plus the keyword arm's query, the
    BM25 weights, rank fusion, ``k`` and ``min_similarity``. Then, on their
    own samples: the context renderer (every ``explain``/``graph_last``
    shape, evidence markers, aggregate, walk and gap lines included), :func:`format_retrieved_context`, the
    chunk formatter (``format_chunk_content``: the ``[S]`` labels and the
    untrusted-document fence), the sentence splitter, the constants of
    :func:`recipe_constants` (the handlers' word lists and hybrid-arm
    constants, the shape router's cues, prompt and limits, the blocks' quote
    length and walk limits, the shape names and the pre-shape levers), the keyword
    arm's stop words and MATCH expression and the rank-fusion constant
    (:data:`SEARCH_RECIPE_CONSTANTS`), and the hybrid reserve on cases where
    the rule matters, the evidence protection included (``_reserve_hybrid_hits``). It is part of every rung's
    settings fingerprint, so pins built under other retrieval code are not
    found, and :meth:`RetrievalPins.check` refuses a file whose header records
    another recipe. Until 2026-10-06 the fingerprint covered settings only
    (audit H6/C3), and then no handler code: the handler fixes of that day
    moved the chunks of 83 of 120 questions under an unchanged fingerprint
    (audit wave 2). And the naming tables of every registered domain
    (:func:`name_conventions_part`), which the sample's behaviour reads but
    covers only as far as its cast does (audit wave 5).
    """
    state = _recipe_code_state()
    tables = name_conventions_part()
    if not (_same_state(_RECIPE_MEMO.get("state"), state) and _RECIPE_MEMO.get("tables") == tables):
        _RECIPE_MEMO.update(state=state, tables=tables, recipe=_compute_retrieval_recipe(tables))
    recipe: str = _RECIPE_MEMO["recipe"]
    return recipe


def name_conventions_part() -> str:
    """The naming tables the retrieval reads names by, every registered domain's, as canonical JSON.

    Each distinct set of conventions once (the universal ones and every
    registered domain's), through the extraction recipe's own lookup
    (:func:`~chaoscypher_core.services.sources.engine.extraction.domain_resolver.registered_name_conventions`),
    every table of each (:meth:`~chaoscypher_core.services.sources.engine.deduplication.name_identity.NameConventions.as_dict`).
    The handlers read a question and the entity names by the domain's
    titles, gendered titles, generation words, name forms and relation
    words; until 2026-10-06 the recipe ran the sample under the literary
    domain's tables without hashing them, so dropping ``vicomte``,
    ``vicomtesse``, ``duc`` and ``duchesse`` from literary's titles moved 4 of
    120 benchmark contexts under the same recipe (audit wave 5). Read on
    every :func:`retrieval_recipe` call (a few milliseconds), so a table
    changed in-process recomputes the recipe too.
    """
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        registered_name_conventions,
    )

    return json.dumps(
        [conv.as_dict() for conv in registered_name_conventions()],
        sort_keys=True,
        ensure_ascii=False,
    )


def _compute_retrieval_recipe(tables: str | None = None) -> str:
    """The hash :func:`retrieval_recipe` returns, computed (see there).

    ``tables`` is :func:`name_conventions_part` (read now when not given).
    """
    from chaoscypher_core.adapters.sqlite.repos import search as search_repo
    from chaoscypher_core.benchmark import chat_prompt
    from chaoscypher_core.benchmark.retrieval_sample import sample_retrievals
    from chaoscypher_core.services.sources.engine.extraction.utils import sentence_splitter
    from chaoscypher_core.services.workflows.tools.engine import chunk_hydration, context_render
    from chaoscypher_core.services.workflows.tools.engine.handlers import graphrag_handlers

    parts = [
        context_render.render_retrieved_context(
            {
                **copy.deepcopy(_RECIPE_CONTEXT),
                "retrieval_stats": {**_RECIPE_CONTEXT["retrieval_stats"], "shape": shape},
            },
            explain=explain,
            graph_last=graph_last,
        )
        for shape in _RECIPE_SHAPES
        for explain in (False, True)
        for graph_last in (False, True)
    ]
    parts.append(chat_prompt.format_retrieved_context(copy.deepcopy(_RECIPE_CONTEXT)))
    parts.append(repr(chunk_hydration.format_chunk_content(_RECIPE_CHUNK_TEXT, "book.txt", "C3")))
    parts.append(repr(sentence_splitter.split_into_sentences(_RECIPE_CHUNK_TEXT)))
    for module, names in recipe_constants():
        parts.extend(_constant_repr(getattr(module, name)) for name in names)
    parts.extend(_constant_repr(getattr(search_repo, name)) for name in SEARCH_RECIPE_CONSTANTS)
    parts.append(search_repo._fts5_query(_RECIPE_QUESTION))  # noqa: SLF001 - the keyword arm's own query builder
    for case in copy.deepcopy(_RECIPE_RESERVE):
        reserved = graphrag_handlers.GraphRAGToolHandlers._reserve_hybrid_hits(*case)  # noqa: SLF001 - the reserve rule itself
        parts.append(repr([c["chunk_id"] for c in reserved]))
    parts.extend(sample_retrievals())
    parts.append(tables if tables is not None else name_conventions_part())
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


def recipe_constants() -> tuple[tuple[ModuleType, tuple[str, ...]], ...]:
    """Each module the recipe hashes constants of, with their names, in hashing order.

    The one pairing of a module with its constant list: the handlers'
    (:data:`RETRIEVAL_RECIPE_CONSTANTS`), the shape router's
    (:data:`SHAPE_RECIPE_CONSTANTS`), the graph blocks'
    (:data:`BLOCK_RECIPE_CONSTANTS`), the shape names'
    (:data:`SHAPES_RECIPE_CONSTANTS`) and the renderer's
    (:data:`RENDER_RECIPE_CONSTANTS`). The recipe hashes through it and the
    guard test that changes each constant in turn reads it too, so a list
    added here is both hashed and tested.
    """
    from chaoscypher_core import question_shapes
    from chaoscypher_core.services.workflows.tools.engine import context_render
    from chaoscypher_core.services.workflows.tools.engine.handlers import (
        graphrag_blocks,
        graphrag_handlers,
        question_shape,
    )

    return (
        (graphrag_handlers, RETRIEVAL_RECIPE_CONSTANTS),
        (question_shape, SHAPE_RECIPE_CONSTANTS),
        (graphrag_blocks, BLOCK_RECIPE_CONSTANTS),
        (question_shapes, SHAPES_RECIPE_CONSTANTS),
        (context_render, RENDER_RECIPE_CONSTANTS),
    )


def _constant_repr(value: Any) -> str:
    """A constant as stable text: a set sorted, anything else as its repr."""
    return repr(sorted(value) if isinstance(value, frozenset | set) else value)


def retrieval_settings(retrieval: str, embedder: str, settings: EngineSettings) -> dict[str, Any]:
    """Everything that decides what a rung retrieves, as plain JSON values.

    The naive rung reads its constants and its splitter (by behaviour,
    :func:`~chaoscypher_core.benchmark.retrieval.naive_recipe`, the value its
    index's READY marker records, and the llama-index version); the graphrag
    and hybrid rungs read the GraphRAG and search settings and the edge query
    batch limit.
    Over-inclusion is safe (a changed setting asks for new pins); a missing
    field would serve stale pins, so whole sections are included.
    """
    payload: dict[str, Any] = {
        "retrieval": retrieval,
        "embedder": embedder,
        "chunk_limit": settings.graphrag.chunk_limit,
        # what turns the retrieval into text (renderer, chunk formatter, splitter, word
        # lists): every rung, the naive one's [S] labels included
        "retrieval_recipe": retrieval_recipe(),
    }
    if retrieval == "naive":
        payload["naive"] = {
            "top_k": NAIVE_TOP_K,
            "chunk_size": NAIVE_CHUNK_SIZE,
            "chunk_overlap": NAIVE_CHUNK_OVERLAP,
            # the splitter by what it does, as the naive index's READY records it (audit wave 2)
            "splitter_recipe": naive_recipe(),
            "llama_index": llama_index_version(),
        }
        return payload
    graphrag = settings.graphrag.model_dump(mode="json")
    payload["graphrag"] = {
        k: v
        for k, v in sorted(graphrag.items())
        if k not in PROMPT_ONLY_GRAPHRAG_FIELDS and k not in RESOLUTION_GRAPHRAG_FIELDS
    }
    search = settings.search.model_dump(mode="json")
    payload["search"] = {
        k: v for k, v in sorted(search.items()) if k not in MACHINE_LOCAL_SEARCH_FIELDS
    }
    # the size the index and the questions are embedded at is the embedder's own (audit R1):
    # search.vector_dimensions holds the 1024 base while a pack indexes qwen3-embedding:4b at 2560
    payload["search"]["vector_dimensions"] = _embedder_dimensions(embedder)
    # the handler reads this one from the app settings, not the engine's
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.benchmark.reindex import reindex_recipe

    # what the index's node and chunk vectors were embedded from: a text change
    # asks for new pins
    payload["reindex_recipe"] = reindex_recipe()

    payload["graphrag_edge_query_limit"] = get_settings().batching.graphrag_edge_query_limit
    return payload


def _embedder_dimensions(embedder: str) -> int:
    """The embedder's native vector size, the size a graph rung indexes at.

    Raises:
        PinError: When the size is not known (the pack could not be indexed either).
    """
    from chaoscypher_core.benchmark.reference import ReferencePackError, embedder_dimensions

    try:
        return embedder_dimensions(embedder)
    except ReferencePackError as exc:
        raise PinError(str(exc)) from exc


def retrieval_fingerprint(retrieval: str, embedder: str, settings: EngineSettings) -> str:
    """A short, stable hash of :func:`retrieval_settings`."""
    blob = json.dumps(retrieval_settings(retrieval, embedder, settings), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def pin_file(pins_dir: Path, retrieval: str, fingerprint: str) -> Path:
    """Where the pins for one rung and settings fingerprint live."""
    return pins_dir / f"{retrieval}-{fingerprint}.json"


_INDEX_DIGEST_MEMO: dict[tuple[object, ...], str] = {}
""":func:`index_digest` by the database file's stamp (:func:`_index_file_stamp`), within a process."""


def _index_file_stamp(db_path: Path) -> tuple[object, ...]:
    """What says a database file is unchanged: its path, and its and its WAL's inode, mtime and size.

    A write in WAL mode lands in ``<db>-wal`` and leaves the main file as
    it was until a checkpoint, so the WAL's stamp is part of it; a file
    replaced by another (a rename) has another inode.
    """
    stamp: list[object] = [str(db_path.resolve())]
    for path in (db_path, db_path.with_name(db_path.name + "-wal")):
        try:
            st = path.stat()
        except FileNotFoundError:
            stamp.append(None)
            continue
        stamp.append((st.st_ino, st.st_mtime_ns, st.st_size))
    return tuple(stamp)


def index_digest(db_path: Path) -> str:
    """A digest of what an indexed copy retrieves from (:func:`_index_digest_uncached`), memoised per file stamp.

    ``pin_retrieval --if-missing`` asks for it once per rung and lever
    variant, about six times a run, and each read hashed the whole copy
    again (2.9 to 3.9 s at 179,000 chunks, audit wave 6). Within a process
    an unchanged file (:func:`_index_file_stamp`) is hashed once.
    """
    stamp = _index_file_stamp(db_path)
    digest = _INDEX_DIGEST_MEMO.get(stamp)
    if digest is None:
        digest = _index_digest_uncached(db_path)
        _INDEX_DIGEST_MEMO[stamp] = digest
    return digest


def _index_digest_uncached(db_path: Path) -> str:
    """A digest of what an indexed copy retrieves from: its vectors and its full-text indexes.

    Reads sqlite-vec's shadow tables directly, so no extension is needed.
    Two indexes built from the same graph on different runs hash
    differently when the GPU gave slightly different vectors, which is the
    drift pins exist to remove. The keyword arm is part of it too, when the
    copy has it (the search repository's
    :data:`~chaoscypher_core.adapters.sqlite.repos.search.FULLTEXT_INDEXES`):
    each content table's rows (the node rows: label, properties with the
    aliases, searchable text; the chunk rows), since a node index left stale
    by the identity resolver served other keyword seeds from the same
    vectors (audit wave 2); and each FTS5 index's own per-item token counts
    (``<index>_docsize``, by item id), since a dropped or emptied index over
    intact content tables digested as the healthy one, so an ``--if-missing``
    pin build kept pins built from the other (audit wave 4). And the graph
    tables the rung reads (:data:`INDEX_GRAPH_TABLES`), every column but
    vectors and timestamps, by id (audit wave 5).
    """
    from chaoscypher_core.adapters.sqlite.repos.search import FULLTEXT_INDEXES

    digest = hashlib.sha256()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for table in _VECTOR_TABLES:
            rows = con.execute(f'SELECT * FROM "{table}_rowids" ORDER BY rowid').fetchall()
            digest.update(repr(rows).encode("utf-8"))
            for (blob,) in con.execute(
                f'SELECT vectors FROM "{table}_vector_chunks00" ORDER BY rowid'
            ):
                digest.update(bytes(blob))
        tables = {name for (name,) in con.execute("SELECT name FROM sqlite_master")}
        for fx in FULLTEXT_INDEXES:
            if fx.content not in tables:
                continue
            columns = ", ".join((fx.key, *fx.columns))
            digest.update(fx.content.encode("utf-8"))
            for row in con.execute(f'SELECT {columns} FROM "{fx.content}" ORDER BY {fx.key}'):
                digest.update(repr(row).encode("utf-8"))
            sizes = f"{fx.fts}_docsize"
            if sizes not in tables:
                digest.update(f"{sizes}:absent".encode())
                continue
            digest.update(sizes.encode("utf-8"))
            for row in con.execute(
                f'SELECT c.{fx.key}, d.sz FROM "{sizes}" d '
                f'LEFT JOIN "{fx.content}" c ON c.{fx.rowid} = d.id ORDER BY c.{fx.key}, d.id'
            ):
                digest.update(repr(row).encode("utf-8"))
        for table in INDEX_GRAPH_TABLES:
            if table not in tables:
                digest.update(f"{table}:absent".encode())
                continue
            kept = [
                name
                for (_, name, *_) in con.execute(f'PRAGMA table_info("{table}")')
                if name not in _INDEX_DIGEST_SKIPPED and not name.endswith(_INDEX_DIGEST_VOLATILE)
            ]
            digest.update(f"{table}:{','.join(kept)}".encode())
            listed = ", ".join(f'"{c}"' for c in kept)
            for row in con.execute(f'SELECT {listed} FROM "{table}" ORDER BY id'):
                digest.update(repr(row).encode("utf-8"))
    finally:
        con.close()
    return digest.hexdigest()[:16]


def dir_digest(path: Path) -> str:
    """A digest of every file under a directory (the naive rung's persisted index)."""
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(file_sha256(item).encode("ascii"))
    return digest.hexdigest()[:16]


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON via a temp file and a rename, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
            handle.write("\n")
        Path(tmp).chmod(0o644)  # mkstemp makes it private; pins are shared files
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


ROUTER_MODEL_MODES: frozenset[str] = frozenset({"llm", "rules_then_llm"})
"""The ``graphrag.router`` modes whose ``route`` may ask the chat model, so a pin header names it.

A test holds it to ``handlers.question_shape.route`` over every router mode.
"""

_UNCHECKED: Any = object()
"""``RetrievalPins.check``'s default for ``router_model``: a serving caller does not compare it."""


@dataclass
class RetrievalPins:
    """One rung's pinned retrieval for one graph, index, embedder and settings.

    Attributes:
        path: The pin file.
        header: What the pins were built from (see the module docstring).
        entries: ``query id -> {"question": str, "retrieved": dict}``.
    """

    path: Path
    header: dict[str, Any]
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> RetrievalPins:
        """Read a pin file.

        Raises:
            PinError: When the file is not a pin file of this schema.
        """
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema") != PIN_SCHEMA:
            msg = f"{path}: not a schema-{PIN_SCHEMA} pin file"
            raise PinError(msg)
        header = {k: v for k, v in raw.items() if k != "pins"}
        return cls(path=path, header=header, entries=dict(raw.get("pins") or {}))

    def check(
        self,
        *,
        graph_sha256: str,
        embedder: str,
        retrieval: str,
        index_digest: str | None = None,
        recipe: str | None = None,
        router_model: Any = _UNCHECKED,
    ) -> None:
        """Refuse to serve a run on another graph, embedder, rung, index, retrieval or splitter recipe.

        Args:
            graph_sha256: The run's graph snapshot.
            embedder: The run's embedder.
            retrieval: The run's rung.
            index_digest: The index the caller would build from, when it has one
                (a pin build with ``--if-missing``); serving a pin reads no index,
                so a serving caller leaves it out.
            recipe: The current :func:`retrieval_recipe` (computed when not given);
                the header's ``settings`` must record the same one. A naive file
                must also record today's
                :func:`~chaoscypher_core.benchmark.retrieval.naive_recipe`.
            router_model: The model the shape router would ask in a build now
                (:func:`router_model_record`, None when it asks none), when the
                caller builds pins (``--if-missing``): a header that records
                another one, or none at all (a file from before the field), is
                stale. Not in the fingerprint, which consumers compute from
                their own ``settings.llm``; a serving caller leaves it out.

        Raises:
            PinError: Naming every field that differs.
        """
        wanted = {"graph_sha256": graph_sha256, "embedder": embedder, "retrieval": retrieval}
        if index_digest is not None:
            wanted["index_digest"] = index_digest
        wrong = {k: (self.header.get(k), v) for k, v in wanted.items() if self.header.get(k) != v}
        built = (self.header.get("settings") or {}).get("retrieval_recipe")
        current = recipe if recipe is not None else retrieval_recipe()
        if built != current:
            wrong["retrieval_recipe"] = (built, current)
        if router_model is not _UNCHECKED and (
            "router_model" not in self.header or self.header["router_model"] != router_model
        ):
            wrong["router_model"] = (self.header.get("router_model", "unrecorded"), router_model)
        if retrieval == "naive":
            naive_built = ((self.header.get("settings") or {}).get("naive") or {}).get(
                "splitter_recipe"
            )
            if naive_built != naive_recipe():
                wrong["splitter_recipe"] = (naive_built, naive_recipe())
        if wrong:
            detail = ", ".join(f"{k}: pinned {a!r}, run {b!r}" for k, (a, b) in wrong.items())
            msg = f"{self.path.name} was built for another run ({detail})"
            raise PinError(msg)

    def lookup(self, query_id: str, question: str) -> dict[str, Any] | None:
        """The pinned ``retrieved`` dict, or None when absent or the question was reworded."""
        entry = self.entries.get(query_id)
        if entry is None or entry.get("question") != question:
            return None
        retrieved: dict[str, Any] = copy.deepcopy(entry["retrieved"])
        return retrieved

    def missing(self, questions: Iterable[tuple[str, str]]) -> list[str]:
        """Ids of ``(query id, question)`` pairs this file cannot serve."""
        return [qid for qid, text in questions if self.lookup(qid, text) is None]

    def put(self, query_id: str, question: str, retrieved: dict[str, Any]) -> None:
        """Pin one question's retrieval (a JSON round trip, so it is served as stored)."""
        self.entries[query_id] = {
            "question": question,
            "retrieved": json.loads(json.dumps(retrieved, ensure_ascii=False)),
        }

    def save(self) -> None:
        """Write the file atomically, with the header's count updated."""
        self.header["count"] = len(self.entries)
        _write_json_atomic(self.path, {**self.header, "pins": self.entries})

    def content_digest(self) -> str:
        """A digest of what the file serves (every question and its retrieval)."""
        blob = json.dumps(self.entries, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def provenance(self) -> dict[str, Any]:
        """What a scored row records about the pins it was served.

        ``oracle`` is True for a ceiling file (``oracle_pins.py`` swaps in the
        chunks holding each answer and keeps the real file's fingerprint), so
        such a row never passes for a real retrieval; ``content_digest`` tells
        two files of one name apart.
        """
        keys = ("fingerprint", "graph_sha256", "index_digest", "embedder", "built_at", "pack")
        return {
            "file": self.path.name,
            **{k: self.header.get(k) for k in keys},
            "retrieval_recipe": (self.header.get("settings") or {}).get("retrieval_recipe"),
            "oracle": bool(self.header.get("oracle")),
            "content_digest": self.content_digest(),
        }


def router_model_record(settings: EngineSettings, *, seed: int | None) -> dict[str, Any] | None:
    """What a pin header records about the shape router's model: provider, model and seed, or None.

    None when ``graphrag.router`` never asks a model (``off``, ``rules``:
    not in :data:`ROUTER_MODEL_MODES`);
    otherwise the settings' chat provider and its chat model, and the seed the
    router decodes at (temperature 0). Recorded in the header and compared by
    a pin build (:meth:`RetrievalPins.check`), never in the fingerprint.
    """
    if settings.graphrag.router not in ROUTER_MODEL_MODES:
        return None
    provider = settings.llm.chat_provider
    return {
        "provider": provider,
        "model": getattr(settings.llm, f"{provider}_chat_model", None),
        "seed": seed,
    }


def new_pins(
    pins_dir: Path,
    *,
    retrieval: str,
    embedder: str,
    settings: EngineSettings,
    graph_sha256: str,
    index_digest: str | None,
    pack: str | None = None,
) -> RetrievalPins:
    """An empty pin file for one rung, named by its settings fingerprint."""
    fingerprint = retrieval_fingerprint(retrieval, embedder, settings)
    header = {
        "schema": PIN_SCHEMA,
        "pack": pack,
        "retrieval": retrieval,
        "embedder": embedder,
        "fingerprint": fingerprint,
        "graph_sha256": graph_sha256,
        "index_digest": index_digest,
        "settings": retrieval_settings(retrieval, embedder, settings),
        "built_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return RetrievalPins(path=pin_file(pins_dir, retrieval, fingerprint), header=header)


def find_pins(
    pins_dir: Path, *, retrieval: str, embedder: str, settings: EngineSettings
) -> RetrievalPins | None:
    """The pin file for this rung and these settings, or None when there is none."""
    path = pin_file(pins_dir, retrieval, retrieval_fingerprint(retrieval, embedder, settings))
    return RetrievalPins.load(path) if path.exists() else None


def query_vector_identity(embedder: str, provider: Any = None) -> str:
    """What decides a query's vector besides its text: the embedder, its query prefix and dimensions.

    ``provider`` is the embedding provider that embeds: its ``query_prefix``
    (part of the embedding provider protocol, empty for every provider today)
    and ``vector_dimensions`` are read when they are plain values; two
    providers of one model with another prefix or truncation give other
    vectors, so they get other cache keys (audit H6).
    """
    prefix = getattr(provider, "query_prefix", None)
    dims = getattr(provider, "vector_dimensions", None)
    return json.dumps(
        {
            "embedder": embedder,
            "query_prefix": prefix if isinstance(prefix, str) else "",
            "dimensions": dims if isinstance(dims, int) and not isinstance(dims, bool) else None,
        },
        sort_keys=True,
    )


@dataclass
class QueryVectorCache:
    """Query embeddings computed once per embedder and text.

    Every pin build of a pack (each rung, each settings variant) embeds the
    same question to the same vector, so two variants' pins differ only by
    what the variant changes. Vectors are stored as base64 float32, and the
    stored value is what is returned even on the first call, so the first
    build and later ones see identical numbers.

    Attributes:
        path: The cache file (``query-vectors-<embedder slug>[-<N>d].json``).
        embedder: ``provider/model`` the vectors came from.
        vectors: ``sha256(identity, text)[:16] -> base64 float32``.
        dimensions: The size every vector has, when the cache is for an index
            of a known size: it names the file, is checked on load, and a
            vector of another length is refused, so 1024-d question vectors
            are never served to a 2560-d index (audit R8).
    """

    path: Path
    embedder: str
    vectors: dict[str, str] = field(default_factory=dict)
    dimensions: int | None = None

    @classmethod
    def for_embedder(
        cls, pins_dir: Path, embedder: str, *, dimensions: int | None = None
    ) -> QueryVectorCache:
        """Load this embedder's cache (of ``dimensions``-sized vectors) from ``pins_dir``, or start one.

        Raises:
            PinError: When the file holds another embedder's or another size's vectors.
        """
        size = f"-{dimensions}d" if dimensions else ""
        path = pins_dir / f"query-vectors-{embedder_slug(embedder)}{size}.json"
        if not path.exists():
            return cls(path=path, embedder=embedder, dimensions=dimensions)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("embedder") != embedder:
            msg = f"{path.name} holds {raw.get('embedder')!r} vectors, not {embedder!r}"
            raise PinError(msg)
        if raw.get("dimensions") != dimensions:
            msg = f"{path.name} holds {raw.get('dimensions')}-d vectors, not {dimensions}-d"
            raise PinError(msg)
        return cls(
            path=path,
            embedder=embedder,
            vectors=dict(raw.get("vectors") or {}),
            dimensions=dimensions,
        )

    @staticmethod
    def _key(identity: str, text: str) -> str:
        """The cache key of a question: SHA-256 of the embedding identity and the text, 16 hex chars."""
        return hashlib.sha256(f"{identity}\x1f{text}".encode()).hexdigest()[:16]

    @staticmethod
    def _decode(blob: str) -> list[float]:
        """A stored vector (base64 of float32) back to floats."""
        values = array("f")
        values.frombytes(base64.b64decode(blob))
        return list(values)

    def wrap(
        self, embed: Callable[[str], Awaitable[Any]], *, provider: Any = None
    ) -> Callable[[str], Awaitable[dict[str, list[float]]]]:
        """An embedding callback that serves cached vectors and caches new ones.

        ``provider`` is the embedding provider behind ``embed``; its query
        prefix and dimensions are part of every key (:func:`query_vector_identity`).
        """
        identity = query_vector_identity(self.embedder, provider)

        async def cached(text: str) -> dict[str, list[float]]:
            """The vector for ``text``: from the cache, or embedded once and cached."""
            key = self._key(identity, text)
            if key not in self.vectors:
                result = await embed(text)
                if isinstance(result, dict):
                    vector = result.get("embedding") or []
                else:
                    vector = getattr(result, "embedding", result) or []
                self.vectors[key] = base64.b64encode(array("f", vector).tobytes()).decode("ascii")
            served = self._decode(self.vectors[key])
            if self.dimensions and len(served) != self.dimensions:
                msg = (
                    f"{self.path.name}: a {len(served)}-d vector for a {self.dimensions}-d index "
                    f"({text[:60]!r}); the embedder is not embedding at the index's size"
                )
                raise PinError(msg)
            return {"embedding": served}

        return cached

    def save(self) -> None:
        """Write the cache atomically."""
        _write_json_atomic(
            self.path,
            {"embedder": self.embedder, "dimensions": self.dimensions, "vectors": self.vectors},
        )


__all__ = [
    "BLOCK_RECIPE_CONSTANTS",
    "INDEX_GRAPH_TABLES",
    "MACHINE_LOCAL_SEARCH_FIELDS",
    "PINS_DIR",
    "PIN_SCHEMA",
    "PROMPT_ONLY_GRAPHRAG_FIELDS",
    "RENDER_RECIPE_CONSTANTS",
    "RESOLUTION_GRAPHRAG_FIELDS",
    "RETRIEVAL_RECIPE_CONSTANTS",
    "ROUTER_MODEL_MODES",
    "SEARCH_RECIPE_CONSTANTS",
    "SHAPES_RECIPE_CONSTANTS",
    "SHAPE_RECIPE_CONSTANTS",
    "PinError",
    "QueryVectorCache",
    "RetrievalPins",
    "dir_digest",
    "file_sha256",
    "find_pins",
    "index_digest",
    "name_conventions_part",
    "new_pins",
    "pin_file",
    "query_vector_identity",
    "recipe_constants",
    "retrieval_fingerprint",
    "retrieval_recipe",
    "retrieval_settings",
    "router_model_record",
]
