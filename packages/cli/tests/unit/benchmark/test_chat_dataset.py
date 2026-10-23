# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_cli.benchmark.chat_dataset import (
    GraphRAGChatDataset,
    JudgeVerdict,
)
from chaoscypher_cli.benchmark.models import ModelConfig
from chaoscypher_cli.benchmark.queries import LabeledQuery, LabeledQuerySet


def _qs():
    return LabeledQuerySet(
        version="1.0",
        queries=[
            LabeledQuery(
                id="q1",
                band="factual_single_hop",
                question="?",
                gold_entities=["A"],
                gold_answer="a",
            ),
            LabeledQuery(id="q2", band="out_of_scope", question="?", expect_refusal=True),
        ],
    )


@pytest.mark.asyncio
async def test_run_invokes_pipeline_and_judge():
    chat = ModelConfig(provider="ollama", model="llama", label="L")
    judge = ModelConfig(provider="anthropic", model="claude-opus-4-7", label="J")

    indexed = MagicMock()

    @asynccontextmanager
    async def fake_indexed_graph(*, embedder=None):
        yield indexed

    provider = MagicMock()
    provider.indexed_graph = fake_indexed_graph

    graphrag_search = AsyncMock(return_value={"entities": [{"id": "uuid-1", "name": "A"}]})
    chat_call = AsyncMock(return_value="A is the answer.")
    judge_call = AsyncMock(side_effect=["5", "5"])

    ds = GraphRAGChatDataset(
        id="demo",
        version="1.0",
        corpus_id="demo",
        queries=_qs(),
        graph_provider=provider,
        graphrag_search=graphrag_search,
        chat=chat_call,
        judge=judge,
        judge_call=judge_call,
    )
    out = await ds.run(chat)
    assert out.error is None
    per_query = out.extras["per_query"]
    assert len(per_query) == 2
    assert per_query[0]["query_id"] == "q1"
    assert per_query[0]["judge_scores"]["faithfulness"] == 5
    assert per_query[0]["judge_scores"]["correctness"] == 5
    assert per_query[1]["query_id"] == "q2"
    assert out.extras["judge_provider"] == "anthropic"
    assert out.extras["judge_model"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_run_records_chat_failure_per_query():
    chat = ModelConfig(provider="ollama", model="llama", label="L")
    judge = ModelConfig(provider="anthropic", model="claude-opus-4-7", label="J")

    indexed = MagicMock()

    @asynccontextmanager
    async def fake_indexed_graph(*, embedder=None):
        yield indexed

    provider = MagicMock()
    provider.indexed_graph = fake_indexed_graph

    graphrag_search = AsyncMock(return_value={"entities": []})
    chat_call = AsyncMock(side_effect=RuntimeError("boom"))
    judge_call = AsyncMock()

    ds = GraphRAGChatDataset(
        id="demo",
        version="1.0",
        corpus_id="demo",
        queries=_qs(),
        graph_provider=provider,
        graphrag_search=graphrag_search,
        chat=chat_call,
        judge=judge,
        judge_call=judge_call,
    )
    out = await ds.run(chat)
    assert out.error is None
    pqs = out.extras["per_query"]
    assert all("error" in pq for pq in pqs)
    judge_call.assert_not_called()


def test_judge_verdict_dataclass():
    v = JudgeVerdict(faithfulness=5, correctness=4, refusal_correct=None)
    assert v.faithfulness == 5
    assert v.refusal_correct is None


# ---------------------------------------------------------------------------
# Retrieval rungs on the local chat stage
# ---------------------------------------------------------------------------


def _rung_dataset(retrieval: str, **over):
    """A probe-scored chat dataset (no judge) under ``retrieval`` with fakes for every arm."""
    from pathlib import Path

    indexed = MagicMock()
    indexed.ctx.settings = "settings"

    @asynccontextmanager
    async def fake_indexed_graph(*, embedder=None):
        yield indexed

    provider = MagicMock()
    provider.indexed_graph = fake_indexed_graph
    fields = {
        "id": "demo",
        "version": "1.0",
        "corpus_id": "demo",
        "queries": _qs(),
        "graph_provider": provider,
        "graphrag_search": AsyncMock(return_value={"chunks": [{"content": "graph"}]}),
        "chat": AsyncMock(return_value="A is the answer."),
        "judge": None,
        "judge_call": AsyncMock(),
        "embedder": ModelConfig(provider="ollama", model="qwen3-embedding:0.6b", label="E"),
        "retrieval": retrieval,
        "hybrid_search": AsyncMock(return_value={"chunks": [{"content": "hybrid"}]}),
        "naive_index_root": Path("/tmp/naive"),
    }
    fields.update(over)
    return GraphRAGChatDataset(**fields)


@pytest.mark.asyncio
async def test_hybrid_rung_calls_the_hybrid_arm_and_never_graphrag():
    ds = _rung_dataset("hybrid")
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    assert out.error is None and out.extras["retrieval"] == "hybrid"
    assert ds.hybrid_search.await_count == 2
    ds.graphrag_search.assert_not_awaited()
    assert ds.chat.await_args.args[2] == {"chunks": [{"content": "hybrid"}]}


@pytest.mark.asyncio
async def test_naive_rung_builds_the_index_once_from_the_corpus_and_embedder():
    """The factory is called once per run with the corpus id, the embedder and the cache dir."""
    specs = []
    asked = []

    async def factory(spec, settings):
        specs.append((spec, settings))

        async def retrieve(question):
            asked.append(question)
            return [{"content": "naive"}]

        return retrieve

    ds = _rung_dataset("naive", naive_retriever=factory)
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    assert out.error is None and out.extras["retrieval"] == "naive"
    ((spec, settings),) = specs
    assert settings == "settings"
    assert (spec.corpus_id, spec.embedder) == ("demo", "ollama/qwen3-embedding:0.6b")
    assert spec.cache_dir.name == "demo-naive-ollama_qwen3-embedding:0.6b"
    assert asked == [q.question for q in _qs().queries]
    ds.graphrag_search.assert_not_awaited()
    ds.hybrid_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_graphrag_rung_is_the_default_and_records_itself():
    ds = _rung_dataset("graphrag")
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    assert out.extras["retrieval"] == "graphrag"
    assert ds.graphrag_search.await_count == 2
    ds.hybrid_search.assert_not_awaited()


def test_a_rung_without_its_wiring_is_refused_at_construction():
    with pytest.raises(ValueError, match="hybrid_search"):
        _rung_dataset("hybrid", hybrid_search=None)
    with pytest.raises(ValueError, match="naive_retriever"):
        _rung_dataset("naive")
    with pytest.raises(ValueError, match="graphrag, hybrid, naive"):
        _rung_dataset("plain")


# ---------------------------------------------------------------------------
# Pinned retrieval on the local chat stage
# ---------------------------------------------------------------------------


def _pinned_dataset(tmp_path, *, pin_ids=("q1", "q2")):
    """A dataset over a fake graph whose pins dir holds graphrag pins for ``pin_ids``."""
    from chaoscypher_core.benchmark.pins import file_sha256, new_pins
    from chaoscypher_core.settings import EngineSettings

    snapshot = tmp_path / "app.db"
    snapshot.write_bytes(b"graph")
    settings = EngineSettings()
    pins = new_pins(
        tmp_path / "pins",
        retrieval="graphrag",
        embedder="ollama/qwen3-embedding:0.6b",
        settings=settings,
        graph_sha256=file_sha256(snapshot),
        index_digest="i" * 16,
    )
    questions = {q.id: q.question for q in _qs().queries}
    for qid in pin_ids:
        pins.put(qid, questions[qid], {"chunks": [{"content": f"pinned {qid}"}]})
    pins.save()

    reindexed_with = []
    indexed = MagicMock()
    indexed.ctx.settings = settings

    @asynccontextmanager
    async def fake_indexed_graph(*, embedder=None):
        reindexed_with.append(embedder)
        yield indexed

    provider = MagicMock()
    provider.indexed_graph = fake_indexed_graph
    provider.snapshot_path = snapshot
    ds = _rung_dataset("graphrag", graph_provider=provider, pins_dir=tmp_path / "pins")
    return ds, pins, reindexed_with


@pytest.mark.asyncio
async def test_a_pinned_run_serves_the_pins_and_never_reindexes_or_retrieves(tmp_path):
    ds, pins, reindexed_with = _pinned_dataset(tmp_path)
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    assert out.error is None
    assert reindexed_with == [None]
    ds.graphrag_search.assert_not_awaited()
    served = [call.args[2] for call in ds.chat.await_args_list]
    assert served == [
        {"chunks": [{"content": "pinned q1"}]},
        {"chunks": [{"content": "pinned q2"}]},
    ]
    assert out.extras["retrieval_pins"]["file"] == pins.path.name


@pytest.mark.asyncio
async def test_a_pinned_run_without_a_matching_file_fails_before_any_chat(tmp_path):
    ds, pins, _ = _pinned_dataset(tmp_path)
    pins.path.unlink()
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    assert out.error is not None and "no graphrag pins" in out.error
    ds.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_pinned_run_missing_a_question_fails_before_any_chat(tmp_path):
    ds, _, _ = _pinned_dataset(tmp_path, pin_ids=("q1",))
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    assert out.error is not None and "has no pin for q2" in out.error
    ds.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_pinned_run_on_another_graph_fails_before_any_chat(tmp_path):
    ds, _, _ = _pinned_dataset(tmp_path)
    ds.graph_provider.snapshot_path.write_bytes(b"another graph")
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    assert out.error is not None and "graph_sha256" in out.error
    ds.chat.assert_not_awaited()


# ---------------------------------------------------------------------------
# A retryable model failure asks the question again (2026-10-04, a host waking from suspend)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_retryable_failure_asks_the_question_again_and_counts_the_retries():
    from chaoscypher_core.exceptions import LLMServiceError

    lost = LLMServiceError(
        provider="ollama", reason="Connection to Ollama lost [ReadError]", model="m"
    )
    chat = AsyncMock(side_effect=[lost, lost, "A is the answer.", "B."])
    ds = _rung_dataset("graphrag", chat=chat, question_retries=3, retry_backoff_seconds=0)
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    first, second = out.extras["per_query"]
    assert first["answer"] == "A is the answer." and first["retries"] == 2 and "error" not in first
    assert second["answer"] == "B." and "retries" not in second


@pytest.mark.asyncio
async def test_a_failure_that_is_not_retryable_is_recorded_at_once():
    from chaoscypher_core.exceptions import LLMError

    chat = AsyncMock(side_effect=[LLMError("model 'x' not found"), "B."])
    ds = _rung_dataset("graphrag", chat=chat, question_retries=3, retry_backoff_seconds=0)
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    first, second = out.extras["per_query"]
    assert "not found" in first["error"] and "retries" not in first
    assert chat.await_count == 2


@pytest.mark.asyncio
async def test_retries_run_out_and_the_question_is_recorded_as_an_error():
    from chaoscypher_core.exceptions import LLMServiceError

    refused = LLMServiceError(provider="ollama", reason="Cannot connect to Ollama", model="m")
    chat = AsyncMock(side_effect=[refused, refused, refused, "B."])
    ds = _rung_dataset("graphrag", chat=chat, question_retries=2, retry_backoff_seconds=0)
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    first, second = out.extras["per_query"]
    assert "Cannot connect" in first["error"] and first["retries"] == 2
    assert second["answer"] == "B."


@pytest.mark.asyncio
async def test_an_unclassified_dropped_connection_is_retried_and_marked():
    """Audit wave 3: a raw stream/transport failure no provider classified is transient too."""
    import httpx

    chat = AsyncMock(
        side_effect=[
            ConnectionResetError("peer reset"),
            "A is the answer.",
            httpx.ReadError("x"),
            "B.",
        ]
    )
    ds = _rung_dataset("graphrag", chat=chat, question_retries=1, retry_backoff_seconds=0)
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    first, second = out.extras["per_query"]
    assert first["answer"] == "A is the answer." and first["retried"] is True
    assert second["answer"] == "B." and second["retried"] is True and second["retries"] == 1
    assert out.extras["per_query_errors"] == 0


@pytest.mark.asyncio
async def test_a_transient_retrieval_failure_is_retried_like_the_chat_call():
    """The live retrieval (an embedding call to the same host) gets the same retry."""
    search = AsyncMock(
        side_effect=[
            TimeoutError("embed timed out"),
            {"chunks": [{"content": "g"}]},
            {"chunks": []},
        ]
    )
    ds = _rung_dataset(
        "graphrag", graphrag_search=search, question_retries=1, retry_backoff_seconds=0
    )
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    first, second = out.extras["per_query"]
    assert "error" not in first and first["retried"] is True
    assert "retried" not in second


@pytest.mark.asyncio
async def test_a_fatal_model_error_is_never_retried_even_when_it_says_retryable():
    """The extraction stage's rule: a missing model or capability is not transient."""
    from chaoscypher_core.exceptions import LLMModelError

    fatal = LLMModelError(provider="ollama", model="m", reason="Model not found")
    fatal.is_retryable = True
    chat = AsyncMock(side_effect=[fatal, "B."])
    ds = _rung_dataset("graphrag", chat=chat, question_retries=3, retry_backoff_seconds=0)
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    first, _ = out.extras["per_query"]
    assert "Model not found" in first["error"] and "retried" not in first


def test_the_transient_rule_is_the_extraction_stages_plus_raw_transport_errors():
    from chaoscypher_cli.benchmark.chat_dataset import transient_failure
    from chaoscypher_core.exceptions import LLMServiceError

    assert transient_failure(LLMServiceError(provider="ollama", reason="the chat stream failed"))
    assert transient_failure(ConnectionRefusedError()) and transient_failure(TimeoutError())
    assert not transient_failure(ValueError("bad prompt")) and not transient_failure(KeyError("q"))


async def test_looped_answers_are_counted_whether_trimmed_or_retried():
    """The guard check of 2026-10-05: eight retried answers showed loops=0 because only the trimmed shape counted."""
    chat = ModelConfig(provider="ollama", model="llama", label="L")
    indexed = MagicMock()

    @asynccontextmanager
    async def fake_indexed_graph(*, embedder=None):
        yield indexed

    provider = MagicMock()
    provider.indexed_graph = fake_indexed_graph
    graphrag_search = AsyncMock(return_value={"entities": []})
    chat_call = AsyncMock(
        side_effect=[
            {"answer": "A.", "finish_reason": "stop", "loop_retried": True, "output_tokens": 1},
            {"answer": "B.", "finish_reason": "loop", "output_tokens": 1},
        ]
    )
    ds = GraphRAGChatDataset(
        id="demo",
        version="1.0",
        corpus_id="demo",
        queries=_qs(),
        graph_provider=provider,
        graphrag_search=graphrag_search,
        chat=chat_call,
        judge=None,
        judge_call=None,
    )
    out = await ds.run(chat)
    first, second = out.extras["per_query"]
    assert first["loop_retried"] is True and "loop_retried" not in second
    assert out.chunks_aborted_by_loop == 2 and out.chunks_truncated == 0


async def test_the_rows_model_profile_is_resolved_before_retrieval_and_pins():
    """A rule for the chat model sets its levers on the run's settings (graphrag_profiles, 2026-10-05)."""
    from chaoscypher_core.settings import EngineSettings, GraphRAGProfileRule

    chat = ModelConfig(provider="ollama", model="qwen3.5:9b", label="S")
    settings = EngineSettings()
    settings.graphrag.profiles = {"rich": {"entity_aliases": True, "chunk_limit": 12}}
    settings.graphrag.profile_rules = [GraphRAGProfileRule(profile="rich", max_params_b=27)]
    ctx = MagicMock()
    ctx.settings = settings
    indexed = MagicMock()
    indexed.ctx = ctx

    @asynccontextmanager
    async def fake_indexed_graph(*, embedder=None):
        yield indexed

    provider = MagicMock()
    provider.indexed_graph = fake_indexed_graph
    seen: list[tuple[bool, int]] = []

    async def graphrag_search(query, ctx_):
        seen.append((ctx_.settings.graphrag.entity_aliases, ctx_.settings.graphrag.chunk_limit))
        return {"entities": []}

    chat_call = AsyncMock(return_value="A.")
    ds = GraphRAGChatDataset(
        id="demo",
        version="1.0",
        corpus_id="demo",
        queries=_qs(),
        graph_provider=provider,
        graphrag_search=graphrag_search,
        chat=chat_call,
        judge=None,
        judge_call=None,
        domain="literary",
    )
    out = await ds.run(chat)
    assert out.error is None
    assert seen and all(s == (True, 12) for s in seen)
    assert ctx.settings.graphrag.profiles == {}  # settled: the resolved levers, no rules left


# ---------------------------------------------------------------------------
# What a row records about its prompt, its tokens and its graph (audit H5, H9, H10, R10)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_row_records_the_levers_policy_sweep_prompt_and_pins_it_ran_with(tmp_path):
    """Policy, variant and default rows shared one fingerprint and differed by app version alone (H5)."""
    from chaoscypher_core.benchmark.chat_run import prompt_template_sha256, resolved_levers
    from chaoscypher_core.settings import EngineSettings

    ds, pins, _ = _pinned_dataset(tmp_path)
    ds.queries_file = "/data/holdout.yaml"
    ds.graph_provenance = {
        "graph_cache_key": "k" * 16,
        "resolution": "res",
        "extraction_recipe": "ext",
        "extraction_domain": "literary",
        "corpus_sha256": "s" * 64,
    }
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    assert out.error is None
    run = out.extras["run_settings"]
    default = EngineSettings().graphrag
    assert run["levers"] == resolved_levers(default)
    assert "profiles" not in run["levers"] and "profile_rules" not in run["levers"]
    assert run["answer_policy"] is default.answer_policy
    assert run["prompt_sha256"] == prompt_template_sha256(default)
    assert run["pins_file"] == pins.path.name
    assert run["pins_fingerprint"] == run["retrieval_fingerprint"] == pins.header["fingerprint"]
    assert (run["resolution"], run["extraction_recipe"], run["graph_cache_key"]) == (
        "res",
        "ext",
        "k" * 16,
    )
    assert run["queries_file"] == out.extras["queries_file"] == "/data/holdout.yaml"
    assert (run["extraction_domain"], run["corpus_sha256"]) == ("literary", "s" * 64)
    assert run["sweep_set"] is None and run["profile"] is None


def test_the_row_records_the_candidates_window_output_cap_and_loop_guard(tmp_path):
    """Audit wave 2: num_ctx is the candidate's provider's, not the shared settings' chat provider's."""
    from types import SimpleNamespace

    from chaoscypher_core.settings import EngineSettings

    ds, _, _ = _pinned_dataset(tmp_path)
    settings = EngineSettings()
    settings.llm.chat_provider = (
        "anthropic"  # the run's shared chat provider is not the candidate's
    )
    settings.chat.loop_max_consecutive_lines = 6
    ctx = SimpleNamespace(settings=settings)
    ollama = ModelConfig(provider="ollama", model="m", label="M")
    run = ds._run_settings(ctx, None, None, ollama)
    assert run["num_ctx"] == settings.llm.ollama_num_ctx  # an Ollama candidate has a window
    assert run["ai_max_tokens"] == settings.llm.ai_max_tokens
    assert run["loop_guard"] == {"max_consecutive": 6, "max_occurrences": 25}
    settings.llm.chat_provider = "ollama"
    cloud = ModelConfig(provider="anthropic", model="m", label="M")
    assert ds._run_settings(ctx, None, None, cloud)["num_ctx"] is None  # a cloud candidate: none


@pytest.mark.asyncio
async def test_a_sweep_variant_beats_the_profile_and_the_row_says_so(tmp_path):
    """A profile silently overwrote SWEEP_SET levers on the models it matched until 2026-10-06."""
    from chaoscypher_core.settings import EngineSettings, GraphRAGProfileRule

    settings = EngineSettings()
    settings.graphrag.profiles = {"rich": {"max_triples": 12, "entity_aliases": True}}
    settings.graphrag.profile_rules = [GraphRAGProfileRule(profile="rich", max_params_b=27)]
    seen = []

    async def graphrag_search(query, ctx_):
        seen.append((ctx_.settings.graphrag.max_triples, ctx_.settings.graphrag.entity_aliases))
        return {"chunks": []}

    ds = _rung_dataset(
        "graphrag", graphrag_search=graphrag_search, sweep_set="graphrag.max_triples=33"
    )
    ds.graph_provider.indexed_graph = _graph_with(settings)
    out = await ds.run(ModelConfig(provider="ollama", model="qwen3.5:9b", label="S"))
    assert out.error is None, out.error
    assert seen and all(s == (33, True) for s in seen)
    run = out.extras["run_settings"]
    assert run["sweep_set"] == "graphrag.max_triples=33" and run["profile"] == "rich"
    assert run["levers"]["max_triples"] == 33 and run["levers"]["entity_aliases"] is True


def _graph_with(settings):
    indexed = MagicMock()
    indexed.ctx.settings = settings

    @asynccontextmanager
    async def fake_indexed_graph(*, embedder=None):
        yield indexed

    return fake_indexed_graph


@pytest.mark.asyncio
async def test_a_question_records_its_prompt_tokens_entity_ids_and_loop(tmp_path):
    """input_tokens was always 0 and retrieved_entity_ids always empty (H9, R10)."""
    reply = {
        "answer": "A.",
        "finish_reason": "stop",
        "output_tokens": 7,
        "prompt_tokens": 1500,
        "num_ctx": 32768,
        "num_predict": 30720,
        "loop_retried": True,
        "thinking_retried": True,
        "loop_phase": "content",
        "loop_reason": "line repeated 8 times in a row",
        "thinking_chars": 420,
        "context_text": "ctx",
    }
    retrieved = {
        "graph_context": {"seed_entities": [{"id": "n1"}], "related_entities": [{"id": "n2"}]}
    }
    ds = _rung_dataset(
        "graphrag",
        graphrag_search=AsyncMock(return_value=retrieved),
        chat=AsyncMock(return_value=reply),
    )
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    first = out.extras["per_query"][0]
    assert first["retrieved_entity_ids"] == ["n1", "n2"]
    assert (first["prompt_tokens"], first["num_ctx"], first["num_predict"]) == (1500, 32768, 30720)
    assert first["thinking_retried"] is True and first["loop_reason"].startswith("line repeated")
    assert first["thinking_chars"] == 420 and first["loop_phase"] == "content"
    assert out.input_tokens == 3000 and out.output_tokens == 14
    assert out.extras["thinking_retried"] == 2 and out.extras["per_query_errors"] == 0


@pytest.mark.asyncio
async def test_a_question_records_the_shape_its_retrieval_ran_with() -> None:
    """``shape``, ``shape_reason``, ``shape_source`` on every entry; None on the hybrid rung (Task 10)."""
    stats = {
        "shape": "aggregate",
        "shape_reason": "a named entity and every",
        "shape_source": "rules",
    }
    ds = _rung_dataset(
        "graphrag",
        graphrag_search=AsyncMock(return_value={"chunks": [], "retrieval_stats": stats}),
    )
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    for entry in out.extras["per_query"]:
        assert {k: entry[k] for k in stats} == stats
    hybrid = _rung_dataset("hybrid")
    out = await hybrid.run(ModelConfig(provider="anthropic", model="m", label="M"))
    for entry in out.extras["per_query"]:
        assert (entry["shape"], entry["shape_reason"], entry["shape_source"]) == (None, None, None)


@pytest.mark.asyncio
async def test_the_row_records_the_sha256_of_the_question_file_it_was_asked(tmp_path):
    """Audit wave 3: the file name alone hid a fixture edit between two runs."""
    import hashlib

    questions = tmp_path / "queries.yaml"
    questions.write_text("version: '1'\nqueries: []\n", encoding="utf-8")
    ds = _rung_dataset("graphrag", queries_file=str(questions))
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    assert out.extras["queries_file"] == str(questions)
    assert out.extras["queries_sha256"] == hashlib.sha256(questions.read_bytes()).hexdigest()
    questions.write_text("edited after the run started", encoding="utf-8")
    again = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    assert again.extras["queries_sha256"] == out.extras["queries_sha256"]  # read once, at start
    assert (
        await _rung_dataset("graphrag").run(ModelConfig(provider="anthropic", model="m", label="M"))
    ).extras["queries_sha256"] is None


# ---------------------------------------------------------------------------
# Audit wave 4 (2026-10-06)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_row_records_the_bytes_the_loader_parsed_not_a_second_read(tmp_path):
    """The hash is taken at parse time: an edit after loading does not change what the row says."""
    import hashlib

    from chaoscypher_core.benchmark.queries import load_queries

    questions = tmp_path / "queries.yaml"
    asked = (
        "version: '1'\nqueries:\n"
        "  - {id: q1, band: out_of_scope, question: 'Who?', expect_refusal: true}\n"
    )
    questions.write_text(asked, encoding="utf-8")
    loaded = load_queries(questions)
    questions.write_text(asked + "# edited mid-sweep\n", encoding="utf-8")
    ds = _rung_dataset("graphrag", queries=loaded, queries_file=str(questions))
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    assert out.extras["queries_sha256"] == hashlib.sha256(asked.encode()).hexdigest()
    assert loaded.sha256 == out.extras["queries_sha256"]


@pytest.mark.asyncio
async def test_a_retry_backoff_is_recorded_apart_and_left_out_of_the_latency():
    """``retry_wait_ms`` holds the wait; the latency is the chat call's own time."""
    from chaoscypher_core.exceptions import LLMServiceError

    lost = LLMServiceError(provider="ollama", reason="Connection to Ollama lost", model="m")
    chat = AsyncMock(side_effect=[lost, "A is the answer.", "B."])
    ds = _rung_dataset("graphrag", chat=chat, question_retries=1, retry_backoff_seconds=0.2)
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    first, second = out.extras["per_query"]
    assert first["retries"] == 1 and first["retry_wait_ms"] >= 190
    assert "retry_wait_ms" not in second
    # the two instant chat calls take far less than the 200 ms the first one waited
    assert max(out.per_chunk_latency_ms) < 150
    # audit wave 5: the row's own latency leaves the wait out too, and says so
    from chaoscypher_core.benchmark.results import LATENCY_EXCLUDES_RETRY_WAIT

    assert out.extras[LATENCY_EXCLUDES_RETRY_WAIT] is True
    assert out.latency_ms < 150


def test_one_transient_retry_is_the_default():
    """The wave-3 decision: one retry, then the question is recorded as an error."""
    from chaoscypher_core.app_config import BenchmarkSettings

    assert BenchmarkSettings().chat_question_retries == 1


@pytest.mark.asyncio
async def test_a_judged_row_records_the_judge_prompts_fingerprint():
    """An edited judge prompt moves every judged score under one judge model."""
    import hashlib

    from chaoscypher_cli.benchmark.chat_dataset import judge_prompts_sha256
    from chaoscypher_cli.benchmark.judge_prompts import (
        CORRECTNESS_PROMPT,
        FAITHFULNESS_PROMPT,
        REFUSAL_PROMPT,
    )

    judge = ModelConfig(provider="anthropic", model="claude-opus-4-7", label="J")
    ds = _rung_dataset("graphrag", judge=judge, judge_call=AsyncMock(return_value="5"))
    out = await ds.run(ModelConfig(provider="anthropic", model="m", label="M"))
    expected = hashlib.sha256(
        b"".join(
            t.encode() + b"\0" for t in (CORRECTNESS_PROMPT, FAITHFULNESS_PROMPT, REFUSAL_PROMPT)
        )
    ).hexdigest()
    assert out.extras["judge_prompts_sha256"] == judge_prompts_sha256() == expected
    probed = await _rung_dataset("graphrag").run(
        ModelConfig(provider="anthropic", model="m", label="M")
    )
    assert probed.extras["judge_prompts_sha256"] is None
