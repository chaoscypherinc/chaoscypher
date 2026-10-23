# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Benchmark results file: harness-track fields and legacy compatibility."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from chaoscypher_core.benchmark.results import (
    BENCHMARK_VERSION,
    BenchmarkResult,
    current_app_version,
    dump_results,
    format_harness_settings,
    load_results,
)


if TYPE_CHECKING:
    from pathlib import Path


def _row(**kw: Any) -> BenchmarkResult:
    """A minimal valid row."""
    base: dict[str, Any] = {
        "model_id": "ollama/m",
        "model_label": "M",
        "dataset_id": "probes",
        "dataset_kind": "probes",
        "dataset_version": "0.1",
        "dataset_source": "builtin",
        "config_name": "probes",
        "headline_score": 50.0,
        "metrics": {},
        "latency_ms_total": 1,
        "latency_ms_per_chunk_p50": 1,
        "input_tokens": 1,
        "output_tokens": 1,
        "cost_usd": 0.0,
        "success": True,
        "error": None,
        "timestamp": datetime(2026, 9, 25, tzinfo=UTC),
        "benchmark_version": BENCHMARK_VERSION,
        "scorer_version": 1,
        "seed": 42,
        "temperature": 0.0,
        "thinking": False,
        "thinking_honoured": None,
        "chunks_truncated": 0,
        "chunks_aborted_by_loop": 0,
    }
    base.update(kw)
    return BenchmarkResult(**base)


def test_harness_track_row_round_trips(tmp_path: Path) -> None:
    """pins_applied, harness and unpinned temperature/thinking survive a dump/load."""
    path = tmp_path / "r.json"
    row = _row(
        pins_applied=False,
        harness="mcp:cursor",
        harness_settings={"effort": "high", "thinking": True, "budget": 8000},
        temperature=None,
        thinking=None,
    )
    dump_results([row], path)
    assert load_results(path) == [row]
    assert path.read_text().endswith("}\n")  # committed as written: no end-of-file fix


def test_legacy_rows_load_as_pinned(tmp_path: Path) -> None:
    """Files written before the harness track load with pins applied, no harness."""
    path = tmp_path / "old.json"
    dump_results([_row()], path)
    payload = json.loads(path.read_text())
    for r in payload["results"]:
        del r["pins_applied"], r["harness"], r["harness_settings"], r["app_version"]
    path.write_text(json.dumps(payload))
    (row,) = load_results(path)
    assert row.pins_applied is True
    assert row.harness is None
    assert row.harness_settings is None
    # An unrecorded build stays unknown, not stamped with the reader's build.
    assert row.app_version is None


def test_new_rows_record_the_app_build(tmp_path: Path) -> None:
    """A row is stamped with the build that made it, and the stamp round-trips."""
    row = _row()
    assert row.app_version == current_app_version()
    path = tmp_path / "r.json"
    dump_results([row], path)
    assert load_results(path)[0].app_version == row.app_version


@pytest.mark.parametrize(("status", "suffix"), [("", ""), (" M results.py", ".dirty")])
def test_app_version_in_a_checkout_names_the_commit(
    monkeypatch: Any, status: str, suffix: str
) -> None:
    """From a source checkout the stamp is version+g<sha>, marked .dirty when edited."""
    import shutil
    import subprocess

    from chaoscypher_core import __version__

    def fake_git(argv: list[str], **_k: Any) -> subprocess.CompletedProcess[str]:
        out = "abc123def\n" if "rev-parse" in argv else status
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(subprocess, "run", fake_git)
    current_app_version.cache_clear()
    try:
        assert current_app_version() == f"{__version__}+gabc123def{suffix}"
    finally:
        current_app_version.cache_clear()


def test_app_version_falls_back_to_package_version_without_git(monkeypatch: Any) -> None:
    """An installed wheel (no git, or not a checkout) records the package version alone."""
    import subprocess

    from chaoscypher_core import __version__

    def no_git(*_a: Any, **_k: Any) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", no_git)
    current_app_version.cache_clear()
    try:
        assert current_app_version() == __version__
    finally:
        current_app_version.cache_clear()


def test_harness_settings_render_effort_and_thinking_first() -> None:
    """Effort and thinking lead, the rest follow A-Z; booleans read on/off."""
    assert format_harness_settings(None) == []
    assert format_harness_settings({"version": "2.3", "thinking": False, "effort": "high"}) == [
        "effort high",
        "thinking off",
        "version 2.3",
    ]


def test_a_row_takes_the_scorer_recipe_from_its_metrics(tmp_path: Path) -> None:
    """Next to scorer_version, every row built from a scorer's metrics carries its recipe (H3)."""
    row = _row(metrics={"scorer_recipe": "recipe-a", "verdicts": []})
    assert row.scorer_recipe == "recipe-a"
    assert _row(metrics={"scorer_recipe": "x"}, scorer_recipe="given").scorer_recipe == "given"
    assert _row(metrics={}).scorer_recipe is None  # an errored row, or a scorer with none
    path = tmp_path / "r.json"
    dump_results([row], path)
    assert load_results(path)[0].scorer_recipe == "recipe-a"


def test_rows_written_before_the_recipe_load_without_one(tmp_path: Path) -> None:
    """A file from before 2026-10-06 has no scorer_recipe key; it loads as None, not as today's."""
    path = tmp_path / "old.json"
    dump_results([_row()], path)
    payload = json.loads(path.read_text())
    for r in payload["results"]:
        del r["scorer_recipe"]
    path.write_text(json.dumps(payload))
    assert load_results(path)[0].scorer_recipe is None


def test_row_problems_name_a_timeout_a_failure_and_errored_questions() -> None:
    """p2-lean-3090's qwen3:14b row held 120 connection errors and pooled as finished (H8)."""
    from chaoscypher_core.benchmark.results import row_problems

    assert row_problems(_row()) == []
    assert row_problems(
        _row(success=False, error="TimeoutError: ", extras={"timed_out": True})
    ) == ["timed out"]
    assert row_problems(_row(success=False, error="TimeoutError")) == ["timed out"]  # pre-flag
    assert row_problems(_row(success=False, error="PinError: no pins")) == [
        "failed (PinError: no pins)"
    ]
    errored = _row(
        extras={"per_query": [{"query_id": "q1"}, {"query_id": "q2", "error": "refused"}]}
    )
    assert row_problems(errored) == ["1 question(s) errored (first q2: refused)"]


def test_context_overflows_name_the_questions_past_the_window() -> None:
    from chaoscypher_core.benchmark.results import context_overflows

    per_query = [
        {"query_id": "q1", "prompt_tokens": 3000, "output_tokens": 30000},
        {"query_id": "q2", "prompt_tokens": 3000, "output_tokens": 1000},
        {"query_id": "q3", "prompt_tokens": 100, "output_tokens": 9000, "num_ctx": 8192},
        {"query_id": "q4", "output_tokens": 99999},  # no prompt count: not judged
    ]
    row = _row(extras={"per_query": per_query, "run_settings": {"num_ctx": 32768}})
    assert context_overflows(row) == ["q1", "q3"]


# --- comparability, audit wave 4 ---------------------------------------------------------

_RUN = {"run_settings": {"levers": {"chunk_limit": 10}, "num_ctx": 32768}}


def _chat(model: str, *, seed: int = 42, **extras: Any) -> BenchmarkResult:
    """A pinned chat row of ``model`` at ``seed`` with ``extras`` over the run settings."""
    return _row(
        model_label=model,
        dataset_id="book__chat__x",
        dataset_kind="chat",
        seed=seed,
        extras={**_RUN, **extras},
    )


def test_the_seed_check_is_per_model_not_per_label() -> None:
    """A's variant at 43 is refused when only B's baseline ran 43 (pooled per label it passed)."""
    from chaoscypher_core.benchmark.results import comparability_findings

    rows = {
        "base": [_chat("A", seed=42), _chat("B", seed=43)],
        "variant": [_chat("A", seed=43), _chat("B", seed=43)],
    }
    findings = comparability_findings(rows, baseline="base")
    assert [f for f in findings if f.startswith("seed")] == [
        "seed: variant's A ran at seed 43, which the baseline base never ran it at (it ran ['42'])"
    ]
    rows["base"].append(_chat("A", seed=43))
    assert not [f for f in comparability_findings(rows, baseline="base") if f.startswith("seed")]


def test_a_pull_between_baseline_and_variant_is_two_measurements() -> None:
    """The model build is compared per model: another digest for one model is refused."""
    from chaoscypher_core.benchmark.results import comparability_findings

    build = {"digest": "sha256:aaa", "server_version": "0.12.3", "parameters": {"top_k": 40}}
    other_model = {**build, "digest": "sha256:zzz"}
    rows = {
        "base": [_chat("A", model_build=build), _chat("B", model_build=other_model)],
        "variant": [_chat("A", model_build=build), _chat("B", model_build=other_model)],
    }
    assert comparability_findings(rows, baseline="base") == []  # two models, two digests: fine
    rows["variant"][0] = _chat("A", model_build={**build, "digest": "sha256:bbb"})
    findings = comparability_findings(rows, baseline="base")
    assert len(findings) == 1 and findings[0].startswith(
        "model_build: A ran with 2 values of digest"
    )
    rows["variant"][0] = _chat("A", model_build={**build, "parameters": {"top_k": 20}})
    assert "parameters" in comparability_findings(rows, baseline="base")[0]
    # audit wave 5: unknown is a value, not a match (a failed fetch, or a row before the field)
    rows["variant"][0] = _chat("A", model_build=None)
    findings = comparability_findings(rows, baseline="base")
    assert findings and all(f.startswith("model_build: A ran with 2 values of") for f in findings)
    assert "unrecorded (variant)" in findings[0]
    rows["variant"][0] = _chat("A", model_build={**build, "digest": None})
    findings = comparability_findings(rows, baseline="base")
    assert findings == [
        "model_build: A ran with 2 values of digest (sha256:aaa (base); unrecorded (variant)): "
        "a pull, a server change or an unrecorded build between the runs"
    ]


def test_a_model_no_row_records_a_build_for_has_nothing_to_compare() -> None:
    """A cloud model's rows record no build: no finding (within a model, recorded by one is required of all)."""
    from chaoscypher_core.benchmark.results import comparability_findings

    rows = {"base": [_chat("cloud", model_build=None)], "variant": [_chat("cloud")]}
    assert comparability_findings(rows, baseline="base") == []


@pytest.mark.parametrize(
    "field", ["queries_sha256", "scorer_recipe", "rescored_queries_sha256", "judge_prompts_sha256"]
)
def test_a_field_recorded_on_one_side_only_is_a_finding(field: str) -> None:
    """Audit wave 5: these were compared only between rows that recorded them, so a row that
    recorded none matched every row.
    """
    from dataclasses import replace

    from chaoscypher_core.benchmark.results import comparability_findings

    if field == "scorer_recipe":
        recorded, bare = replace(_chat("A"), scorer_recipe="r1"), _chat("A")
    else:
        recorded, bare = _chat("A", **{field: "x" * 64}), _chat("A")
    assert comparability_findings({"base": [bare], "variant": [bare]}, baseline="base") == []
    findings = comparability_findings({"base": [recorded], "variant": [bare]}, baseline="base")
    value = "r1" if field == "scorer_recipe" else "x" * 64
    assert len(findings) == 1 and findings[0].startswith(f"{field}: ")
    assert f"{value} (base)" in findings[0] and "unrecorded (variant)" in findings[0]


def test_unpinned_rows_compare_the_graph_they_read() -> None:
    """Audit wave 5: two unpinned rows read from different graphs gave no finding.

    The auditor's case: graph_cache_key, recipe, resolution, adopted_from and domain all differ.
    """
    from chaoscypher_core.benchmark.results import (
        GRAPH_IDENTITY_FIELDS,
        PINS_EXEMPTION,
        comparability_findings,
        graph_varies_finding,
    )

    def unpinned(**run: Any) -> BenchmarkResult:
        settings = {**_RUN["run_settings"], "graph_cache_key": "k", "extraction_recipe": "A"}
        return _chat("A", run_settings={**settings, **run})

    a = unpinned()
    b = unpinned(
        graph_cache_key="OTHER",
        extraction_recipe="B",
        resolution="R",
        graph_adopted_from="v3",
        extraction_domain="generic",
    )
    findings = comparability_findings({"base": [a], "var": [b]}, baseline="base")
    assert {f.split(":")[0] for f in findings} == {
        "extraction_domain",
        "resolution",
        "extraction_recipe",
    }
    sha_a = unpinned(graph_snapshot_sha256="1" * 64)
    sha_b = unpinned(graph_snapshot_sha256="2" * 64)
    assert [f.split(":")[0] for f in comparability_findings({"b": [sha_a], "v": [sha_b]})] == [
        "graph_sha256"
    ]
    # a pinned row's graph sha256 comes from its pins when the run settings lack it
    pinned = _chat("A", retrieval_pins={"graph_sha256": "1" * 64})
    assert "graph_sha256" not in " ".join(
        comparability_findings({"b": [pinned], "v": [replace_run(pinned, "1" * 64)]})
    )
    exempt = GRAPH_IDENTITY_FIELDS | {PINS_EXEMPTION}
    assert comparability_findings({"base": [a], "var": [b]}, baseline="base", exempt=exempt) == []
    note = graph_varies_finding({"base": [a], "var": [b]})
    assert note.startswith("graph: --graph-varies declared") and "extraction_domain" in note
    assert "the rows read one graph" in graph_varies_finding({"base": [a], "var": [a]})


def replace_run(row: BenchmarkResult, sha: str) -> BenchmarkResult:
    """``row`` with ``graph_snapshot_sha256`` recorded in its run settings."""
    from dataclasses import replace

    run = {**row.extras["run_settings"], "graph_snapshot_sha256": sha}
    return replace(row, extras={**row.extras, "run_settings": run})


def test_a_timeout_is_recognised_by_its_httpx_names_too() -> None:
    """Audit wave 5: httpx's ConnectTimeout/ReadTimeout are not TimeoutError subclasses."""
    import httpx

    from chaoscypher_core.benchmark.results import is_timeout_error, row_timed_out

    assert is_timeout_error(TimeoutError()) and is_timeout_error(httpx.ReadTimeout("slow"))
    assert is_timeout_error(httpx.ConnectTimeout("no route"))
    assert not is_timeout_error(httpx.ConnectError("refused"))
    for error in ("ReadTimeout: ", "ConnectTimeout: x", "LLMServiceError: request timed out"):
        assert row_timed_out(_row(success=False, error=error)), error
    assert not row_timed_out(_row(success=False, error="ConnectError: refused"))


def test_what_scored_the_verdicts_is_a_comparability_field() -> None:
    """Recipe, rescored fixture content and the judge (model and prompts) must be one value."""
    from chaoscypher_core.benchmark.results import COMPARABILITY_FIELDS, comparability_findings

    assert {
        "scorer_recipe",
        "rescored_queries_sha256",
        "judge_model",
        "judge_prompts_sha256",
    } <= set(COMPARABILITY_FIELDS)
    a, b = _chat("A"), _chat("A")
    a.scorer_recipe, b.scorer_recipe = "r1", "r2"
    assert any(f.startswith("scorer_recipe") for f in comparability_findings({"x": [a, b]}))
    b.scorer_recipe = None  # audit wave 5: unrecorded on one side is a finding, not a match
    assert comparability_findings({"x": [a, b]}) == ["scorer_recipe: r1 (x); unrecorded (x)"]
    rescored = {
        "x": [_chat("A", rescored_queries_sha256="1"), _chat("A", rescored_queries_sha256="2")]
    }
    assert comparability_findings(rescored)[0].startswith("rescored_queries_sha256")
    judged = {"x": [_chat("A", judge_model="j1"), _chat("A", judge_model="j2")]}
    assert comparability_findings(judged)[0].startswith("judge_model")
    prompts = {"x": [_chat("A", judge_prompts_sha256="p"), _chat("A", judge_prompts_sha256="q")]}
    assert comparability_findings(prompts)[0].startswith("judge_prompts_sha256")


def test_rung_specific_fields_are_exempt_across_rungs_and_pins_are_per_rung() -> None:
    """Across the three rungs only the rung, its dataset id and its pins may differ."""
    from chaoscypher_core.benchmark.results import RUNG_SPECIFIC_FIELDS, comparability_findings

    graphrag = _chat("A", retrieval="graphrag", retrieval_pins={"file": "g.json"})
    naive = _chat("A", retrieval="naive", retrieval_pins={"file": "n.json"})
    naive.dataset_id = "book__chat__x__retrieval_naive"
    rungs = {"graphrag": [graphrag], "naive": [naive]}
    assert len(comparability_findings(rungs)) == 2  # retrieval and dataset_id, never the pins
    assert comparability_findings(rungs, exempt=RUNG_SPECIFIC_FIELDS) == []
    naive.scorer_recipe = "another-scorer"
    assert comparability_findings(rungs, exempt=RUNG_SPECIFIC_FIELDS)[0].startswith("scorer_recipe")


def test_a_timed_out_row_is_told_apart_from_an_errored_one() -> None:
    """``row_timed_out`` is the one test; ``row_problems`` names it first."""
    from chaoscypher_core.benchmark.results import row_problems, row_timed_out

    flagged = _chat("A", timed_out=True)
    legacy = _row(success=False, error="TimeoutError: model exceeded 5400s")
    errored = _chat("A", per_query=[{"query_id": "q1", "error": "LLMServiceError"}])
    assert row_timed_out(flagged) and row_timed_out(legacy) and not row_timed_out(errored)
    assert row_problems(flagged)[0] == "timed out"
    assert row_problems(errored)[0].startswith("1 question(s) errored")


def test_the_same_fixture_in_another_checkout_is_the_datasets_own_question_file(
    tmp_path: Path,
) -> None:
    """Identity by content: a worktree's copy of the fixture is not a second question file."""
    from chaoscypher_core.benchmark.results import resolve_queries_file

    own = tmp_path / "main" / "war_and_peace_book1" / "queries.yaml"
    other = tmp_path / "worktree" / "data" / "x" / "queries.yaml"
    own.parent.mkdir(parents=True), other.parent.mkdir(parents=True)
    own.write_text("version: '1.0'\nqueries: []\n", encoding="utf-8")
    other.write_text("version: '1.0'\nqueries: []\n", encoding="utf-8")
    assert resolve_queries_file(str(other), own) is None
    other.write_text("version: '1.0'\nqueries: [{id: h1}]\n", encoding="utf-8")  # a holdout set
    assert resolve_queries_file(str(other), own) == other.resolve()


def test_rows_recording_two_copies_of_one_holdout_file_are_one_question_file(
    tmp_path: Path,
) -> None:
    """Two checkouts' copies of a holdout set (neither the dataset's own) group as one file."""
    from chaoscypher_core.benchmark.results import common_queries_file

    own = tmp_path / "own" / "queries.yaml"
    own.parent.mkdir()
    own.write_text("version: '1.0'\nqueries: []\n", encoding="utf-8")
    a = tmp_path / "a" / "holdout.yaml"
    b = tmp_path / "b" / "holdout.yaml"
    a.parent.mkdir(), b.parent.mkdir()
    for f in (a, b):
        f.write_text("version: '1.0'\nqueries: [{id: h1}]\n", encoding="utf-8")
    rows = [_chat("A", queries_file=str(a)), _chat("B", queries_file=str(b))]
    assert common_queries_file(rows, lambda _cid: own) == a.resolve()


def test_app_version_is_captured_when_the_module_loads() -> None:
    """The stamp is the tree as it was when the run's code loaded, not when its first row landed.

    A lazy first call recorded the commit made minutes into a run that started on those very
    changes uncommitted (2026-10-15 provenance audit). A fresh interpreter proves the import fills
    the cache: no other call has run yet.
    """
    import subprocess
    import sys

    probe = (
        "from chaoscypher_core.benchmark import results as r; "
        "print(r.current_app_version.cache_info().currsize)"
    )
    out = subprocess.run(  # noqa: S603 - the test's own interpreter and code
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout
    assert out.strip().splitlines()[-1] == "1"
