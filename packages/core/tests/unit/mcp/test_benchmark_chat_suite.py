# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The grounded-chat suite: an MCP client answers a reference pack's questions.

A fake reference pack lives in ``tmp_path`` and a fake retriever stands in
for GraphRAG search, so no SQLite database or embedder is touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.benchmark.chat_prompt import format_retrieved_context, grounded_chat_prompt
from chaoscypher_core.benchmark.reference import reference_root, write_manifest
from chaoscypher_core.benchmark.results import load_results
from chaoscypher_core.benchmark.scorers.chat_probes import CHAT_PROBE_SCORER_VERSION
from chaoscypher_core.mcp.benchmark import BenchmarkBridge
from chaoscypher_core.settings import EngineSettings, PathSettings


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
            {"label": "Kutuzov", "description": "commanded the Russian army in 1805"},
            {"label": "Bagration", "description": "led the Second Army under Kutuzov"},
        ],
        "relationships": [{"source": "Bagration", "label": "serves under", "target": "Kutuzov"}],
    },
    "chunks": [{"text": "Kutuzov commanded the Russian army. Bagration led the Second Army."}],
    "entities": [{"id": "n1"}, {"id": "n2"}],
}

_MANIFEST = {
    "name": "tiny",
    "fixture_id": "war_and_peace_tiny",
    "fixture_version": "1.0",
    "corpus_id": "war_and_peace_tiny",
    "extractor": "ollama/gemma4:31b",
    "extractor_label": "Gemma 4 31B",
    "embedder": "ollama/qwen3-embedding:0.6b",
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
        "Who commanded the Russian army in 1805?", format_retrieved_context(_RETRIEVED)
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
        "answer",
        "finish_reason",
        "output_tokens",
        "context_text",
    }
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
