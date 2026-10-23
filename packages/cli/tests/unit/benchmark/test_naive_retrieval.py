# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The naive rung: a LlamaIndex vector index over the corpus, cached under a READY marker.

A deterministic fake embedder stands in for Ollama (bag of words hashed into
a few dimensions), so retrieval ranks by shared words and nothing here
reaches the network.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, ClassVar

import pytest
from llama_index.core.embeddings import BaseEmbedding

from chaoscypher_cli.benchmark.naive_retrieval import build_naive_retriever
from chaoscypher_core.benchmark.reference import READY_FILE
from chaoscypher_core.benchmark.retrieval import NAIVE_TOP_K, NaiveIndexSpec
from chaoscypher_core.settings import EngineSettings


# Four chapters, each padded past the naive chunk size with words too short for
# the fake embedder to count, so the splitter yields several chunks per chapter
# and the chapter's first chunk is the one its named people make retrievable.
_FILLER = " ".join(["So it is as we go on, and on it is."] * 110)
CORPUS = "\n\n".join(
    [
        "CHAPTER I\n\nAnna Pavlovna Scherer greeted Prince Vasili Kuragin, the first to arrive at "
        "her reception in July. She had a cough and spoke of la grippe, a new word in Petersburg. "
        + _FILLER,
        "CHAPTER II\n\nPierre, the illegitimate son of Count Bezukhov, entered the drawing room. "
        "He had been educated abroad and this was his first appearance in society. " + _FILLER,
        "CHAPTER III\n\nDolokhov, an officer of the Semenov regiment, bet Stevens the Englishman "
        "that he would drink a bottle of rum sitting on the outer window ledge. " + _FILLER,
        "CHAPTER IV\n\nIt was the name day of the countess and her youngest daughter Natasha, "
        "both named Nataly, in the big house on the Povarskaya in Moscow. " + _FILLER,
    ]
)
DIM = 32


class _WordHashEmbedding(BaseEmbedding):
    """Bag of words hashed into DIM buckets; counts every embedding call."""

    calls: ClassVar[int] = 0

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * DIM
        for word in re.findall(r"[a-z]+", text.lower()):
            if len(word) < 4:
                continue
            bucket = int(hashlib.md5(word.encode(), usedforsecurity=False).hexdigest(), 16) % DIM
            vec[bucket] += 1.0
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]

    def _get_query_embedding(self, query: str) -> list[float]:
        type(self).calls += 1
        return self._vector(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        type(self).calls += 1
        return self._vector(text)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_query_embedding(query)

    async def _aget_text_embedding(self, text: str) -> list[float]:
        return self._get_text_embedding(text)


def _spec(tmp_path: Path) -> NaiveIndexSpec:
    return NaiveIndexSpec(
        corpus_id="war_and_peace_tiny",
        embedder="ollama/fake-embedder",
        cache_dir=tmp_path / "index" / "naive-ollama_fake-embedder",
    )


async def _build(tmp_path: Path, corpus: str = CORPUS) -> Any:
    return await build_naive_retriever(
        _spec(tmp_path),
        EngineSettings(),
        embed_model=_WordHashEmbedding(),
        corpus_text=corpus,
        corpus_name="tiny.txt",
    )


@pytest.mark.asyncio
async def test_retrieves_top_k_headed_chunks_ranked_by_similarity(tmp_path: Path) -> None:
    """NAIVE_TOP_K chunks, the best first, headed and sentence-numbered like the product's chunks."""
    retrieve = await _build(tmp_path)

    chunks = await retrieve("Which officer bet the Englishman he would drink rum on the ledge?")

    assert len(chunks) == NAIVE_TOP_K
    assert "Dolokhov" in chunks[0]["original_content"]
    assert [c["chunk_alias"] for c in chunks] == [f"C{i}" for i in range(len(chunks))]
    assert "[CHUNK C0 | tiny.txt]" in chunks[0]["content"] and "[S1]" in chunks[0]["content"]
    assert all(c["retrieval_origin"] == "naive" for c in chunks)
    assert chunks[0]["score"] >= chunks[-1]["score"]
    assert (tmp_path / "index" / "naive-ollama_fake-embedder" / READY_FILE).exists()


@pytest.mark.asyncio
async def test_a_ready_index_is_loaded_and_only_the_question_is_embedded(tmp_path: Path) -> None:
    """The second build finds READY and embeds nothing until a question is asked."""
    await _build(tmp_path)
    _WordHashEmbedding.calls = 0

    retrieve = await _build(tmp_path)
    assert _WordHashEmbedding.calls == 0
    chunks = await retrieve("Who greeted Prince Vasili at the July reception?")
    assert _WordHashEmbedding.calls == 1
    assert "Anna Pavlovna" in chunks[0]["original_content"]


@pytest.mark.asyncio
async def test_a_changed_corpus_or_embedder_rebuilds(tmp_path: Path) -> None:
    """READY names the corpus hash and the embedder; either changing invalidates the index."""
    await _build(tmp_path)
    marker_path = tmp_path / "index" / "naive-ollama_fake-embedder" / READY_FILE
    marker = json.loads(marker_path.read_text())
    assert (
        marker["embedder"] == "ollama/fake-embedder" and marker["corpus_id"] == "war_and_peace_tiny"
    )
    assert {"corpus_sha256", "chunk_size", "chunk_overlap", "top_k"} <= set(marker)

    _WordHashEmbedding.calls = 0
    await _build(tmp_path, corpus=CORPUS + "\n\nCHAPTER V\n\nA new chapter about Kutuzov.")
    assert _WordHashEmbedding.calls > 0
    assert json.loads(marker_path.read_text())["corpus_sha256"] != marker["corpus_sha256"]

    marker_path.write_text(json.dumps({**marker, "embedder": "ollama/other"}), encoding="utf-8")
    _WordHashEmbedding.calls = 0
    await _build(tmp_path, corpus=CORPUS + "\n\nCHAPTER V\n\nA new chapter about Kutuzov.")
    assert _WordHashEmbedding.calls > 0


@pytest.mark.asyncio
async def test_the_dataset_corpus_is_the_default_text(tmp_path: Path) -> None:
    """Without corpus_text the builder reads the dataset bundle named by the spec."""
    spec = _spec(tmp_path)
    retrieve = await build_naive_retriever(spec, EngineSettings(), embed_model=_WordHashEmbedding())
    chunks = await retrieve("Kutuzov")
    assert chunks and all(c["filename"] == "war_and_peace_tiny.txt" for c in chunks)
    assert "[CHUNK C0 | war_and_peace_tiny.txt]" in chunks[0]["content"]


def test_a_non_ollama_embedder_is_refused_without_a_fake() -> None:
    from chaoscypher_cli.benchmark.naive_retrieval import _ollama_embedder

    with pytest.raises(ValueError, match="Ollama"):
        _ollama_embedder("openai/text-embedding-3-small", EngineSettings())


def test_the_naive_rung_embeds_on_the_embedding_host_not_the_chat_one() -> None:
    """``embedding.api_base`` places the embedder for every rung (2026-10-03: naive used the chat host)."""
    from chaoscypher_cli.benchmark.naive_retrieval import _ollama_embedder
    from chaoscypher_core.settings import EmbeddingSettings

    settings = EngineSettings(embedding=EmbeddingSettings(api_base="http://embedder:11434"))
    adapter = _ollama_embedder("ollama/qwen3-embedding:4b", settings)
    assert adapter.base_url == "http://embedder:11434"
    assert _ollama_embedder("ollama/qwen3-embedding:4b", EngineSettings()).base_url == (
        EngineSettings().llm.primary_ollama_url
    )


# Prose with sentences of ordinary length, paragraph breaks and dialogue: what
# the corpus is made of, and what the splitter glued before 2026-10-06. Every
# sentence carries its paragraph's number, so each node's text occurs once.
def _prose_paragraph(i: int) -> str:
    return (
        f"Prince Andrew took his arm in room {i}. Before he could answer, the old prince "
        f"had turned away for the {i}th time. The room was quiet; the clock on the mantel "
        f"struck {i}! Did anyone hear it in hall {i}? Pierre looked at door {i} and smiled "
        f'at nobody in particular. "You are late, number {i}," said Anna Pavlovna, and she '
        f"gave him her hand. Outside, carriage {i} waited in the snow for its master."
    )


PROSE = "\n\n".join(_prose_paragraph(i) for i in range(40))


def test_sentence_pieces_join_back_to_the_text_exactly() -> None:
    """The tokenizer keeps the whitespace after each sentence: no glued boundary (audit C1)."""
    from chaoscypher_core.benchmark.retrieval import sentences_keeping_whitespace

    pieces = sentences_keeping_whitespace(PROSE)
    assert "".join(pieces) == PROSE
    assert len(pieces) > 200
    assert pieces[0] == "Prince Andrew took his arm in room 0. "


@pytest.mark.asyncio
async def test_node_texts_are_the_corpus_text_and_prose_chunks_render_many_sentences(
    tmp_path: Path,
) -> None:
    """Every node's text is the corpus verbatim, the nodes cover it, and [S] labels follow sentences.

    Before the fix nodes read "took his arm.Before he could…" and a ~1,900-char
    chunk rendered about one [S] label (the product's chunks: ~4.4 per 660).
    """
    from llama_index.core import StorageContext, load_index_from_storage

    spec = _spec(tmp_path)
    retrieve = await build_naive_retriever(
        spec,
        EngineSettings(),
        embed_model=_WordHashEmbedding(),
        corpus_text=PROSE,
        corpus_name="prose.txt",
    )
    storage = StorageContext.from_defaults(persist_dir=str(spec.cache_dir))
    index = load_index_from_storage(storage, embed_model=_WordHashEmbedding())
    nodes = list(index.docstore.docs.values())
    assert len(nodes) > 1
    covered = [False] * len(PROSE)
    for node in nodes:
        text = node.get_content()
        assert text in PROSE, "a node's text is not the corpus's own text"
        assert ".Before" not in text and ".B" not in text
        start = PROSE.find(text)
        assert PROSE.find(text, start + 1) == -1
        covered[start : start + len(text)] = [True] * len(text)
    assert all(covered[i] for i, ch in enumerate(PROSE) if not ch.isspace())

    chunks = await retrieve("Who took his arm before the old prince turned away in room 7?")
    prose_chunk = max(chunks, key=lambda c: len(c["original_content"]))
    assert len(prose_chunk["original_content"]) >= 1500
    assert prose_chunk["sentence_count"] >= 5
    assert prose_chunk["content"].count("[S") >= 5


@pytest.mark.asyncio
async def test_an_index_off_the_embedders_native_size_is_refused(tmp_path: Path) -> None:
    """A known embedder must be indexed at its native size, the size the graph rungs use (R1)."""
    spec = NaiveIndexSpec(
        corpus_id="war_and_peace_tiny",
        embedder="ollama/qwen3-embedding:4b",  # 2560 natively; the fake gives DIM
        cache_dir=tmp_path / "index" / "naive-q",
    )
    with pytest.raises(ValueError, match="native"):
        await build_naive_retriever(
            spec,
            EngineSettings(),
            embed_model=_WordHashEmbedding(),
            corpus_text=CORPUS,
            corpus_name="tiny.txt",
        )
    assert not (spec.cache_dir / READY_FILE).exists()


@pytest.mark.asyncio
async def test_a_short_retrieval_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fewer than NAIVE_TOP_K chunks from an index holding more raises (audit R8)."""
    from llama_index.core.retrievers import VectorIndexRetriever

    from chaoscypher_core.benchmark.retrieval import DegradedRetrievalError

    retrieve = await _build(tmp_path)
    full = VectorIndexRetriever.aretrieve

    async def one_hit(self: Any, query: Any) -> list[Any]:
        return (await full(self, query))[:1]

    monkeypatch.setattr(VectorIndexRetriever, "aretrieve", one_hit)
    with pytest.raises(DegradedRetrievalError, match="fewer than its budget"):
        await retrieve("Kutuzov")


@pytest.mark.asyncio
async def test_a_changed_splitter_changes_the_marker_and_the_fingerprint_and_rebuilds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """READY and the naive pins record the splitter by what it does (audit wave 2).

    Until 2026-10-06 READY carried a hand-written ``sentence_tokenizer`` label
    and the naive fingerprint the three constants: a splitter change reused
    the old index and found the old pins.
    """
    from chaoscypher_core.benchmark import retrieval
    from chaoscypher_core.benchmark.pins import retrieval_fingerprint

    await _build(tmp_path)
    marker_path = tmp_path / "index" / "naive-ollama_fake-embedder" / READY_FILE
    marker = json.loads(marker_path.read_text())
    assert marker["splitter_recipe"] == retrieval.naive_recipe()
    assert marker["llama_index"] == retrieval.llama_index_version() is not None
    fingerprint = retrieval_fingerprint("naive", "ollama/qwen3-embedding:4b", EngineSettings())

    original = retrieval.sentences_keeping_whitespace
    monkeypatch.setattr(  # one sentence per piece, its whitespace dropped: another splitter
        retrieval, "sentences_keeping_whitespace", lambda text: [p.strip() for p in original(text)]
    )
    assert retrieval.naive_recipe() != marker["splitter_recipe"]
    assert (
        retrieval_fingerprint("naive", "ollama/qwen3-embedding:4b", EngineSettings()) != fingerprint
    )
    _WordHashEmbedding.calls = 0
    await _build(tmp_path)
    assert _WordHashEmbedding.calls > 0  # rebuilt, not loaded
    assert json.loads(marker_path.read_text())["splitter_recipe"] == retrieval.naive_recipe()


def test_a_naive_pin_built_under_another_splitter_is_refused(tmp_path: Path) -> None:
    """The naive file records the splitter recipe; check() compares it like the retrieval recipe."""
    from chaoscypher_core.benchmark.pins import PinError, new_pins

    pins = new_pins(
        tmp_path,
        retrieval="naive",
        embedder="ollama/qwen3-embedding:4b",
        settings=EngineSettings(),
        graph_sha256="g" * 64,
        index_digest=None,
    )
    pins.check(graph_sha256="g" * 64, embedder="ollama/qwen3-embedding:4b", retrieval="naive")
    pins.header["settings"]["naive"]["splitter_recipe"] = "older-splitter"
    with pytest.raises(PinError, match="splitter_recipe: pinned 'older-splitter'"):
        pins.check(graph_sha256="g" * 64, embedder="ollama/qwen3-embedding:4b", retrieval="naive")


@pytest.mark.asyncio
async def test_a_decomposed_question_retrieves_what_the_composed_one_does(tmp_path: Path) -> None:
    """NFC at the naive rung's entry (audit W5-13): accents typed as combining marks are not another question."""
    import unicodedata

    retrieve = await _build(tmp_path)
    composed = "Who greeted Prince Vasíli at the July reception?"
    decomposed = unicodedata.normalize("NFD", composed)
    assert decomposed != composed  # the word hash would differ without the fold

    nfc = await retrieve(composed)
    nfd = await retrieve(decomposed)
    assert [c["chunk_id"] for c in nfd] == [c["chunk_id"] for c in nfc]
