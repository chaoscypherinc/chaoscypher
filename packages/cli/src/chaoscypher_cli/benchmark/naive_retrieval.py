# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The naive rung of the retrieval comparison: the LlamaIndex tutorial pipeline.

``SentenceSplitter`` over the dataset's corpus text (the product's sentence
boundaries, whitespace kept), a ``VectorStoreIndex`` embedded with the pack's
embedder through the Ollama embedding adapter at the embedder's native size
(the size the graph rungs index at), pure vector similarity, the top
:data:`~chaoscypher_core.benchmark.retrieval.NAIVE_TOP_K` chunks. Nothing of ChaosCypher's retrieval is involved: it is what a
prospective user already has.

The index is built once per corpus and embedder and persisted under the
spec's ``cache_dir`` with a ``READY`` marker naming what it was built from
(the splitter by its behaviour, :func:`~chaoscypher_core.benchmark.retrieval.naive_recipe`,
and the llama-index version); a later run with the same marker loads it and
embeds only the question. The
retrieved chunks are headed ``[CHUNK Cn | <corpus file>]`` by the product's
own :func:`~chaoscypher_core.services.workflows.tools.engine.chunk_hydration.format_chunk_content`,
so the three rungs render through one context renderer and the citation
check reads every rung alike.

llama-index is the CLI package's ``benchmark`` extra; this module imports it
lazily inside the builder so the rest of the benchmark code loads without it.
Core never imports this module: the factory is handed to the benchmark bridge
and the local chat dataset by their builders.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_cli.benchmark.discovery import load_dataset_bundle
from chaoscypher_core.benchmark.reference import (
    READY_FILE,
    ReferencePackError,
    embedder_dimensions,
    split_model_id,
)
from chaoscypher_core.benchmark.retrieval import (
    NAIVE_CHUNK_OVERLAP,
    NAIVE_CHUNK_SIZE,
    NAIVE_TOP_K,
    llama_index_version,
    naive_recipe,
    naive_splitter,
    require_complete_retrieval,
)
from chaoscypher_core.services.workflows.tools.engine.chunk_hydration import format_chunk_content
from chaoscypher_core.utils.query_text import normalize_query


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.benchmark.retrieval import NaiveIndexSpec, NaiveRetriever
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class NaiveRetrievalUnavailableError(RuntimeError):
    """llama-index is not installed: the CLI's ``benchmark`` extra is missing."""


def _llama_index() -> Any:
    """The llama-index modules, imported on first use.

    Raises:
        NaiveRetrievalUnavailableError: When the extra is not installed.
    """
    try:
        from llama_index.core import (
            Document,
            StorageContext,
            VectorStoreIndex,
            load_index_from_storage,
        )
    except ImportError as exc:
        msg = (
            "naive retrieval needs llama-index: install the CLI's benchmark extra "
            "(uv sync --all-packages --extra dev, or pip install 'chaoscypher-cli[benchmark]')"
        )
        raise NaiveRetrievalUnavailableError(msg) from exc
    return {
        "Document": Document,
        "StorageContext": StorageContext,
        "VectorStoreIndex": VectorStoreIndex,
        "load_index_from_storage": load_index_from_storage,
    }


def _ollama_embedder(embedder: str, settings: EngineSettings) -> Any:
    """LlamaIndex's Ollama embedding adapter for the pack's embedder, on the embedding host.

    The host is resolved as the product resolves it (``embedding.api_base``,
    then the embedding instance, then the primary Ollama), so the naive rung
    embeds where the graphrag and hybrid rungs do. It used the primary
    (chat) Ollama until 2026-10-03, which put the embedder beside the chat
    model on a two-GPU split and timed out three 3090 rows.
    """
    from llama_index.embeddings.ollama import OllamaEmbedding

    from chaoscypher_core.adapters.embedding.factory import resolve_ollama_base_url

    provider, model = split_model_id(embedder)
    if provider != "ollama":
        msg = f"the naive rung embeds through Ollama; got embedder {embedder!r}"
        raise ValueError(msg)
    embedder_dimensions(embedder)  # a model of unknown native size is refused, as for the pack
    return OllamaEmbedding(model_name=model, base_url=resolve_ollama_base_url(settings))


def _known_dimensions(embedder: str) -> int | None:
    """The embedder's native size when the registry knows it (a test's fake model it does not)."""
    try:
        return embedder_dimensions(embedder)
    except ReferencePackError:
        return None


def _marker(spec: NaiveIndexSpec, corpus_sha256: str) -> dict[str, Any]:
    """What READY records: a change in any of it rebuilds the index.

    The splitter is recorded by what it does (:func:`naive_recipe`: the
    chunks it cuts from a fixed sample) and by the llama-index version, the
    same values the naive pins' fingerprint carries. Until 2026-10-06 it was
    a hand-written ``sentence_tokenizer`` label that no splitter change
    moved (audit wave 2).
    """
    return {
        "corpus_id": spec.corpus_id,
        "corpus_sha256": corpus_sha256,
        "embedder": spec.embedder,
        "vector_dimensions": _known_dimensions(spec.embedder),
        "splitter_recipe": naive_recipe(),
        "llama_index": llama_index_version(),
        "chunk_size": NAIVE_CHUNK_SIZE,
        "chunk_overlap": NAIVE_CHUNK_OVERLAP,
        "top_k": NAIVE_TOP_K,
    }


def _ready(cache_dir: Path, marker: dict[str, Any]) -> bool:
    """Whether ``cache_dir`` holds an index built from exactly ``marker``."""
    try:
        recorded = json.loads((cache_dir / READY_FILE).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return False
    return bool(recorded == marker)


def _build_or_load(
    spec: NaiveIndexSpec, embed_model: Any, corpus_text: str, corpus_name: str
) -> Any:
    """The persisted index for ``spec``, built when READY does not match (blocking)."""
    li = _llama_index()
    marker = _marker(spec, hashlib.sha256(corpus_text.encode("utf-8")).hexdigest())
    if _ready(spec.cache_dir, marker):
        storage = li["StorageContext"].from_defaults(persist_dir=str(spec.cache_dir))
        return li["load_index_from_storage"](storage, embed_model=embed_model)
    logger.info("naive_index_building", corpus=spec.corpus_id, embedder=spec.embedder)
    # The product's sentence boundaries, not nltk's punkt: the splitter then
    # numbers sentences the way every other rung's chunks are numbered, and
    # it never opens nltk's bundled data, which nltk 3.10 refuses when the
    # files are hardlinks (every uv install on Linux and Windows links them).
    # The whitespace between sentences is kept (core's sentences_keeping_whitespace),
    # so a node's text is the corpus's own text, as with nltk's tokenizer.
    # One splitter definition (core's naive_splitter): the marker's recipe measures it.
    splitter = naive_splitter()
    document = li["Document"](text=corpus_text, metadata={"file_name": corpus_name})
    index = li["VectorStoreIndex"].from_documents(
        [document], transformations=[splitter], embed_model=embed_model, show_progress=False
    )
    _check_dimensions(index, marker["vector_dimensions"], spec.embedder)
    spec.cache_dir.mkdir(parents=True, exist_ok=True)
    index.storage_context.persist(persist_dir=str(spec.cache_dir))
    (spec.cache_dir / READY_FILE).write_text(json.dumps(marker) + "\n", encoding="utf-8")
    return index


def _check_dimensions(index: Any, expected: int | None, embedder: str) -> None:
    """Refuse an index whose vectors are not the embedder's native size (the graph rungs' size).

    Raises:
        ValueError: When a stored vector's length differs from ``expected``.
    """
    if expected is None:
        return
    for node_id in list(index.index_struct.nodes_dict)[:1]:
        got = len(index.vector_store.get(node_id))
        if got != expected:
            msg = (
                f"the naive index embedded {embedder!r} at {got} dimensions, not its native "
                f"{expected}: every rung must compare the same embeddings"
            )
            raise ValueError(msg)


async def build_naive_retriever(
    spec: NaiveIndexSpec,
    settings: EngineSettings,
    *,
    embed_model: Any | None = None,
    corpus_text: str | None = None,
    corpus_name: str | None = None,
) -> NaiveRetriever:
    """Build (or load) the naive index for ``spec`` and return its retriever.

    Args:
        spec: The corpus, the embedder and the cache directory.
        settings: Engine settings; the Ollama URL comes from them.
        embed_model: A LlamaIndex embedding model to use instead of the
            Ollama adapter (tests pass a fake; nothing here calls Ollama then).
        corpus_text: The corpus to split, instead of the dataset's file.
        corpus_name: The file name the chunk headers carry.

    Returns:
        ``async (question) -> chunks``: the top ``NAIVE_TOP_K`` chunks as
        dicts with ``content`` (headed and sentence-numbered), ``chunk_id``,
        ``chunk_alias``, ``score`` and ``retrieval_origin: "naive"``.
    """
    if corpus_text is None or corpus_name is None:
        bundle = load_dataset_bundle(spec.corpus_id)
        corpus_text = (
            corpus_text
            if corpus_text is not None
            else bundle.corpus_path.read_text(encoding="utf-8")
        )
        corpus_name = corpus_name or bundle.corpus_path.name
    model = embed_model if embed_model is not None else _ollama_embedder(spec.embedder, settings)
    index = await asyncio.to_thread(_build_or_load, spec, model, corpus_text, corpus_name)
    retriever = index.as_retriever(similarity_top_k=NAIVE_TOP_K)
    file_name = corpus_name
    indexed = len(index.index_struct.nodes_dict)

    async def retrieve(question: str) -> list[dict[str, Any]]:
        """The top chunks for ``question``, headed like the product's chunks.

        Raises:
            DegradedRetrievalError: When fewer than ``NAIVE_TOP_K`` chunks come
                back from an index holding more.
        """
        nodes = await retriever.aretrieve(normalize_query(question))  # NFC like every rung
        chunks: list[dict[str, Any]] = []
        for i, hit in enumerate(nodes[:NAIVE_TOP_K]):
            text = hit.node.get_content()
            alias = f"C{i}"
            content, sentence_count = format_chunk_content(text, file_name, alias)
            chunks.append(
                {
                    "chunk_id": hit.node.node_id,
                    "chunk_alias": alias,
                    "content": content,
                    "original_content": text,
                    "filename": file_name,
                    "sentence_count": sentence_count,
                    "score": float(hit.score) if hit.score is not None else 0.0,
                    "retrieval_origin": "naive",
                }
            )
        require_complete_retrieval(
            {"chunks": chunks}, mode="naive", chunk_limit=NAIVE_TOP_K, indexed_chunks=indexed
        )
        return chunks

    return retrieve


async def naive_retriever_factory(spec: NaiveIndexSpec, settings: EngineSettings) -> NaiveRetriever:
    """The factory the bridge and the local chat dataset are given: Ollama embedder, dataset corpus."""
    return await build_naive_retriever(spec, settings)


__all__ = [
    "NaiveRetrievalUnavailableError",
    "build_naive_retriever",
    "naive_retriever_factory",
]
