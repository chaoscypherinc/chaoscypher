# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The retrieval rungs of the grounded-chat benchmark.

The leaderboard's "Where the graph matters" comparison answers the same
questions with the same prompt and the same context renderer under three
retrievals that differ only in what they retrieve:

- ``graphrag`` (the default): the product's ``graphrag_search``: seed
  entities, personalised PageRank, provenance chunks merged with the hybrid
  hits, entity and relationship lines plus ``graphrag.chunk_limit`` chunks.
- ``hybrid``: ChaosCypher's chunk search without the graph: the hybrid
  (BM25 keyword plus cosine) arm over the pack's chunks, the top
  ``graphrag.chunk_limit``, rendered as chunks only.
- ``naive``: the canonical tutorial pipeline through LlamaIndex: a sentence
  splitter over the corpus text, a vector index built with the pack's
  embedder, pure vector similarity, the top :data:`NAIVE_TOP_K` chunks of
  :data:`NAIVE_CHUNK_SIZE` tokens. Four such chunks hold about as many
  characters as the other rungs' ten, so the rungs differ in what they
  retrieve, not how much; the graphrag rung adds its entity and
  relationship lines on top of that matched budget, which is the thing
  measured.

The budgets are the rungs' definitions, like the probe fixture's padding
width, and live here as constants rather than as settings: a setting would
let what is measured drift per machine.

The naive rung needs llama-index, a benchmark-only optional extra of the CLI
package that the product never imports. Core therefore defines the index
spec, the factory protocol and the naive splitter (:func:`naive_splitter`,
imported lazily, with :func:`naive_recipe`, the hash of its behaviour that the
naive index's READY marker and the naive pins' fingerprint both record); the
CLI provides the factory (``chaoscypher_cli.benchmark.naive_retrieval``) and
hands it to the bridge and the local chat dataset.
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, get_args


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        GraphRAGToolHandlers,
    )
    from chaoscypher_core.services.workflows.tools.engine.handlers.question_shape import (
        ChatCallback,
    )
    from chaoscypher_core.settings import EngineSettings


RetrievalMode = Literal["graphrag", "hybrid", "naive"]

RETRIEVAL_MODES: tuple[str, ...] = get_args(RetrievalMode)
"""The rungs, in the order the comparison lists them (the default first)."""

DEFAULT_RETRIEVAL_MODE: RetrievalMode = "graphrag"


NAIVE_CHUNK_SIZE = 512
"""Tokens per naive chunk (LlamaIndex's ``SentenceSplitter`` default of the tutorials)."""

NAIVE_CHUNK_OVERLAP = 50
"""Token overlap between naive chunks."""

NAIVE_TOP_K = 4
"""Naive chunks per question: about the other rungs' ten 900-character chunks in characters."""

NAIVE_INDEX_PREFIX = "naive-"
"""Directory prefix of a naive index under a pack's ``index/``: ``naive-<embedder slug>``."""


def validate_retrieval_mode(value: object) -> RetrievalMode:
    """``value`` as a retrieval mode, or a ``ValueError`` naming the modes.

    Raises:
        ValueError: When ``value`` is not one of :data:`RETRIEVAL_MODES`.
    """
    if isinstance(value, str) and value in RETRIEVAL_MODES:
        return value  # type: ignore[return-value]  # narrowed by the membership check
    msg = f"retrieval must be one of {', '.join(RETRIEVAL_MODES)}, got {value!r}"
    raise ValueError(msg)


def retrieval_dataset_suffix(mode: str) -> str:
    """What a chat row's dataset id carries for ``mode``.

    Empty for ``graphrag``, so those rows keep today's dataset id and pool
    with every existing row; ``__retrieval_<mode>`` otherwise, so rows never
    pool across modes.
    """
    return "" if validate_retrieval_mode(mode) == DEFAULT_RETRIEVAL_MODE else f"__retrieval_{mode}"


def embedder_slug(model_id: str) -> str:
    """A ``provider/model`` id as a path or dataset-id component."""
    return model_id.replace("/", "_")


class DegradedRetrievalError(ValueError):
    """A retrieval returned less than its rung's budget while the index held more."""


def chunk_budget(mode: str, chunk_limit: int) -> int:
    """The chunks a rung returns per question: :data:`NAIVE_TOP_K` for naive, ``chunk_limit`` else."""
    return NAIVE_TOP_K if validate_retrieval_mode(mode) == "naive" else chunk_limit


FULL_GRAPHRAG_MODE = "full_graphrag"
"""The ``retrieval_stats["mode"]`` of a graphrag retrieval that ran every stage."""

HYBRID_RETRIEVAL_MODE = "hybrid"
"""The ``retrieval_stats["mode"]`` of the hybrid rung (``GraphRAGToolHandlers.hybrid_retrieval``)."""


def require_complete_retrieval(
    retrieved: dict[str, Any], *, mode: str, chunk_limit: int, indexed_chunks: int
) -> None:
    """Refuse a retrieval that is empty, short of its budget, or (graphrag) degraded.

    A degraded retrieval is a fact about the index or the code, not the
    question: an index emptied by a dimension change, a query vector of the
    wrong size, a budget shared with other item types, a stage that raised
    and was swallowed. Until 2026-10-06 such retrievals were pinned and
    served to every model without complaint (audit R8), and the graphrag
    rung's only check was the chunk count: the pipeline swallows exceptions
    at eight sites and falls back to ``vector_only``, which still fills the
    budget from the hybrid arm (audit wave 2).

    On the graphrag rung the retrieval must also have run every stage:
    ``retrieval_stats.mode`` is :data:`FULL_GRAPHRAG_MODE`, the graph context
    is not empty, and ``retrieval_stats.faults`` (the stages that swallowed
    an exception, :mod:`chaoscypher_core.utils.retrieval_faults`) is empty.

    On the hybrid rung the retrieval must carry ``retrieval_stats``
    (``GraphRAGToolHandlers.hybrid_retrieval``; the CLI's live rung and the
    MCP suite both build it) saying :data:`HYBRID_RETRIEVAL_MODE` with no
    fault: a keyword arm that raised still fills the budget from the vector
    arm. Until 2026-10-06 the hybrid rung was checked by its count alone, and
    with the chunk keyword index dropped all 120 benchmark retrievals were
    accepted (audit wave 3). A chunks-only dict (``hybrid_chunk_search``'s
    bare list) carries no record and is refused for that reason.

    Args:
        retrieved: What the rung returned (``chunks``; on graphrag also
            ``retrieval_stats`` and ``graph_context``).
        mode: The rung.
        chunk_limit: ``graphrag.chunk_limit``.
        indexed_chunks: How many chunks the rung's index holds.

    Raises:
        DegradedRetrievalError: When the index holds no chunk, the rung
            returned fewer than ``min(budget, indexed_chunks)``, or a graphrag
            retrieval is degraded (naming why).
    """
    if indexed_chunks <= 0:
        msg = f"the {mode} index holds no chunks: rebuild it before retrieving"
        raise DegradedRetrievalError(msg)
    got = len(retrieved.get("chunks") or [])
    wanted = min(chunk_budget(mode, chunk_limit), indexed_chunks)
    if got < wanted:
        msg = (
            f"the {mode} retrieval returned {got} chunks, fewer than its budget of {wanted} "
            f"while the index holds {indexed_chunks}"
        )
        raise DegradedRetrievalError(msg)
    if mode == "graphrag":
        _require_clean_stats(retrieved, mode, FULL_GRAPHRAG_MODE, need_graph=True)
    elif mode == "hybrid":
        _require_clean_stats(retrieved, mode, HYBRID_RETRIEVAL_MODE, need_graph=False)


def _require_clean_stats(
    retrieved: dict[str, Any], rung: str, wanted_mode: str, *, need_graph: bool
) -> None:
    """Refuse a retrieval whose stats say another mode, no fault record or a swallowed fault.

    The one check of ``retrieval_stats`` for every rung that records them
    (graphrag, hybrid); ``need_graph`` also refuses an empty graph context.

    Raises:
        DegradedRetrievalError: Naming the mode, the empty context or the faults.
    """
    stats = retrieved.get("retrieval_stats") or {}
    problems: list[str] = []
    if stats.get("mode") != wanted_mode:
        problems.append(f"mode {stats.get('mode')!r}, not {wanted_mode!r}")
    if need_graph and not retrieved.get("graph_context"):
        problems.append("an empty graph context")
    faults = stats.get("faults")
    if faults is None:
        problems.append("no fault record (retrieval_stats.faults)")
    elif faults:
        problems.append(f"swallowed exceptions in {', '.join(map(str, faults))}")
    if problems:
        msg = f"the {rung} retrieval is degraded: {'; '.join(problems)}"
        raise DegradedRetrievalError(msg)


def sentences_keeping_whitespace(text: str) -> list[str]:
    """The product's sentences of ``text`` with the whitespace between them kept, so they join back to ``text``.

    LlamaIndex's ``SentenceSplitter`` re-joins its splits with ``""``; fed
    ``split_into_sentences`` (which strips each sentence) it glued every
    boundary ("took his arm.Before he..."), 677 of them on Book One, and the
    [S] renderer then found about one sentence per 1,900-character chunk
    (audit C1). Each piece here runs from one sentence's start to the next
    one's, so ``"".join(pieces) == text`` and the boundaries are the
    product's own.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        split_into_sentences_with_offsets,
    )

    if not text:
        return []
    starts = sorted({o["start"] for o in split_into_sentences_with_offsets(text)} - {0})
    bounds = [0, *[p for p in starts if 0 < p < len(text)], len(text)]
    return [text[a:b] for a, b in itertools.pairwise(bounds) if b > a]


def llama_index_version() -> str | None:
    """The installed ``llama-index-core`` version, or None when the CLI's benchmark extra is absent."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("llama-index-core")
    except PackageNotFoundError:
        return None


def naive_splitter() -> Any:
    """The naive rung's splitter: LlamaIndex's ``SentenceSplitter`` on the product's sentences.

    :data:`NAIVE_CHUNK_SIZE` tokens with :data:`NAIVE_CHUNK_OVERLAP`, split at
    the product's sentence boundaries with the whitespace kept
    (:func:`sentences_keeping_whitespace`). The one place the splitter is
    configured: the naive index is built with it and :func:`naive_recipe`
    measures it.

    Raises:
        ImportError: When llama-index is not installed.
    """
    from llama_index.core.node_parser import SentenceSplitter

    return SentenceSplitter(
        chunk_size=NAIVE_CHUNK_SIZE,
        chunk_overlap=NAIVE_CHUNK_OVERLAP,
        chunking_tokenizer_fn=sentences_keeping_whitespace,
    )


_NAIVE_SAMPLE_PARAGRAPHS = (
    'Mr. Halváne said: "Go!" He went. Dr. Smith (the surgeon) stayed... and wept!',
    "“No,” she said. The end? Yes. Prince Corvin took his arm. Before he spoke, "
    "the old prince had left the room.",
    "Snow fell over the old capital; nobody went out. A letter arrived that nobody read, "
    "and the count gave a dinner in Velmar.",
)


def _naive_sample() -> str:
    """A fixed text of several naive chunks: quotes, abbreviations and paragraph breaks."""
    rounds = [
        f"CHAPTER {i}\n\n"
        + "\n\n".join(f"{p} ({i}.{j})" for j, p in enumerate(_NAIVE_SAMPLE_PARAGRAPHS))
        for i in range(1, 25)
    ]
    return "\n\n".join(rounds)


def naive_recipe() -> str:
    """What the naive splitter makes of a fixed text, as a hash; ``"llama-index-absent"`` without it.

    Computed, never declared, like the retrieval recipe: the chunk texts
    :func:`naive_splitter` cuts from a fixed multi-chunk sample, and the
    llama-index version. The naive index's READY marker and the naive pins'
    fingerprint both record it, so a splitter change (ours or the library's)
    rebuilds the index and asks for new pins. Until 2026-10-06 the marker
    carried a hand-written ``sentence_tokenizer`` label and the fingerprint
    the three constants only (audit wave 2).
    """
    version = llama_index_version()
    if version is None:
        return "llama-index-absent"
    from llama_index.core import Document

    nodes = naive_splitter().get_nodes_from_documents([Document(text=_naive_sample())])
    parts = [version, *(node.get_content() for node in nodes)]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class NaiveIndexSpec:
    """What a naive index is built from and where it is cached.

    Attributes:
        corpus_id: The benchmark dataset whose corpus text is split.
        embedder: ``provider/model`` that embeds the chunks and the questions.
        cache_dir: Directory holding the persisted index and its READY marker.
    """

    corpus_id: str
    embedder: str
    cache_dir: Path


type NaiveRetriever = Callable[[str], Awaitable[list[dict[str, Any]]]]
"""``async (question) -> chunks``: chunk dicts the context renderer reads (``content``)."""

type NaiveRetrieverFactory = Callable[[NaiveIndexSpec, EngineSettings], Awaitable[NaiveRetriever]]
"""Builds (or loads from cache) the naive index for a spec and returns its retriever."""


def retrieval_handlers(
    engine: Any,
    *,
    settings: EngineSettings | None = None,
    embedding_callback: Callable[..., Any] | None = None,
    chat_callback: ChatCallback | None = None,
) -> GraphRAGToolHandlers:
    """The product's retrieval handlers over an open engine, as every benchmark retrieval wires them.

    The one wiring of the CLI's chat stage, the MCP chat suite (and so the
    pin builder) and the reach tools: the engine's graph and search
    repositories, its storage adapter for both the chunk hydration and the
    citations, as in the product's bootstrap (with None the handler returns
    no chunks at all, 2026-09-25), and its database name.

    Args:
        engine: An open engine or CLI context (``graph_repository``,
            ``search_repository``, ``storage_adapter``, ``embedding_service``,
            ``settings``, ``database_name``).
        settings: The settings to retrieve under (a run's resolved levers);
            None is the engine's.
        embedding_callback: The query embedder (the pins' query-vector cache);
            None is the engine's.
        chat_callback: The shape router's model; None decides by the rules
            alone, an abstention being lookup.
    """
    from chaoscypher_core.adapters.embedding import require_embedding_provider
    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        GraphRAGToolHandlers,
    )

    return GraphRAGToolHandlers(
        graph_repository=engine.graph_repository,
        search_repository=engine.search_repository,
        indexing_repository=engine.storage_adapter,
        source_storage=engine.storage_adapter,
        embedding_callback=embedding_callback
        or require_embedding_provider(
            engine.embedding_service, "The benchmark's vector retrieval"
        ).embed,
        settings=engine.settings if settings is None else settings,
        database_name=engine.database_name,
        chat_callback=chat_callback,
    )


__all__ = [
    "DEFAULT_RETRIEVAL_MODE",
    "FULL_GRAPHRAG_MODE",
    "HYBRID_RETRIEVAL_MODE",
    "NAIVE_CHUNK_OVERLAP",
    "NAIVE_CHUNK_SIZE",
    "NAIVE_INDEX_PREFIX",
    "NAIVE_TOP_K",
    "RETRIEVAL_MODES",
    "DegradedRetrievalError",
    "NaiveIndexSpec",
    "NaiveRetriever",
    "NaiveRetrieverFactory",
    "RetrievalMode",
    "chunk_budget",
    "embedder_slug",
    "llama_index_version",
    "naive_recipe",
    "naive_splitter",
    "require_complete_retrieval",
    "retrieval_dataset_suffix",
    "retrieval_handlers",
    "sentences_keeping_whitespace",
    "validate_retrieval_mode",
]
