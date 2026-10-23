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
    # the question set the row answered (a rescore that answers none of it is refused)
    asked = SimpleNamespace(queries=[SimpleNamespace(id="q1")])
    monkeypatch.setattr(
        discovery, "load_dataset_bundle", lambda _cid: SimpleNamespace(queries=asked)
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


def test_a_chat_rescore_stamps_and_prints_the_scorer_recipe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The rescored row carries the recipe next to the version, and the script prints both (H3)."""
    from datetime import UTC, datetime

    from chaoscypher_cli.benchmark.results import BenchmarkResult, dump_results, load_results
    from chaoscypher_core.benchmark.queries import LabeledQuery, LabeledQuerySet
    from chaoscypher_core.benchmark.scorers.chat_probe_recipe import scorer_recipe

    queries = LabeledQuerySet(
        version="1",
        queries=[
            LabeledQuery(
                id="q1",
                band="factual_single_hop",
                question="Who built the IMP?",
                gold_entities=["BBN"],
                gold_answer="BBN.",
                answer_terms=["BBN"],
            )
        ],
    )
    monkeypatch.setattr(
        discovery, "load_dataset_bundle", lambda _cid: SimpleNamespace(queries=queries)
    )
    record = {"query_id": "q1", "answer": "BBN.", "context_text": "BBN built the IMP."}
    row = BenchmarkResult(
        model_id="m",
        model_label="M",
        dataset_id="corpus__chat__x",
        dataset_kind="chat",
        dataset_version="1",
        dataset_source="builtin",
        config_name=None,
        headline_score=0.0,
        metrics={"verdicts": [], "chat_probe_scorer_version": 5},
        latency_ms_total=0,
        latency_ms_per_chunk_p50=0,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        success=True,
        error=None,
        timestamp=datetime.now(UTC),
        benchmark_version="test",
        scorer_version=5,
        seed=0,
        temperature=0.0,
        thinking=False,
        thinking_honoured=None,
        chunks_truncated=0,
        chunks_aborted_by_loop=0,
        extras={"judge_model": None, "per_query": [record]},
    )
    assert row.scorer_recipe is None  # a v5 row: nothing recorded the behaviour
    src, out = tmp_path / "in.json", tmp_path / "out.json"
    dump_results([row], src)
    script = _load_script()
    monkeypatch.setattr("sys.argv", ["rescore_probes.py", str(src), str(out)])

    script.main()

    rescored = load_results(out)[0]
    assert rescored.scorer_version == chat_probes.CHAT_PROBE_SCORER_VERSION
    assert rescored.scorer_recipe == scorer_recipe()
    printed = capsys.readouterr().out
    assert (
        f"chat probe scorer: v{chat_probes.CHAT_PROBE_SCORER_VERSION} recipe {scorer_recipe()}"
        in printed
    )


# ---------------------------------------------------------------------------
# A row is rescored against the question file it was asked (audit wave 2)
# ---------------------------------------------------------------------------


def _chat_result(model: str, records: list[dict[str, Any]], queries_file: str | None) -> Any:
    """A probe-scored chat row of the book dataset, as a sweep writes it."""
    from datetime import UTC, datetime

    from chaoscypher_cli.benchmark.results import BenchmarkResult

    extras: dict[str, Any] = {"judge_model": None, "per_query": records}
    if queries_file is not None:
        extras["queries_file"] = queries_file
    return BenchmarkResult(
        model_id=f"ollama/{model}",
        model_label=model,
        dataset_id="war_and_peace_book1__chat__a__b",
        dataset_kind="chat",
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
        scorer_version=6,
        seed=42,
        temperature=0.0,
        thinking=True,
        thinking_honoured=None,
        chunks_truncated=0,
        chunks_aborted_by_loop=0,
        extras=extras,
    )


HOLDOUT_YAML = """version: "h-test"
holdout: true
queries:
  - id: h001
    band: factual_single_hop
    question: "Who built the IMP?"
    gold_entities: ["BBN"]
    answer_terms: ["BBN"]
    gold_answer: "BBN."
"""


def _holdout_file(tmp_path: Path) -> Path:
    path = tmp_path / "holdout.yaml"
    path.write_text(HOLDOUT_YAML, encoding="utf-8")
    return path


def _run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rows: list[Any], *extra: str
) -> list[Any]:
    from chaoscypher_cli.benchmark.results import dump_results, load_results

    src, out = tmp_path / "in.json", tmp_path / "out.json"
    dump_results(rows, src)
    monkeypatch.setattr("sys.argv", ["rescore_probes.py", *extra, str(src), str(out)])
    _load_script().main()
    return load_results(out)


def test_a_holdout_row_is_rescored_against_its_own_question_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The row's extras.queries_file, not the dataset fixture: its h-ids are scored."""
    holdout = _holdout_file(tmp_path)
    record = {"query_id": "h001", "answer": "BBN.", "context_text": "BBN built the IMP."}
    rescored = _run(monkeypatch, tmp_path, [_chat_result("M", [record], str(holdout))])[0]
    verdicts = rescored.metrics["verdicts"]
    assert [(v["id"], v["passed"]) for v in verdicts] == [("h001", True)]
    assert rescored.headline_score == 100.0


def test_an_old_holdout_row_needs_queries_and_never_scores_zero_silently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A row that records no file answers none of the main fixture: refused, never '0 changed'."""
    holdout = _holdout_file(tmp_path)
    record = {"query_id": "h001", "answer": "BBN.", "context_text": "BBN built the IMP."}
    rows = [_chat_result("M", [record], None)]
    with pytest.raises(SystemExit, match=r"none of its 1 answers is a question of .*h001"):
        _run(monkeypatch, tmp_path, rows)
    rescored = _run(monkeypatch, tmp_path, rows, "--queries", str(holdout))[0]
    assert [v["id"] for v in rescored.metrics["verdicts"]] == ["h001"]


def test_rows_that_asked_different_question_files_are_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A holdout row and a main row in one file: one rescore would score one of them as zero."""
    holdout = _holdout_file(tmp_path)
    h = _chat_result("H", [{"query_id": "h001", "answer": "BBN."}], str(holdout))
    main = _chat_result("M", [{"query_id": "q001", "answer": "July 1805."}], None)
    with pytest.raises(SystemExit, match="different question files"):
        _run(monkeypatch, tmp_path, [h, main])
    other = tmp_path / "other.yaml"
    other.write_text(HOLDOUT_YAML, encoding="utf-8")
    with pytest.raises(SystemExit, match="rows record another question file"):
        _run(monkeypatch, tmp_path, [h], "--queries", str(other))


def test_rows_asked_two_contents_of_one_file_are_refused_and_a_rescore_records_its_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Audit wave 3: ``queries_sha256`` tells two versions of one question file apart."""
    import hashlib

    holdout = _holdout_file(tmp_path)
    record = {"query_id": "h001", "answer": "BBN.", "context_text": "BBN built the IMP."}
    a, b = (_chat_result(m, [record], str(holdout)) for m in ("A", "B"))
    a.extras["queries_sha256"], b.extras["queries_sha256"] = "a" * 64, "b" * 64
    with pytest.raises(SystemExit, match=r"different question-file contents.*--allow-mixed"):
        _run(monkeypatch, tmp_path, [a, b])
    rescored = _run(monkeypatch, tmp_path, [a, b], "--allow-mixed")
    digest = hashlib.sha256(holdout.read_bytes()).hexdigest()
    assert {r.extras["rescored_queries_sha256"] for r in rescored} == {digest}
    assert {r.extras["queries_sha256"] for r in rescored} == {"a" * 64, "b" * 64}  # as asked
