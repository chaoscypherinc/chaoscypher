# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""scripts/benchmark/rescore_probes.py rescores probe-scored chat rows only."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chaoscypher_cli.benchmark import discovery
from chaoscypher_cli.benchmark.scorers import chat_probes


SCRIPT = Path(__file__).resolve().parents[5] / "scripts" / "benchmark" / "rescore_probes.py"


def _load_script() -> Any:
    """Import the script as a module without running main()."""
    spec = importlib.util.spec_from_file_location("rescore_probes_script", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(*, metrics: dict[str, Any], extras: dict[str, Any]) -> SimpleNamespace:
    """A saved chat row with only the fields the rescorer reads."""
    return SimpleNamespace(
        model_label="M",
        dataset_id="corpus__chat__x",
        metrics=metrics,
        extras=extras,
        headline_score=0.42,
    )


def test_rescore_chat_skips_judged_rows_and_rescores_probe_rows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A judged row keeps its judge metrics; a probe row (even failed) is rescored."""
    script = _load_script()
    scored: list[Any] = []

    class _FakeScorer:
        """Stand-in ChatProbeScorer recording what it scored."""

        def score(self, raw: Any, _queries: Any) -> SimpleNamespace:
            """Return fixed probe metrics."""
            scored.append(raw.extras)
            return SimpleNamespace(metrics={"verdicts": []}, headline_score=0.9)

    monkeypatch.setattr(chat_probes, "ChatProbeScorer", _FakeScorer)
    monkeypatch.setattr(
        discovery, "load_dataset_bundle", lambda _cid: SimpleNamespace(queries=object())
    )

    judged_metrics = {"faithfulness_mean": 4.2}
    judged = _row(
        metrics=dict(judged_metrics),
        extras={
            "judge_model": "claude-judge",
            "per_query": [{"query_id": "q1", "answer": "a", "judge_scores": {}}],
        },
    )
    probe = _row(
        metrics={},
        extras={
            "judge_model": None,
            "per_query": [{"query_id": "q1", "answer": "a", "context_text": "ctx"}],
        },
    )

    assert script._rescore_chat(judged) == 0
    assert judged.metrics == judged_metrics
    assert judged.headline_score == 0.42
    assert "judge-scored chat row, not rescored" in capsys.readouterr().out

    script._rescore_chat(probe)
    assert probe.metrics == {"verdicts": []}
    assert probe.headline_score == 0.9
    assert scored == [probe.extras]


def test_split_raw_harvest_response_inverts_the_recorded_format() -> None:
    """The two passes come back out of ``format_raw_harvest_response``; odd text does not."""
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        format_raw_harvest_response,
    )

    script = _load_script()
    raw = format_raw_harvest_response("E|A|T||0.9|S1|d\n", "R|0|1|x|0.9|S1|j")
    assert script.split_raw_harvest_response(raw) == ("E|A|T||0.9|S1|d\n", "R|0|1|x|0.9|S1|j")
    assert script.split_raw_harvest_response(format_raw_harvest_response("E|A", "")) == (
        "E|A",
        "",
    )
    assert script.split_raw_harvest_response("no headers at all") is None
    doubled = format_raw_harvest_response(format_raw_harvest_response("a", "b"), "c")
    assert script.split_raw_harvest_response(doubled) is None


@pytest.mark.asyncio
async def test_reparse_recovers_an_entity_line_with_the_aliases_field_omitted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A saved record whose raw text omits the aliases slot gains that entity on reparse."""
    from datetime import UTC, datetime

    from chaoscypher_core.benchmark.results import BenchmarkResult, dump_results, load_results
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        format_raw_harvest_response,
    )

    script = _load_script()
    sentences = [
        "Anna Pávlovna Schérer received her guests in July.",
        "Anna Pávlovna quoted Rousseau to the vicomte.",
    ]
    raw = format_raw_harvest_response(
        "E|Anna Pávlovna|Character|Annette|0.9|S1|The hostess of the soirée in July\n"
        "E|Rousseau|Author|1.0|S2|Author quoted by Anna Pávlovna in the salon",
        "R|0|1|mentions|0.8|S2|Anna Pávlovna quoted Rousseau to the vicomte.",
    )
    saved_entity = {"name": "Anna Pávlovna", "type": "Character", "aliases": ["Annette"]}
    record = {
        "id": "A1-easy",
        "entities": [saved_entity],
        "relationships": [],
        "parser_lines_dropped": 1,
        "invalid_relationship_count": 0,
        "raw_llm_response": raw,
        "sentences": sentences,
        "relationship_instructions": "",
    }
    row = BenchmarkResult(
        model_id="m",
        model_label="Fake Model",
        dataset_id="probes",
        dataset_kind="probes",
        dataset_version="1",
        dataset_source="builtin",
        config_name=None,
        headline_score=0.0,
        metrics={"verdicts": []},
        latency_ms_total=0,
        latency_ms_per_chunk_p50=0,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        success=True,
        error=None,
        timestamp=datetime.now(UTC),
        benchmark_version="test",
        scorer_version=1,
        seed=0,
        temperature=None,
        thinking=None,
        thinking_honoured=None,
        chunks_truncated=0,
        chunks_aborted_by_loop=0,
        extras={"probes": [record], "selected_ids": ["A1-easy"]},
    )
    path = tmp_path / "results.json"
    dump_results([row], path)
    rows = load_results(path)
    probe = SimpleNamespace(id="A1-easy", entity_exclusions=(), strict_types=False, entity_types=())

    await script.reparse_rows(rows, {"A1-easy": probe})

    reparsed = rows[0].extras["probes"][0]
    assert [e["name"] for e in reparsed["entities"]] == ["Anna Pávlovna", "Rousseau"]
    assert reparsed["entities"][1]["aliases"] == []
    assert reparsed["parser_lines_dropped"] == 0
    assert "Fake Model: reparsed 1, entity count changed in 1 (+1" in capsys.readouterr().out
