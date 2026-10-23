# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The grounded-chat suite: an MCP client answers a reference pack's questions.

A fake reference pack lives in ``tmp_path`` and a fake retriever stands in
for GraphRAG search, so no SQLite database or embedder is touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from chaoscypher_core.benchmark.chat_prompt import format_retrieved_context, grounded_chat_prompt
from chaoscypher_core.benchmark.reference import reference_root, write_manifest
from chaoscypher_core.benchmark.results import load_results
from chaoscypher_core.benchmark.scorers.chat_probes import CHAT_PROBE_SCORER_VERSION
from chaoscypher_core.mcp.benchmark import BenchmarkBridge
from chaoscypher_core.settings import EngineSettings, PathSettings


if TYPE_CHECKING:
    from chaoscypher_core.benchmark.retrieval import NaiveIndexSpec


_QUERIES = """
version: "1.0"
queries:
  - id: q001
    band: factual_single_hop
    question: "Who commanded the Russian army in 1805?"
    gold_entities: ["Kutuzov"]
    answer_terms: [["Kutuzov", "the commander-in-chief"]]
    gold_answer: |
      Kutuzov.
  - id: q002
    band: multi_hop
    question: "Who led the Second Army, and under whom?"
    gold_entities: ["Bagration", "Kutuzov"]
    answer_terms: ["Bagration", "Kutuzov"]
    gold_answer: |
      Bagration, under Kutuzov.
  - id: q003
    band: out_of_scope
    question: "Who won the battle of Borodino?"
    expect_refusal: true
    decoy_entities: ["Napoleon"]
"""

_RETRIEVED = {
    "graph_context": {
        "seed_entities": [
            {"id": "n1", "label": "Kutuzov", "description": "commanded the Russian army in 1805"},
            {"id": "n2", "label": "Bagration", "description": "led the Second Army under Kutuzov"},
        ],
        "relationships": [{"source": "Bagration", "label": "serves under", "target": "Kutuzov"}],
    },
    "chunks": [{"text": "Kutuzov commanded the Russian army. Bagration led the Second Army."}],
    # a complete graphrag retrieval: every stage ran, none swallowed an exception; the shape
    # the router chose, which every record carries
    "retrieval_stats": {
        "mode": "full_graphrag",
        "faults": [],
        "shape": "lookup",
        "shape_reason": "no cue",
        "shape_source": "rules",
    },
}

_MANIFEST = {
    "name": "tiny",
    "fixture_id": "war_and_peace_tiny",
    "fixture_version": "1.0",
    "corpus_id": "war_and_peace_tiny",
    "extractor": "ollama/gemma4:31b",
    "extractor_label": "Gemma 4 31B",
    "embedder": "ollama/qwen3-embedding:0.6b",
    "dedup_embedder": "local/Qwen/Qwen3-Embedding-0.6B",
    "graph_cache_key": "0123456789abcdef",
    "created": "2026-09-26T00:00:00+00:00",
    "snapshot_file": "app.db",
    "queries_file": "queries.yaml",
}


def _write_pack(data_dir: Path, name: str = "tiny") -> Path:
    """A reference pack with a placeholder snapshot and the three-question fixture."""
    root = reference_root(data_dir) / name
    root.mkdir(parents=True)
    (root / "app.db").write_bytes(b"")
    (root / "queries.yaml").write_text(_QUERIES, encoding="utf-8")
    write_manifest(root, {**_MANIFEST, "name": name})
    return root


class _FakeRetriever:
    """Counts questions and returns the canned retrieval."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.questions: list[str] = []

    async def __call__(self, question: str) -> dict[str, Any]:
        """Record the question; return the canned context."""
        self.questions.append(question)
        return _RETRIEVED


def _verdict(b: BenchmarkBridge, run_id: str, task_id: str) -> dict[str, Any]:
    """A completed question's verdict, from the state file (never from a tool response)."""
    state = json.loads((b.state_dir / f"{run_id}.json").read_text())
    verdict: dict[str, Any] = state["verdicts"][task_id]
    return verdict


def _bridge(data_dir: Path, retriever: Any, reference: str = "tiny") -> BenchmarkBridge:
    """A bridge whose chat suite retrieves with ``retriever``."""
    bridge = BenchmarkBridge(EngineSettings(paths=PathSettings(data_dir=str(data_dir))))
    bridge.suite("chat", reference).retrieve = retriever  # type: ignore[attr-defined]
    return bridge


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A data dir holding one reference pack."""
    data = tmp_path / "data"
    _write_pack(data)
    return data


@pytest.mark.asyncio
async def test_a_chat_task_carries_the_local_runs_grounded_prompt(data_dir: Path) -> None:
    """One user message: the local chat stage's prompt over the retrieved context."""
    fake = _FakeRetriever()
    bridge = _bridge(data_dir, fake)
    started = await bridge.start("chat", "claude-code", "claude-sonnet-5")
    assert started["success"], started
    assert started["reference"] == "tiny" and started["total"] == 3
    task = started["next"]
    assert task["task_id"] == task["probe_id"] == "q001"
    assert task["kind"] == "chat" and task["stage"] == "answer"
    assert task["band"] == "factual_single_hop" and task["tier"] == "easy"
    assert task["system_prompt"] == ""
    expected = grounded_chat_prompt(
        "Who commanded the Russian army in 1805?",
        format_retrieved_context(_RETRIEVED),
        ask_for_citations=True,  # the default since 2026-10-02: the app's rule, asked here too
        answer_policy=True,  # the default since 2026-10-05: the app's grounded answer policy
    )
    assert task["user_prompt"] == expected
    assert "not in the sources" in task["answer_format"]
    assert fake.questions == ["Who commanded the Russian army in 1805?"]


@pytest.mark.asyncio
async def test_retrieval_runs_once_per_question_and_a_resumed_run_reuses_it(
    data_dir: Path,
) -> None:
    """The context is cached in the run state, so neither a repeat nor a new bridge retrieves."""
    fake = _FakeRetriever()
    bridge = _bridge(data_dir, fake)
    run_id = (await bridge.start("chat", "claude-code", "m"))["run_id"]
    first = await bridge.next_task(run_id)
    assert len(fake.questions) == 1

    async def _must_not_retrieve(question: str) -> dict[str, Any]:
        """Fail the test if the resumed run retrieves again."""
        raise AssertionError(question)

    resumed = _bridge(data_dir, _must_not_retrieve)
    again = await resumed.next_task(run_id)
    assert again["success"] and again["user_prompt"] == first["user_prompt"]
    state = json.loads((resumed.state_dir / f"{run_id}.json").read_text())
    assert state["stages"]["q001"]["retrieved_entity_ids"] == ["n1", "n2"]
    assert state["stages"]["q001"]["context_text"] == format_retrieved_context(_RETRIEVED)


@pytest.mark.asyncio
async def test_a_right_answer_passes_and_a_hallucinated_name_fails(data_dir: Path) -> None:
    """The verdict is the chat board's scorer on a one-question fixture, kept off the response."""
    bridge = _bridge(data_dir, _FakeRetriever())
    run_id = (await bridge.start("chat", "claude-code", "m"))["run_id"]

    right = await bridge.submit(run_id, "q001", "answer", "Kutuzov commanded the Russian army.")
    assert right["accepted"] and "verdict" not in right
    assert _verdict(bridge, run_id, "q001")["passed"] is True
    assert right["submitted"] == 1 and right["next"]["task_id"] == "q002"

    await bridge.next_task(run_id)
    wrong = await bridge.submit(run_id, "q002", "answer", "Bagration's rival Barclay did.")
    assert wrong["success"], wrong
    verdict = _verdict(bridge, run_id, "q002")
    assert verdict["passed"] is False
    assert any(d.startswith("FAIL no_unsupported_names") for d in verdict["details"])

    res = await bridge.submit(run_id, "q002", "answer", "Bagration, under Kutuzov.")
    assert res["error_code"] == "TASK_FINAL"


@pytest.mark.asyncio
async def test_a_truncated_answer_is_recorded_as_cut_off(data_dir: Path) -> None:
    """truncated=true becomes finish_reason 'length', which the completion check fails."""
    bridge = _bridge(data_dir, _FakeRetriever())
    run_id = (await bridge.start("chat", "claude-code", "m"))["run_id"]
    await bridge.submit(run_id, "q001", "answer", "Kutuzov commanded", truncated=True)
    verdict = _verdict(bridge, run_id, "q001")
    assert verdict["passed"] is False
    assert any(d.startswith("FAIL finish_stop") for d in verdict["details"])
    state = json.loads((bridge.state_dir / f"{run_id}.json").read_text())
    assert state["records"]["q001"]["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_submitting_a_question_before_its_task_is_out_of_order(data_dir: Path) -> None:
    """A question's context is built when its task is handed out; no context, no answer."""
    bridge = _bridge(data_dir, _FakeRetriever())
    run_id = (await bridge.start("chat", "claude-code", "m"))["run_id"]
    res = await bridge.submit(run_id, "q003", "answer", "Not in the sources.")
    assert res["error_code"] == "OUT_OF_ORDER"
    res = await bridge.submit(run_id, "q001", "entities", "Kutuzov.")
    assert res["error_code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_finish_writes_a_chat_row_that_joins_the_models_probe_row(data_dir: Path) -> None:
    """The row has the local chat rows' dataset id and the probe run's model id."""
    bridge = _bridge(data_dir, _FakeRetriever())
    settings = {"effort": "high", "thinking": "adaptive"}
    started = await bridge.start("chat", "claude-code", "claude-sonnet-5", client_settings=settings)
    run_id = started["run_id"]
    answers = {
        "q001": ("Kutuzov commanded the Russian army.", 40),
        "q002": ("Bagration led the Second Army, under Kutuzov.", None),
        "q003": ("That is not in the sources.", 12),
    }
    for qid, (text, tokens) in answers.items():
        assert (await bridge.next_task(run_id))["task_id"] == qid
        res = await bridge.submit(run_id, qid, "answer", text, output_tokens=tokens)
        assert res["accepted"], res
        assert _verdict(bridge, run_id, qid)["passed"], qid

    done = await bridge.finish(run_id)
    assert done["success"], done
    assert done["passed"] == done["probes_passed"] == 3 and done["total"] == 3
    assert done["headline_score"] == 100.0 and "section_rates" in done

    (row,) = load_results(Path(done["results_path"]))
    assert row.dataset_kind == "chat"
    assert row.dataset_id == (
        "war_and_peace_tiny__chat__ollama_gemma4:31b__ollama_qwen3-embedding:0.6b"
    )
    assert row.dataset_version == "1.0" and row.dataset_source == "builtin"
    assert row.config_name == "chat"
    assert row.scorer_version == CHAT_PROBE_SCORER_VERSION
    assert row.seed is None and row.temperature is None
    assert row.thinking is None and row.thinking_honoured is None
    assert row.chunks_truncated == 0
    assert row.pins_applied is False and row.harness == "mcp:claude-code"
    assert row.harness_settings == settings
    assert row.model_id == "mcp/claude-code/claude-sonnet-5"
    assert row.extras["reference"]["name"] == "tiny"
    assert row.extras["run"]["task_count"] == 3
    assert row.extras["run"]["resubmissions_refused"] == 0
    assert [v["passed"] for v in row.metrics["verdicts"]] == [True, True, True]
    per_query = row.extras["per_query"]
    assert [r["query_id"] for r in per_query] == ["q001", "q002", "q003"]
    assert set(per_query[0]) == {
        "query_id",
        "band",
        "retrieved_entity_ids",
        "shape",
        "shape_reason",
        "shape_source",
        "answer",
        "finish_reason",
        "output_tokens",
        "context_text",
    }
    # the shape the router chose, as the CLI's chat rows record it (Task 10)
    assert (per_query[0]["shape"], per_query[0]["shape_reason"], per_query[0]["shape_source"]) == (
        "lookup",
        "no cue",
        "rules",
    )
    assert per_query[0]["output_tokens"] == 40 and per_query[1]["output_tokens"] > 0
    assert row.output_tokens == sum(r["output_tokens"] for r in per_query)


@pytest.mark.asyncio
async def test_only_selects_questions_and_rejects_unknown_ids(data_dir: Path) -> None:
    """``only`` narrows the run to those question ids, in fixture order."""
    bridge = _bridge(data_dir, _FakeRetriever())
    started = await bridge.start("chat", "claude-code", "m", only=["q003", "q001"])
    assert started["total"] == 2 and started["next"]["task_id"] == "q001"
    res = await bridge.start("chat", "claude-code", "m", only=["q999"])
    assert res["error_code"] == "UNKNOWN_PROBE"


@pytest.mark.asyncio
async def test_reference_errors_name_the_available_packs(tmp_path: Path) -> None:
    """Unknown pack, no pack, or several packs and no name: UNKNOWN_REFERENCE."""
    data = tmp_path / "data"
    bridge = BenchmarkBridge(EngineSettings(paths=PathSettings(data_dir=str(data))))
    res = await bridge.start("chat", "claude-code", "m")
    assert res["error_code"] == "UNKNOWN_REFERENCE" and "export" in res["error"]

    _write_pack(data, "tiny")
    _write_pack(data, "other")
    res = await bridge.start("chat", "claude-code", "m")
    assert res["error_code"] == "UNKNOWN_REFERENCE"
    assert "other" in res["error"] and "tiny" in res["error"]
    res = await bridge.start("chat", "claude-code", "m", reference="missing")
    assert res["error_code"] == "UNKNOWN_REFERENCE" and "other, tiny" in res["error"]


def _walk(value: Any) -> list[tuple[str | None, Any]]:
    """Every (key, value) pair in a JSON payload, nested ones included."""
    out: list[tuple[str | None, Any]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            out.append((k, v))
            out.extend(_walk(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_walk(v))
    else:
        out.append((None, value))
    return out


_FIXTURE_KEYS = {
    "answer_terms",
    "must_not_contain",
    "gold_entities",
    "gold_answer",
    "wrong_entities",
    "decoy_entities",
    "expect_refusal",
    "checks",
    "verdict",
    "verdicts",
    "passed",
    "details",
}


@pytest.mark.asyncio
async def test_no_chat_payload_carries_the_fixture_or_a_verdict(data_dir: Path) -> None:
    """Tasks, submits and progress show the prompt and counts, never expected answers."""
    bridge = _bridge(data_dir, _FakeRetriever())
    started = await bridge.start("chat", "claude-code", "m")
    run_id = started["run_id"]
    payloads = [started, await bridge.next_task(run_id)]
    for qid in ("q001", "q002", "q003"):
        payloads.append(await bridge.submit(run_id, qid, "answer", "Not in the sources."))
    payloads.append(await bridge.progress(run_id))
    assert all(p["success"] for p in payloads), payloads
    text = json.dumps(payloads)
    for key, _value in _walk(json.loads(text)):
        assert key not in _FIXTURE_KEYS, key
    # The alias group, the gold answer and the out-of-scope decoy stay in the fixture.
    for leak in ("the commander-in-chief", "Bagration, under Kutuzov", "Napoleon"):
        assert leak not in text, leak
    assert set(started["next"]) == {
        "done",
        "task_id",
        "probe_id",
        "kind",
        "band",
        "tier",
        "stage",
        "system_prompt",
        "user_prompt",
        "answer_format",
        "remaining",
    }


_QUERIES_FLAGGED = """
version: "1.0"
queries:
  - id: q001
    band: factual_single_hop
    question: "Who commanded the Russian army in 1805?"
    gold_entities: ["Kutuzov"]
    answer_terms: [["Kutuzov", "the commander-in-chief"]]
    gold_answer: |
      Kutuzov.
  - id: q002
    band: multi_hop
    question: "Who led the Second Army, and under whom?"
    gold_entities: ["Bagration", "Kutuzov"]
    answer_terms: ["Bagration", "Kutuzov"]
    gold_answer: |
      Bagration, under Kutuzov.
    answerable: false
  - id: q003
    band: out_of_scope
    question: "Who won the battle of Borodino?"
    expect_refusal: true
    decoy_entities: ["Napoleon"]
"""


@pytest.mark.asyncio
async def test_an_unanswerable_question_is_asked_but_not_scored(tmp_path: Path) -> None:
    """``answerable: false``: still put to the model and kept raw, left out of the score."""
    data = tmp_path / "data"
    root = reference_root(data) / "flagged"
    root.mkdir(parents=True)
    (root / "app.db").write_bytes(b"")
    (root / "queries.yaml").write_text(_QUERIES_FLAGGED, encoding="utf-8")
    write_manifest(root, {**_MANIFEST, "name": "flagged"})
    bridge = _bridge(data, _FakeRetriever(), reference="flagged")
    started = await bridge.start("chat", "claude-code", "m")
    assert started["success"] and started["total"] == 3, started
    run_id = started["run_id"]
    answers = (
        ("q001", "Kutuzov commanded the Russian army."),
        ("q002", "Nobody knows."),
        ("q003", "That is not in the sources."),
    )
    for qid, text in answers:
        assert (await bridge.next_task(run_id))["task_id"] == qid
        res = await bridge.submit(run_id, qid, "answer", text)
        assert res["accepted"], res
    assert _verdict(bridge, run_id, "q002") == {
        "passed": None,
        "details": ["excluded: not answerable from the retrieved context (fixture audit)"],
    }
    done = await bridge.finish(run_id)
    assert done["success"], done
    assert done["probes_total"] == 2 and done["probes_passed"] == 2
    (row,) = load_results(Path(done["results_path"]))
    assert row.metrics["excluded"] == ["q002"]
    assert [v["id"] for v in row.metrics["verdicts"]] == ["q001", "q003"]
    assert [r["query_id"] for r in row.extras["per_query"]] == ["q001", "q002", "q003"]


# ---------------------------------------------------------------------------
# Retrieval modes: graphrag (default), hybrid, naive
# ---------------------------------------------------------------------------


class _FakeHandlers:
    """Stands in for GraphRAGToolHandlers: records which arm the suite called."""

    calls: list[tuple[str, str, int]] = []

    async def graphrag_search(self, query: str, limit: int = 10) -> dict[str, Any]:
        _FakeHandlers.calls.append(("graphrag", query, limit))
        return _RETRIEVED

    async def hybrid_retrieval(self, query: str, limit: int) -> dict[str, Any]:
        _FakeHandlers.calls.append(("hybrid", query, limit))
        return {
            "chunks": [
                {"content": "[CHUNK C0 | book.txt]\n[S1] Kutuzov commanded the Russian army."}
            ],
            "retrieval_stats": {"mode": "hybrid", "faults": []},
        }


def _index_holding(pack: Any, chunks: int) -> None:
    """A built index of ``chunks`` chunk vectors for the pack (what a pin build checks against)."""
    import sqlite3

    pack.index_db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(pack.index_db_path)
    for table in ("vec_search_chunks", "vec_search_nodes"):
        con.execute(f'CREATE TABLE "{table}_rowids" (rowid INTEGER PRIMARY KEY, id TEXT)')
        con.execute(
            f'CREATE TABLE "{table}_vector_chunks00" (rowid INTEGER PRIMARY KEY, vectors BLOB)'
        )
    con.executemany(
        'INSERT INTO "vec_search_chunks_rowids" (id) VALUES (?)',
        [(f"c{i}",) for i in range(chunks)],
    )
    con.commit()
    con.close()


@pytest.mark.asyncio
async def test_the_suite_hands_its_chat_callback_to_the_handlers(data_dir: Path) -> None:
    """The shape router's model: pin_retrieval.py passes one, so rules_then_llm pins ask it (Task 10)."""
    from unittest.mock import MagicMock

    from chaoscypher_core.benchmark.reference import load_pack
    from chaoscypher_core.mcp.benchmark.suites.chat import ChatSuite

    async def router(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"content": '{"shape": "path", "reason": "two people"}'}

    settings = EngineSettings(paths=PathSettings(data_dir=str(data_dir)))
    suite = ChatSuite(settings, load_pack(data_dir, "tiny"), chat_callback=router)
    engine = MagicMock()
    engine.settings = settings

    async def fake_engine() -> Any:
        return engine

    suite._open_engine = fake_engine  # type: ignore[method-assign]  # no pack engine in a unit test
    assert (await suite._handlers()).chat_callback is router
    bare = ChatSuite(settings, load_pack(data_dir, "tiny"))
    bare._open_engine = fake_engine  # type: ignore[method-assign]
    assert (await bare._handlers()).chat_callback is None


@pytest.mark.asyncio
async def test_a_graphrag_pin_file_records_the_router_model_it_was_routed_with(
    data_dir: Path,
) -> None:
    """The header names it only for the graphrag rung and only with a callback (Task 10 review)."""
    from chaoscypher_core.benchmark.pins import PINS_DIR

    async def router(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"content": "{}"}

    gemma = {"provider": "ollama", "model": "gemma4:31b", "seed": 42}
    routed = _suite_with_fake_handlers(
        data_dir, "graphrag", chat_callback=router, router_model=gemma
    )
    assert routed.pin_router_model() == gemma
    pins = await routed.build_pins(routed.pack.root / PINS_DIR)
    assert pins.header["router_model"] == gemma
    # no callback: the router never asked a model, whatever the record says
    bare = _suite_with_fake_handlers(data_dir, "graphrag", router_model=gemma)
    assert bare.pin_router_model() is None
    # the hybrid rung routes nothing
    hybrid = _suite_with_fake_handlers(data_dir, "hybrid", chat_callback=router, router_model=gemma)
    assert hybrid.pin_router_model() is None


def _suite_with_fake_handlers(
    data_dir: Path, retrieval: str, *, indexed_chunks: int = 1, **kw: Any
) -> Any:
    """A chat suite whose engine and handlers are fakes; retrieval goes through the real dispatch.

    The pack's index holds ``indexed_chunks`` chunk vectors: the fakes return one chunk, so a
    pin build finds the retrieval complete.
    """
    from chaoscypher_core.benchmark.reference import load_pack
    from chaoscypher_core.mcp.benchmark.suites.chat import ChatSuite

    settings = EngineSettings(paths=PathSettings(data_dir=str(data_dir)))
    suite = ChatSuite(settings, load_pack(data_dir, "tiny"), retrieval=retrieval, **kw)
    if not suite.pack.index_db_path.exists():
        _index_holding(suite.pack, indexed_chunks)

    async def fake_handlers() -> _FakeHandlers:
        return _FakeHandlers()

    suite._handlers = fake_handlers  # type: ignore[method-assign]  # the seam the test replaces
    _FakeHandlers.calls = []
    return suite


@pytest.mark.asyncio
async def test_hybrid_mode_renders_the_hybrid_arms_chunks_only(data_dir: Path) -> None:
    """No entity or relationship lines, the chat budget, and the mode on the dataset id."""
    suite = _suite_with_fake_handlers(data_dir, "hybrid")
    retrieved = await suite.retrieve("Who commanded the Russian army in 1805?")
    assert set(retrieved) == {"chunks", "retrieval_stats"}  # the stats carry the fault record
    assert _FakeHandlers.calls == [
        ("hybrid", "Who commanded the Russian army in 1805?", suite.settings.graphrag.chunk_limit)
    ]
    assert format_retrieved_context(retrieved).startswith("[CHUNK C0 | book.txt]")
    assert suite.run_info()["dataset_id"].endswith("__retrieval_hybrid")


@pytest.mark.asyncio
async def test_graphrag_mode_keeps_the_full_search_and_todays_dataset_id(data_dir: Path) -> None:

    suite = _suite_with_fake_handlers(data_dir, "graphrag")
    retrieved = await suite.retrieve("q")
    assert retrieved == _RETRIEVED
    assert _FakeHandlers.calls == [("graphrag", "q", suite.settings.graphrag.chunk_limit)]
    assert suite.run_info()["dataset_id"] == (
        "war_and_peace_tiny__chat__ollama_gemma4:31b__ollama_qwen3-embedding:0.6b"
    )


@pytest.mark.asyncio
async def test_naive_mode_builds_the_index_once_through_the_injected_factory(
    data_dir: Path,
) -> None:
    """The factory gets the pack's corpus, embedder and index dir; one build serves every question."""
    specs: list[NaiveIndexSpec] = []
    asked: list[str] = []

    async def factory(spec: NaiveIndexSpec, settings: EngineSettings) -> Any:
        specs.append(spec)

        async def retriever(question: str) -> list[dict[str, Any]]:
            asked.append(question)
            return [{"content": "[CHUNK C0 | tiny.txt]\n[S1] Kutuzov."}]

        return retriever

    suite = _suite_with_fake_handlers(data_dir, "naive", naive_retriever=factory)
    first = await suite.retrieve("q1")
    second = await suite.retrieve("q2")
    assert first == second == {"chunks": [{"content": "[CHUNK C0 | tiny.txt]\n[S1] Kutuzov."}]}
    assert asked == ["q1", "q2"] and _FakeHandlers.calls == []
    (spec,) = specs
    assert spec.corpus_id == "war_and_peace_tiny"
    assert spec.embedder == "ollama/qwen3-embedding:0.6b"
    assert spec.cache_dir == suite.pack.index_dir / "naive-ollama_qwen3-embedding:0.6b"
    assert suite.run_info()["dataset_id"].endswith("__retrieval_naive")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["graphrag", "hybrid", "naive"])
async def test_a_decomposed_question_reaches_every_rung_composed(data_dir: Path, mode: str) -> None:
    """An NFD question is composed before any rung tokenizes or embeds it (audit wave 5).

    Decomposed, "Bezúkhov" was the two words ``bezu`` and ``khov`` to every tokenizer.
    """
    import unicodedata

    asked: list[str] = []

    async def factory(spec: NaiveIndexSpec, settings: EngineSettings) -> Any:
        async def retriever(question: str) -> list[dict[str, Any]]:
            asked.append(question)
            return [{"content": "[CHUNK C0 | tiny.txt]\n[S1] Kutuzov."}]

        return retriever

    suite = _suite_with_fake_handlers(data_dir, mode, naive_retriever=factory)
    composed = "Who is Bezúkhov's father?"
    await suite.retrieve(unicodedata.normalize("NFD", composed))
    seen = asked if mode == "naive" else [call[1] for call in _FakeHandlers.calls]
    assert seen == [composed]


@pytest.mark.asyncio
async def test_the_bridge_records_the_mode_and_refuses_unknown_or_misplaced_ones(
    data_dir: Path,
) -> None:
    """``retrieval`` is validated at start, persisted, written on the row; probes take none."""
    bridge = _bridge(data_dir, _FakeRetriever())
    started = await bridge.start("chat", "claude-code", "m", retrieval="hybrid")
    assert started["success"], started
    run_id = started["run_id"]
    state = json.loads((bridge.state_dir / f"{run_id}.json").read_text())
    assert state["retrieval"] == "hybrid"
    assert state["dataset_id"].endswith("__retrieval_hybrid")
    for qid, text in (
        ("q001", "Kutuzov commanded the Russian army."),
        ("q002", "Bagration, under Kutuzov."),
        ("q003", "That is not in the sources."),
    ):
        assert (await bridge.next_task(run_id))["task_id"] == qid
        await bridge.submit(run_id, qid, "answer", text)
    done = await bridge.finish(run_id)
    (row,) = load_results(Path(done["results_path"]))
    assert row.extras["retrieval"] == "hybrid"
    assert row.dataset_id.endswith("__retrieval_hybrid")

    default = await bridge.start("chat", "claude-code", "m")
    state = json.loads((bridge.state_dir / f"{default['run_id']}.json").read_text())
    assert state["retrieval"] == "graphrag"

    res = await bridge.start("chat", "claude-code", "m", retrieval="plain")
    assert res["error_code"] == "INVALID_ARGUMENT" and "graphrag, hybrid, naive" in res["error"]
    res = await bridge.start("probes", "claude-code", "m", retrieval="hybrid")
    assert res["error_code"] == "INVALID_ARGUMENT" and "retrieval" in res["error"]


@pytest.mark.asyncio
async def test_a_naive_run_needs_the_factory_the_cli_provides(data_dir: Path) -> None:
    """A bridge built without the CLI's naive retriever refuses a naive run at start, naming the extra."""
    bridge = BenchmarkBridge(EngineSettings(paths=PathSettings(data_dir=str(data_dir))))
    res = await bridge.start("chat", "claude-code", "m", retrieval="naive")
    assert res["error_code"] == "INVALID_ARGUMENT"
    assert "naive" in res["error"] and "benchmark" in res["error"]


@pytest.mark.asyncio
async def test_a_fake_retriever_is_cached_per_mode(data_dir: Path) -> None:
    """The suite cache is keyed by mode too, so a hybrid run never reuses a graphrag suite's engine."""
    bridge = _bridge(data_dir, _FakeRetriever())
    graphrag = bridge.suite("chat", "tiny")
    hybrid = bridge.suite("chat", "tiny", "hybrid")
    assert graphrag is not hybrid
    assert bridge.suite("chat", "tiny", "graphrag") is graphrag


# ---------------------------------------------------------------------------
# Pinned retrieval: built once from the pack, served to every run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_pins_retrieves_each_question_once_and_a_run_is_served_from_them(
    data_dir: Path,
) -> None:
    """The pack's pins serve every question; the row names the file and the count it served."""
    from chaoscypher_core.benchmark.pins import PINS_DIR

    builder = _suite_with_fake_handlers(data_dir, "graphrag")
    pins = await builder.build_pins(builder.pack.root / PINS_DIR)
    assert [c[1] for c in _FakeHandlers.calls] == [
        "Who commanded the Russian army in 1805?",
        "Who led the Second Army, and under whom?",
        "Who won the battle of Borodino?",
    ]
    assert pins.header["count"] == 3 and pins.path.exists()
    assert pins.header["retrieval"] == "graphrag" and pins.header["pack"] == "tiny"

    async def _must_not_retrieve(question: str) -> dict[str, Any]:
        raise AssertionError(question)

    bridge = _bridge(data_dir, _must_not_retrieve)
    run_id = (await bridge.start("chat", "claude-code", "m"))["run_id"]
    for qid, text in [
        ("q001", "Kutuzov."),
        ("q002", "Bagration, under Kutuzov."),
        ("q003", "Not in the sources."),
    ]:
        task = await bridge.next_task(run_id)
        assert task["task_id"] == qid
        assert format_retrieved_context(_RETRIEVED) in task["user_prompt"]
        assert (await bridge.submit(run_id, qid, "answer", text))["accepted"]
    done = await bridge.finish(run_id)
    (row,) = load_results(Path(done["results_path"]))
    served = row.extras["retrieval_pins"]
    assert served["file"] == pins.path.name and served["served"] == served["of"] == 3


@pytest.mark.asyncio
async def test_a_chat_row_records_the_question_file_and_the_sha256_of_its_bytes(
    data_dir: Path,
) -> None:
    """Audit wave 3: the MCP row carries ``queries_file`` and ``queries_sha256`` as the CLI rows do.

    The sha256 is of the bytes read when the fixture loaded, so a fixture
    edited after the run started does not change what the row says it asked.
    """
    import hashlib

    from chaoscypher_core.benchmark.results import row_comparability

    bridge = _bridge(data_dir, _FakeRetriever())
    run_id = (await bridge.start("chat", "claude-code", "m"))["run_id"]
    pack_queries = reference_root(data_dir) / "tiny" / "queries.yaml"
    asked = hashlib.sha256(pack_queries.read_bytes()).hexdigest()
    for qid in ("q001", "q002", "q003"):
        await bridge.next_task(run_id)
        await bridge.submit(run_id, qid, "answer", "Not in the sources.")
    pack_queries.write_text(pack_queries.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    done = await bridge.finish(run_id)
    (row,) = load_results(Path(done["results_path"]))
    assert Path(row.extras["queries_file"]) == pack_queries
    assert row.extras["queries_sha256"] == asked
    assert row_comparability(row)["queries_sha256"] == asked


def test_a_holdout_question_file_replaces_the_packs_and_is_recorded(
    data_dir: Path, tmp_path: Path
) -> None:
    from chaoscypher_core.benchmark.reference import load_pack
    from chaoscypher_core.mcp.benchmark.suites.chat import ChatSuite

    pack = load_pack(data_dir, "tiny")
    holdout = tmp_path / "holdout.yaml"
    holdout.write_text(pack.queries_path.read_text(encoding="utf-8"), encoding="utf-8")
    settings = EngineSettings(paths=PathSettings(data_dir=str(data_dir)))
    suite = ChatSuite(settings, pack, queries_file=holdout)
    assert [q.id for q in suite._queries().queries] == ["q001", "q002", "q003"]
    assert suite.queries_file == holdout
    assert ChatSuite(settings, pack).queries_file == pack.queries_path


@pytest.mark.asyncio
async def test_a_pack_without_pins_retrieves_live_and_says_so(data_dir: Path) -> None:
    bridge = _bridge(data_dir, _FakeRetriever())
    run_id = (await bridge.start("chat", "claude-code", "m"))["run_id"]
    for qid in ("q001", "q002", "q003"):
        await bridge.next_task(run_id)
        await bridge.submit(run_id, qid, "answer", "Not in the sources.")
    done = await bridge.finish(run_id)
    (row,) = load_results(Path(done["results_path"]))
    assert row.extras["retrieval_pins"] is None


@pytest.mark.asyncio
async def test_build_pins_refuses_a_hybrid_retrieval_whose_keyword_search_raised(
    data_dir: Path, tmp_path: Path
) -> None:
    """Audit wave 3: the hybrid rung records its faults; a keyword arm that raised is never pinned.

    The real handlers over a real search repository whose chunk keyword index
    is gone: the vector half still fills the budget, so the count alone
    accepted every such retrieval until 2026-10-06.
    """
    from unittest.mock import AsyncMock, MagicMock

    from sqlmodel import SQLModel

    from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
    from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository
    from chaoscypher_core.benchmark.pins import PINS_DIR
    from chaoscypher_core.benchmark.retrieval import DegradedRetrievalError
    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        GraphRAGToolHandlers,
    )

    suite = _suite_with_fake_handlers(data_dir, "hybrid")
    path = tmp_path / "search.db"
    engine = get_engine(str(path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    repo = SearchRepository(engine, vector_dim=4, embedding_model="m")
    repo.index_embeddings_batch([("chunk:c1", [1.0, 0.0, 0.0, 0.0])], item_type="chunk")
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE fulltext_index_chunks")
    indexing = MagicMock()
    indexing.get_chunks_by_ids_batch.side_effect = lambda ids, **_: [
        {"id": i, "content": f"Text of {i}.", "source_id": "s1"} for i in ids
    ]
    handlers = GraphRAGToolHandlers(
        MagicMock(),
        repo,
        indexing_repository=indexing,
        embedding_callback=AsyncMock(return_value={"embedding": [1.0, 0.0, 0.0, 0.0]}),
        settings=suite.settings,
    )

    async def real_handlers() -> GraphRAGToolHandlers:
        return handlers

    suite._handlers = real_handlers  # type: ignore[method-assign]  # the seam the test replaces
    try:
        with pytest.raises(DegradedRetrievalError, match="keyword_search:chunk"):
            await suite.build_pins(suite.pack.root / PINS_DIR)
        assert not list((suite.pack.root / PINS_DIR).glob("hybrid-*.json"))
    finally:
        evict_engine(path)


@pytest.mark.asyncio
async def test_pins_built_for_another_graph_are_refused(data_dir: Path) -> None:
    from chaoscypher_core.benchmark.pins import PINS_DIR
    from chaoscypher_core.mcp.benchmark.errors import BenchmarkError

    builder = _suite_with_fake_handlers(data_dir, "graphrag")
    await builder.build_pins(builder.pack.root / PINS_DIR)
    builder.pack.snapshot_path.write_bytes(b"another graph")
    fresh = _suite_with_fake_handlers(data_dir, "graphrag")
    with pytest.raises(BenchmarkError, match="graph_sha256"):
        fresh.pins()


@pytest.mark.asyncio
async def test_pins_from_another_index_of_the_same_kind_are_not_mixed(data_dir: Path) -> None:
    """Graphrag and hybrid read one index: a second build from a different one is refused."""
    from chaoscypher_core.benchmark.pins import PinError, RetrievalPins

    pins_dir = data_dir / "pins-out"
    graphrag = _suite_with_fake_handlers(data_dir, "graphrag")
    built = await graphrag.build_pins(pins_dir)
    # as if the graphrag pins had come from an index built on another day
    stale = RetrievalPins.load(built.path)
    stale.header["index_digest"] = "0" * 16
    stale.save()
    hybrid = _suite_with_fake_handlers(data_dir, "hybrid")
    with pytest.raises(PinError, match="rebuild every pin file of the kind"):
        await hybrid.build_pins(pins_dir)
    assert (await hybrid.build_pins(pins_dir, force=True)).path.exists()


@pytest.mark.asyncio
async def test_build_pins_embeds_each_question_once_through_the_vector_cache(
    data_dir: Path,
) -> None:
    """The handlers get the cache's callback, and the cache file is written beside the pins."""
    from chaoscypher_core.mcp.benchmark.suites.chat import ChatSuite

    embedded: list[str] = []

    class _Engine:
        graph_repository = search_repository = storage_adapter = None
        database_name = "db"
        settings = None

        class embedding_service:  # noqa: N801 - mimics the engine attribute
            @staticmethod
            async def embed(text: str) -> dict[str, list[float]]:
                embedded.append(text)
                return {"embedding": [1.0] + [0.0] * 1023}  # qwen3-embedding:0.6b's size

    seen: list[Any] = []

    import chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers as gh

    original = gh.GraphRAGToolHandlers

    class _Handlers(original):  # type: ignore[misc,valid-type]  # the recipe runs the real rest
        def __init__(self, **kw: Any) -> None:
            super().__init__(**kw)
            seen.append(kw["embedding_callback"])

        async def graphrag_search(
            self, query: str, limit: int | None = 10, **_: Any
        ) -> dict[str, Any]:
            callback = self.embedding_callback
            if callback is None:  # the retrieval recipe's sample run without an embedder
                return _RETRIEVED
            await callback(query)
            await callback(query)  # the hybrid arm embeds the same text again
            return _RETRIEVED

    from chaoscypher_core.benchmark.reference import load_pack

    settings = EngineSettings(paths=PathSettings(data_dir=str(data_dir)))
    suite = ChatSuite(settings, load_pack(data_dir, "tiny"))
    _index_holding(suite.pack, 1)
    suite._engine = _Engine()  # the seam: an engine already open

    gh.GraphRAGToolHandlers = _Handlers  # type: ignore[misc]
    try:
        pins_dir = data_dir / "pins-out"
        await suite.build_pins(pins_dir)
    finally:
        gh.GraphRAGToolHandlers = original  # type: ignore[misc]
    assert embedded == [
        "Who commanded the Russian army in 1805?",
        "Who led the Second Army, and under whom?",
        "Who won the battle of Borodino?",
    ]
    assert (pins_dir / "query-vectors-ollama_qwen3-embedding:0.6b-1024d.json").exists()
    # after the build the suite's own retrieval embeds directly again
    assert suite._vector_cache is None


# ---------------------------------------------------------------------------
# The run's levers are the client model's, resolved as the CLI resolves them (R10)
# ---------------------------------------------------------------------------


def _with_profile(bridge: BenchmarkBridge) -> None:
    """A profile for model 'small:9b' that turns the answer policy off and widens the chunks."""
    from chaoscypher_core.settings import GraphRAGProfileRule

    bridge.settings.graphrag.profiles = {"lean": {"answer_policy": False, "chunk_limit": 7}}
    bridge.settings.graphrag.profile_rules = [GraphRAGProfileRule(profile="lean", model="small:9b")]


def test_the_suite_resolves_profiles_under_the_packs_domain(tmp_path: Path) -> None:
    """A pack whose manifest names no domain reads it from its snapshot (wave 4).

    Until 2026-10-06 the suite read the manifest's ``domain`` alone: None for
    every v3 pack, so a domain's profile rule never applied over MCP while
    the CLI (the dataset's domain) applied it.
    """
    import sqlite3
    from contextlib import closing

    from chaoscypher_core.settings import GraphRAGProfileRule

    data = tmp_path / "data"
    root = _write_pack(data)
    with closing(sqlite3.connect(root / "app.db")) as conn:
        conn.execute("CREATE TABLE sources (forced_domain TEXT, extraction_domain TEXT)")
        conn.execute("INSERT INTO sources VALUES ('literary', NULL)")
        conn.commit()
    bridge = _bridge(data, _FakeRetriever())
    bridge.settings.graphrag.profiles = {"lean": {"chunk_limit": 7}}
    bridge.settings.graphrag.profile_rules = [
        GraphRAGProfileRule(profile="lean", domain="literary")
    ]
    suite: Any = bridge.suite("chat", "tiny")
    assert suite.pack.domain == "literary"
    assert suite.run_settings({"model": "any:1b"}).graphrag.chunk_limit == 7


@pytest.mark.asyncio
async def test_a_run_asks_with_its_models_profile_and_records_the_levers(data_dir: Path) -> None:
    from chaoscypher_core.benchmark.chat_run import prompt_template_sha256

    bridge = _bridge(data_dir, _FakeRetriever())
    _with_profile(bridge)
    run_id = (await bridge.start("chat", "claude-code", "small:9b"))["run_id"]
    task = await bridge.next_task(run_id)
    assert task["user_prompt"] == grounded_chat_prompt(
        "Who commanded the Russian army in 1805?",
        format_retrieved_context(_RETRIEVED),
        ask_for_citations=True,
        answer_policy=False,  # the profile's lever, not the plain settings'
    )
    for qid in ("q001", "q002", "q003"):
        await bridge.next_task(run_id)
        await bridge.submit(run_id, qid, "answer", "Not in the sources.")
    (row,) = load_results(Path((await bridge.finish(run_id))["results_path"]))
    run = row.extras["run_settings"]
    assert run["profile"] == "lean" and run["levers"]["chunk_limit"] == 7
    assert run["answer_policy"] is False and run["pack"] == "tiny"
    resolved = bridge.settings.graphrag.model_copy(update={"answer_policy": False})
    assert run["prompt_sha256"] == prompt_template_sha256(resolved)
    assert row.extras["per_query"][0]["retrieved_entity_ids"] == ["n1", "n2"]


@pytest.mark.asyncio
async def test_a_pinned_pack_refuses_a_run_its_pins_cannot_serve(data_dir: Path) -> None:
    """Retrieving live, uncached, would hand this run another context than the local rows'."""
    from chaoscypher_core.benchmark.pins import PINS_DIR, RetrievalPins

    builder = _suite_with_fake_handlers(data_dir, "graphrag")
    pins = await builder.build_pins(builder.pack.root / PINS_DIR)

    async def _must_not_retrieve(question: str) -> dict[str, Any]:
        raise AssertionError(question)

    # a model whose profile changes a retrieval lever: no pins were built for it
    bridge = _bridge(data_dir, _must_not_retrieve)
    _with_profile(bridge)
    started = await bridge.start("chat", "claude-code", "small:9b")
    assert started["success"] is False and "none for this run's settings" in started["error"]

    # a question the file does not hold
    del pins.entries["q002"]
    pins.save()
    bridge = _bridge(data_dir, _must_not_retrieve)
    run_id = (await bridge.start("chat", "claude-code", "m"))["run_id"]
    await bridge.submit(run_id, "q001", "answer", "Kutuzov.")
    refused = await bridge.next_task(run_id)
    assert refused["success"] is False and "has no pin for q002" in refused["error"]
    assert RetrievalPins.load(pins.path).header["count"] == 2


@pytest.mark.asyncio
async def test_a_degraded_retrieval_is_never_pinned(data_dir: Path) -> None:
    """An index emptied by a dimension change was pinned and served without complaint (R8)."""
    from chaoscypher_core.benchmark.pins import PINS_DIR
    from chaoscypher_core.benchmark.retrieval import DegradedRetrievalError

    empty = _suite_with_fake_handlers(data_dir, "graphrag", indexed_chunks=0)
    with pytest.raises(DegradedRetrievalError, match="holds no chunks"):
        await empty.build_pins(empty.pack.root / PINS_DIR)
    empty.pack.index_db_path.unlink()
    # the fake returns one chunk while the index holds five: short of the budget
    short = _suite_with_fake_handlers(data_dir, "graphrag", indexed_chunks=5)
    with pytest.raises(DegradedRetrievalError, match="returned 1 chunks"):
        await short.build_pins(short.pack.root / PINS_DIR)
    assert not list((short.pack.root / PINS_DIR).glob("graphrag-*.json"))
