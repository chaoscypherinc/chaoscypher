# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""scripts/benchmark/export_leaderboard.py: harness rows apart, grounded-chat board."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from chaoscypher_cli.benchmark.discovery import builtin_dataset_root
from chaoscypher_core.benchmark.queries import LabeledQuery, load_queries
from chaoscypher_core.testing.docs_modules import judge_boards
from chaoscypher_core.testing.repo_files import repo_file_or_skip, repo_root


SCRIPT = Path(__file__).resolve().parents[5] / "scripts" / "benchmark" / "export_leaderboard.py"


def _load_script() -> Any:
    """Import the script as a module without running main().

    The board scores every chat row itself (audit wave 4); the rows below ask
    the real Book One questions and five invented ones (``q1``-``q5``), so the
    board's question set is the fixture plus :data:`SYNTHETIC`.
    """
    spec = importlib.util.spec_from_file_location("export_leaderboard_script", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    real = module._fixture_queries(FIXTURE)
    with_synthetic = replace(real, queries=[*real.queries, *SYNTHETIC])
    module._board_queries = lambda _row: with_synthetic
    return module


def _row(
    model_id: str,
    label: str,
    *,
    harness: str | None,
    harness_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One saved probe row with a pass and a fail in each suite's sections."""
    verdicts = [
        {"id": "A1-easy", "section": "A", "passed": True},
        {"id": "A1-hard", "section": "A", "passed": False},
        {"id": "H1-easy", "section": "H", "passed": True},
    ]
    return {
        "model_id": model_id,
        "model_label": label,
        "dataset_id": "probes",
        "dataset_kind": "probes",
        "dataset_version": "0.1",
        "dataset_source": "builtin",
        "config_name": "probes-all",
        "headline_score": 66.7,
        "metrics": {"verdicts": verdicts},
        "latency_ms_total": 0 if harness else 60000,
        "latency_ms_per_chunk_p50": 0 if harness else 2000,
        "input_tokens": 10,
        "output_tokens": 10,
        "cost_usd": 0.0,
        "success": True,
        "error": None,
        "timestamp": "2026-09-25T00:00:00+00:00",
        "benchmark_version": "2.0",
        "scorer_version": 1,
        "seed": 42,
        "temperature": None if harness else 0.0,
        "thinking": None,
        "thinking_honoured": None,
        "chunks_truncated": 0,
        "chunks_aborted_by_loop": 0,
        "extras": {},
        "pins_applied": harness is None,
        "harness": harness,
        "harness_settings": harness_settings,
    }


def test_harness_rows_reach_the_page_but_not_the_interface_picker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A harness model is on the page with its fields and flags, and absent from the picker file."""
    script = _load_script()
    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "results": [
                    _row("ollama/qwen3:14b", "Qwen3 14B (local)", harness=None),
                    _row(
                        "mcp/claude-code/m",
                        "M via Claude Code",
                        harness="mcp:claude-code",
                        harness_settings={"thinking": "adaptive", "effort": "high"},
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )
    page, interface = tmp_path / "page.json", tmp_path / "interface.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_leaderboard.py",
            "--native",
            str(results),
            "--carrier",
            str(results),
            "--out",
            str(page),
            "--interface-out",
            str(interface),
        ],
    )

    script.main()

    by_id = {m["id"]: m for m in json.loads(page.read_text(encoding="utf-8"))["models"]}
    harness = by_id["mcp/claude-code/m"]
    assert harness["harness"] == "mcp:claude-code"
    assert harness["pins_applied"] is False
    assert harness["label"] == "M via Claude Code"
    assert harness["vram_gb"] is None
    assert harness["notes"] == [
        "harness: mcp:claude-code",
        "effort high",
        "thinking adaptive",
        "pins not applied",
    ]
    assert harness["harness_settings"] == {"thinking": "adaptive", "effort": "high"}
    pinned = by_id["ollama/qwen3:14b"]
    assert pinned["harness"] is None
    assert pinned["pins_applied"] is True
    assert pinned["notes"] == []
    assert pinned["harness_settings"] is None

    picker_ids = [m["id"] for m in json.loads(interface.read_text(encoding="utf-8"))["models"]]
    assert picker_ids == ["qwen3:14b"]


FIXTURE = "war_and_peace_book1"
PINS = {"file": "graphrag-f.json", "fingerprint": "f" * 16, "graph_sha256": "g" * 64}
"""What a pinned chat row records about its pins (the exporter refuses a rung without them)."""

RUN_SETTINGS = {"levers": {"chunk_limit": 10}, "num_ctx": 32768, "ai_max_tokens": 30000}
"""What a chat row records about its run (a row without it ran at 0.3, audit H1, and is refused)."""


SYNTHETIC = [
    LabeledQuery(
        id=f"q{i}",
        band="multi_hop",
        question=f"Invented question {i}: who is here?",
        gold_entities=["Mary"],
        gold_answer="Mary.",
        answer_terms=["Mary"],
    )
    for i in range(1, 6)
]
"""Five invented questions the chat rows answer beside the real fixture's."""

_SYNTHETIC_CONTEXT = "[CHUNK C0 | x.txt]\n[S1] Mary is here."


def _record(qid: str, *, right: bool, unsupported: bool = False) -> dict[str, Any]:
    """A per-query record whose answer the scorer passes (``right``) or fails.

    A real Book One question answers with :data:`_TERMS` over :data:`_CONTEXT`;
    an invented one with 'Mary.' (right), 'it is not said.' (no answer term)
    or 'Mary, with Kutuzov.' (``unsupported``: a name not in the context).
    """
    if qid in _TERMS:
        answer, context = (_TERMS[qid] if right else "That is not in the sources."), _CONTEXT
    else:
        answer = "Mary, with Kutuzov." if unsupported else ("Mary." if right else "it is not said.")
        context = _SYNTHETIC_CONTEXT
    return {
        "query_id": qid,
        "band": "multi_hop",
        "finish_reason": "stop",
        "output_tokens": 5,
        "context_text": context,
        "answer": answer,
    }


def _chat_row(
    model_id: str,
    failed: list[str],
    *,
    success: bool = True,
    error: str | None = None,
    unsupported: tuple[str, ...] = (),
    ids: tuple[str, ...] = ("q1", "q2", "q3", "q4", "q5"),
) -> dict[str, Any]:
    """One grounded-chat row over ``ids``; ``failed`` ids fail, ``unsupported`` ones name an invented name.

    The stored verdicts are the old file shape only: the board scores the
    answers in ``per_query`` itself (audit wave 4).
    """
    verdicts = []
    for qid in ids:
        details = ["PASS finish_stop: finish_reason=stop"]
        if qid in unsupported:
            details.append(
                "FAIL no_unsupported_names: names not in the retrieved context=['Kutuzov']"
            )
        elif qid in failed:
            details.append("FAIL answers_from_graph: answer terms missing=['Mary']")
        verdicts.append(
            {
                "id": qid,
                "probe": qid,
                "section": "multi_hop",
                "tier": "medium",
                "passed": qid not in failed,
                "details": details,
            }
        )
    row = _row(model_id, f"{model_id} (local)", harness=None)
    row.update(
        {
            "dataset_id": f"{FIXTURE}__chat__ollama_gemma4:31b__ollama_qwen3-embedding:0.6b",
            "dataset_kind": "chat",
            "config_name": "chat",
            "metrics": {}
            if not success
            else {
                "chat_probe_scorer_version": 1,
                "probes_total": len(ids),
                "probes_passed": len(ids) - len(failed),
                "section_rates": {},
                "verdicts": verdicts,
            },
            "latency_ms_total": 180000,
            "chunks_truncated": 2,
            "thinking": True,
            "thinking_honoured": True,
            "success": success,
            "error": error,
            "extras": {
                "retrieval_pins": dict(PINS),
                "run_settings": dict(RUN_SETTINGS),
                "per_query": [
                    _record(qid, right=qid not in failed, unsupported=qid in unsupported)
                    for qid in ids
                ]
                if success
                else [],
            },
        }
    )
    return row


def _write(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps({"schema_version": 1, "results": rows}), encoding="utf-8")
    return path


def _export(
    script: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *extra: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run main() on the probe fixture plus ``extra`` args; return (page, interface)."""
    probes = _write(
        tmp_path / "probes.json", [_row("ollama/qwen3:14b", "Qwen3 14B (local)", harness=None)]
    )
    page, interface = tmp_path / "page.json", tmp_path / "interface.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_leaderboard.py",
            "--native",
            str(probes),
            "--carrier",
            str(probes),
            "--out",
            str(page),
            "--interface-out",
            str(interface),
            *extra,
        ],
    )
    script.main()
    return (
        json.loads(page.read_text(encoding="utf-8")),
        json.loads(interface.read_text(encoding="utf-8")),
    )


def test_chat_row_becomes_the_models_chat_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pass counts, minutes, truncation and unsupported names come from the chat row."""
    script = _load_script()
    chat = _write(
        tmp_path / "chat.json",
        [
            # The reference graph's own rows ride along in a chat result file.
            {**_row("ollama/gemma4:31b", "Gemma", harness=None), "dataset_kind": "extraction"},
            _chat_row("ollama/qwen3:14b", ["q2", "q5"], unsupported=("q5",)),
        ],
    )

    page, interface = _export(script, monkeypatch, tmp_path, "--chat", str(chat))

    assert [m["id"] for m in page["models"]] == ["ollama/qwen3:14b"]
    assert page["models"][0]["chat"] == {
        "passed": 3,
        "total": 5,
        "pct": 60.0,
        "runs": 1,
        "minutes": 3.0,
        "truncated": 2,
        "timed_out": False,
        "thinking": True,
        "unsupported_names": 1,
        # cited-correctly over cited; None on a row whose verdicts carry no citation state
        "citation_accuracy": None,
        "extra_sessions": 0,
        "retried_tasks": 0,
        # questions whose prompt plus answer overflowed the window (none recorded here)
        "context_overflows": 0,
        # one verdict per question: the fraction of runs that passed it
        "verdicts": {"q1": 1.0, "q2": 0.0, "q3": 1.0, "q4": 1.0, "q5": 0.0},
    }
    suite = page["suites"]["chat"]
    assert (suite["total"], suite["fixture"], suite["retrieval_floor"]) == (5, FIXTURE, None)
    assert suite["retrieval_floor_ids"] == []
    # The fixture's questions travel with the board: id, section, tier, text and
    # whether the audit left it scorable; never answer terms.
    assert len(suite["questions"]) == 80
    assert set(suite["questions"][0]) == {"id", "section", "tier", "question", "answerable"}
    assert not any("answer_terms" in q or "must_not_contain" in q for q in suite["questions"])
    assert page["thinking"] == "off"
    assert page["chat_thinking"] == "on"
    picker = interface["models"][0]
    assert picker["chat_pct"] == 60.0
    assert picker["chat"] == {"passed": 3, "total": 5, "pct": 60.0, "timed_out": False}


def test_timed_out_chat_row_is_marked_not_scored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model that hit the wall clock shows as did-not-finish, with the board's size."""
    script = _load_script()
    chat = _write(
        tmp_path / "chat.json",
        [
            _chat_row(
                "ollama/qwen3:14b", [], success=False, error="TimeoutError: model exceeded 5400s"
            ),
            _chat_row("ollama/gemma4:31b", ["q1"]),
        ],
    )

    page, interface = _export(
        script, monkeypatch, tmp_path, "--chat", str(chat), "--include-errored"
    )

    by_id = {m["id"]: m for m in page["models"]}
    timed_out = by_id["ollama/qwen3:14b"]["chat"]
    assert timed_out["timed_out"] is True
    assert timed_out["passed"] is None
    assert timed_out["pct"] is None
    assert timed_out["unsupported_names"] is None
    assert timed_out["total"] == 5
    assert page["suites"]["chat"]["total"] == 5
    # a chat-only model still gets a row, with no probe suites
    assert "native" not in by_id["ollama/gemma4:31b"]
    assert by_id["ollama/gemma4:31b"]["chat"]["passed"] == 4
    assert {m["id"]: m["chat_pct"] for m in interface["models"]} == {
        "gemma4:31b": 80.0,
        "qwen3:14b": None,
    }


def test_retrieval_floor_counts_questions_every_model_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """q2 and q4 fail for both scored models; two rows for one model pool as repeats."""
    script = _load_script()
    first = _write(
        tmp_path / "first.json", [_chat_row("ollama/qwen3:14b", ["q1", "q2", "q3", "q4", "q5"])]
    )
    repeat = _chat_row("ollama/qwen3:14b", ["q2", "q4", "q5"])
    repeat["timestamp"] = "2026-09-26T00:00:00+00:00"  # one run is one (model, rung, timestamp)
    second = _write(
        tmp_path / "second.json",
        [
            repeat,
            _chat_row("ollama/gemma4:31b", ["q1", "q2", "q4"]),
            # a timed-out model has no verdicts and does not lower the floor
            _chat_row("ollama/slow:70b", [], success=False, error="TimeoutError"),
        ],
    )

    page, _ = _export(
        script, monkeypatch, tmp_path, "--chat", str(first), str(second), "--include-errored"
    )

    assert page["suites"]["chat"]["retrieval_floor"] == 2
    assert page["suites"]["chat"]["retrieval_floor_ids"] == ["q2", "q4"]
    by_id = {m["id"]: m for m in page["models"]}
    # qwen3 ran twice: q1 and q3 passed once each, so 1.0 of 5 over 2 runs
    qwen = by_id["ollama/qwen3:14b"]["chat"]
    assert qwen["runs"] == 2 and qwen["passed"] == 1.0
    assert qwen["verdicts"] == {"q1": 0.5, "q2": 0.0, "q3": 0.5, "q4": 0.0, "q5": 0.0}


def test_no_chat_argument_leaves_the_export_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --chat no model gets a chat object and suites keep their two keys."""
    script = _load_script()

    page, interface = _export(script, monkeypatch, tmp_path)

    assert set(page["suites"]) == {"native", "carrier"}
    assert all("chat" not in m for m in page["models"])
    assert page["thinking"] == "off"
    assert interface["models"][0]["chat_pct"] is None
    assert interface["models"][0]["chat"] is None


def test_tool_support_reaches_both_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A picker must know which models the app's tool-calling chat loop can use."""
    script = _load_script()
    chat = _write(
        tmp_path / "chat.json",
        [
            _chat_row("ollama/qwen3:14b", ["q1"]),
            _chat_row("ollama/phi4:14b", ["q1"]),
            _chat_row("ollama/not-in-registry:1b", ["q1"]),
        ],
    )

    page, interface = _export(script, monkeypatch, tmp_path, "--chat", str(chat))

    assert {m["id"]: m["tools"] for m in page["models"]} == {
        "ollama/qwen3:14b": True,
        "ollama/phi4:14b": False,
        "ollama/not-in-registry:1b": None,
    }
    assert {m["id"]: m["tools"] for m in interface["models"]} == {
        "qwen3:14b": True,
        "phi4:14b": False,
        "not-in-registry:1b": None,
    }


def test_blended_scores_are_written_once_for_every_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Extraction (chunks twice, isolated once), chat and overall reach both files; None where a board is missing."""
    script = _load_script()
    chat = _write(
        tmp_path / "chat.json",
        [
            _chat_row(
                "ollama/qwen3:14b", ["q1"]
            ),  # 4/5 chat; probe fixture is 1/2 native, 1/1 carrier
            _chat_row("ollama/gemma4:31b", ["q1"]),  # chat only
            _chat_row(
                "ollama/phi4:14b", [], success=False, error="TimeoutError: model exceeded 5400s"
            ),
        ],
    )

    page, interface = _export(
        script, monkeypatch, tmp_path, "--chat", str(chat), "--include-errored"
    )

    by_id = {m["id"]: m["scores"] for m in page["models"]}
    blends = ("extraction", "chat", "overall")

    def blend(scores: dict[str, Any]) -> dict[str, Any]:
        """The three blended numbers; the intervals beside them are checked apart."""
        return {k: scores[k] for k in blends}

    # extraction = 2/3 * 100 + 1/3 * 50 = 83.3; overall = 0.6 * 83.3 + 0.4 * 80 = 82.0
    assert blend(by_id["ollama/qwen3:14b"]) == {"extraction": 83.3, "chat": 80.0, "overall": 82.0}
    assert blend(by_id["ollama/gemma4:31b"]) == {"extraction": None, "chat": 80.0, "overall": None}
    # a chat run that did not finish scores 0 there, so it still gets an overall
    assert blend(by_id["ollama/phi4:14b"]) == {"extraction": None, "chat": 0.0, "overall": None}
    assert set(by_id["ollama/qwen3:14b"]) == {
        *blends,
        "extraction_ci90",
        "chat_ci90",
        "overall_ci90",
    }
    assert by_id["ollama/gemma4:31b"]["overall_ci90"] is None
    assert page["score_weights"] == {
        "extraction": {"carrier": 2 / 3, "native": 1 / 3},
        "overall": {"extraction": 0.6, "chat": 0.4},
    }
    picker = {m["id"]: m["scores"] for m in interface["models"]}
    assert blend(picker["qwen3:14b"]) == {"extraction": 83.3, "chat": 80.0, "overall": 82.0}
    assert interface["score_weights"] == page["score_weights"]


def test_harness_chat_row_joins_the_models_probe_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An MCP client's chat run lands on its probe row, so the model gets an Overall."""
    script = _load_script()
    mid = "mcp/claude-code/claude-sonnet-5"
    settings = {"effort": "high", "thinking": "adaptive"}
    probes = _write(
        tmp_path / "harness-probes.json",
        [
            _row("ollama/qwen3:14b", "Qwen3 14B (local)", harness=None),
            _row(
                mid, "Sonnet via Claude Code", harness="mcp:claude-code", harness_settings=settings
            ),
        ],
    )
    chat_row = _chat_row(mid, ["q2"])
    chat_row.update(
        {
            "model_label": "Sonnet via Claude Code",
            "latency_ms_total": 0,
            "latency_ms_per_chunk_p50": 0,
            "temperature": None,
            "thinking": None,
            "thinking_honoured": None,
            "seed": None,
            "chunks_truncated": 0,
            "pins_applied": False,
            "harness": "mcp:claude-code",
            "harness_settings": settings,
        }
    )
    chat = _write(tmp_path / "chat.json", [_chat_row("ollama/qwen3:14b", ["q1"]), chat_row])
    page, interface = tmp_path / "page.json", tmp_path / "interface.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_leaderboard.py",
            "--native",
            str(probes),
            "--carrier",
            str(probes),
            "--chat",
            str(chat),
            "--out",
            str(page),
            "--interface-out",
            str(interface),
        ],
    )
    script.main()

    data = json.loads(page.read_text(encoding="utf-8"))
    by_id = {m["id"]: m for m in data["models"]}
    assert len([m for m in data["models"] if m["id"] == mid]) == 1
    model = by_id[mid]
    assert model["harness"] == "mcp:claude-code"
    assert model["pins_applied"] is False
    assert model["harness_settings"] == settings
    assert model["label"] == "Sonnet via Claude Code"
    assert model["native"]["passed"] == 1 and model["chat"]["passed"] == 4
    assert model["chat"]["thinking"] is None
    assert model["scores"]["chat"] == 80.0 and model["scores"]["overall"] is not None
    # Still kept out of the local-model picker.
    picker_ids = [m["id"] for m in json.loads(interface.read_text(encoding="utf-8"))["models"]]
    assert mid not in picker_ids


def _probe_row(model_id: str, verdicts: dict[str, bool], *, section: str = "A") -> dict[str, Any]:
    """A probe row with the given verdicts in one section."""
    row = _row(model_id, f"{model_id} (local)", harness=None)
    row["metrics"] = {
        "verdicts": [{"id": k, "section": section, "passed": v} for k, v in verdicts.items()]
    }
    return row


def _export_files(
    script: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    native: list[Path],
    carrier: list[Path],
    chat: list[Path] = (),
) -> dict[str, Any]:
    """Run main() over explicit suite files; return the page data."""
    page = tmp_path / "page.json"
    argv = ["export_leaderboard.py", "--native", *map(str, native), "--carrier", *map(str, carrier)]
    if chat:
        argv += ["--chat", *map(str, chat)]
    monkeypatch.setattr(sys, "argv", [*argv, "--out", str(page)])
    script.main()
    return json.loads(page.read_text(encoding="utf-8"))


def test_two_rows_for_one_model_pool_as_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeats pool per task: the mean pass count, the run count, and one fraction per task."""
    script = _load_script()
    first = _write(
        tmp_path / "r1.json", [_probe_row("ollama/m:1b", {"A1-easy": True, "A1-hard": False})]
    )
    second = _write(
        tmp_path / "r2.json", [_probe_row("ollama/m:1b", {"A1-easy": True, "A1-hard": True})]
    )
    carrier = _write(tmp_path / "h.json", [_probe_row("ollama/m:1b", {"H1": True}, section="H")])

    page = _export_files(script, monkeypatch, tmp_path, native=[first, second], carrier=[carrier])

    native = page["models"][0]["native"]
    assert native["runs"] == 2 and native["total"] == 2
    assert native["passed"] == 1.5
    assert native["verdicts"] == {"A1-easy": 1.0, "A1-hard": 0.5}
    assert "isolated: mean of 2 runs" in page["models"][0]["notes"]
    # an identical row read twice is one run, not two
    page = _export_files(script, monkeypatch, tmp_path, native=[first, first], carrier=[carrier])
    assert page["models"][0]["native"]["runs"] == 1


def test_rows_covering_different_probes_union_their_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later run over new probes extends the row; a row short of the board's size says so."""
    script = _load_script()
    old = _write(
        tmp_path / "old.json",
        [
            _probe_row("ollama/m:1b", {"H1": True, "H2": False}, section="H"),
            _probe_row("ollama/n:1b", {"H1": True, "H2": True}, section="H"),
        ],
    )
    new = _write(tmp_path / "new.json", [_probe_row("ollama/m:1b", {"H3": True}, section="H")])
    native = _write(tmp_path / "a.json", [_probe_row("ollama/m:1b", {"A1-easy": True})])

    page = _export_files(script, monkeypatch, tmp_path, native=[native], carrier=[old, new])

    by_id = {m["id"]: m for m in page["models"]}
    m = by_id["ollama/m:1b"]["carrier"]
    assert (m["total"], m["passed"], m["runs"]) == (3, 2, 1)
    assert m["verdicts"] == {"H1": 1.0, "H2": 0.0, "H3": 1.0}
    assert page["suites"]["carrier"]["total"] == 3
    assert "in chunks: 2 of 3 probes run" in by_id["ollama/n:1b"]["notes"]
    assert not any("probes run" in n for n in by_id["ollama/m:1b"]["notes"])


def test_interval_is_deterministic_and_brackets_the_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 90% interval from resampling the tasks, the same on every export, around the score."""
    script = _load_script()
    native = _write(
        tmp_path / "a.json",
        [_probe_row("ollama/m:1b", {f"A{i}": i % 3 != 0 for i in range(1, 13)})],
    )
    carrier = _write(
        tmp_path / "h.json",
        [_probe_row("ollama/m:1b", {f"H{i}": i % 4 != 0 for i in range(1, 9)}, section="H")],
    )
    chat = _write(tmp_path / "c.json", [_chat_row("ollama/m:1b", ["q2"])])

    first = _export_files(
        script, monkeypatch, tmp_path, native=[native], carrier=[carrier], chat=[chat]
    )
    second = _export_files(
        script, monkeypatch, tmp_path, native=[native], carrier=[carrier], chat=[chat]
    )

    scores = first["models"][0]["scores"]
    assert scores == second["models"][0]["scores"]
    for key in ("extraction", "chat", "overall"):
        lo, hi = scores[f"{key}_ci90"]
        assert lo <= scores[key] <= hi, (key, scores)
        assert lo < hi
    # all-pass suites have no sampling spread
    sure_a = _write(
        tmp_path / "sure-a.json", [_probe_row("ollama/s:1b", {f"A{i}": True for i in range(1, 13)})]
    )
    sure_h = _write(
        tmp_path / "sure-h.json",
        [_probe_row("ollama/s:1b", {f"H{i}": True for i in range(1, 9)}, section="H")],
    )
    page = _export_files(script, monkeypatch, tmp_path, native=[sure_a], carrier=[sure_h])
    assert page["models"][0]["scores"]["extraction_ci90"] == [100.0, 100.0]


def test_excluded_questions_leave_the_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A question the fixture flags unanswerable is dropped from every row's count and verdicts."""
    script = _load_script()
    ids = ["q001", "q030", "q040"]  # q030 and q040 are flagged in the Book One fixture
    row = _chat_row("ollama/m:1b", ["q030"], ids=tuple(ids))
    row["metrics"]["verdicts"] = [
        {
            "id": q,
            "probe": q,
            "section": "multi_hop",
            "tier": "medium",
            "passed": q != "q030",
            "details": [],
        }
        for q in ids
    ]
    row["metrics"]["probes_total"], row["metrics"]["probes_passed"] = 3, 2
    chat = _write(tmp_path / "c.json", [row])

    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat))

    c = page["models"][0]["chat"]
    assert (c["passed"], c["total"], c["verdicts"]) == (1, 1, {"q001": 1.0})
    suite = page["suites"]["chat"]
    assert suite["total"] == 1
    # the board excludes exactly what the Book One fixture flags (pinned in test_chat_fixture_flags)
    fixture = load_queries(builtin_dataset_root() / "war_and_peace_book1" / "queries.yaml")
    assert set(suite["excluded_ids"]) == {q.id for q in fixture.queries if not q.answerable}
    flags = {q["id"]: q["answerable"] for q in suite["questions"]}
    assert flags["q001"] is True and flags["q030"] is False


def test_extra_sessions_become_a_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A harness row that needed extra client sessions says so on the page."""
    script = _load_script()
    row = _row("mcp/claude-code/m", "M via Claude Code", harness="mcp:claude-code")
    row["extras"] = {"run": {"sessions": {"A1-easy": 3, "H1-easy": 2}, "extra_sessions": 3}}
    probes = _write(tmp_path / "p.json", [row])

    page = _export_files(script, monkeypatch, tmp_path, native=[probes], carrier=[probes])

    m = page["models"][0]
    # each suite counts its own tasks (the row holds sections A and H)
    assert (m["native"]["extra_sessions"], m["native"]["retried_tasks"]) == (2, 1)
    assert (m["carrier"]["extra_sessions"], m["carrier"]["retried_tasks"]) == (1, 1)
    assert any(n.startswith("3 extra sessions on 2 tasks") for n in m["notes"]), m["notes"]


def test_graph_advantage_questions_stay_out_of_the_chat_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that covered the comparison band counts only the five board bands here."""
    script = _load_script()
    row = _chat_row("ollama/m:1b", ["q2"])
    row["metrics"]["verdicts"].append(
        {
            "id": "q082",
            "probe": "q082",
            "section": "graph_advantage",
            "tier": "hard",
            "passed": False,
            "details": [],
        }
    )
    chat = _write(tmp_path / "c.json", [row])

    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat))

    c = page["models"][0]["chat"]
    assert (c["passed"], c["total"]) == (4, 5)
    assert "q082" not in c["verdicts"]
    questions = page["suites"]["chat"]["questions"]
    assert len(questions) == 80
    assert not any(q["section"] == "graph_advantage" for q in questions)


# ---------------------------------------------------------------------------
# The three-rung comparison: suites.chat.comparison
# ---------------------------------------------------------------------------

# Real Book One ids: q001 (single-hop, answer term "July"/"1805"), q030 (paraphrase,
# flagged unanswerable, term "Archives"), q082 and q095 (graph_advantage,
# cross_chapter_join / relationship_path), q109 (cast_aggregate), q124 (disambiguation).
_CMP_IDS = ["q001", "q030", "q082", "q095", "q109", "q124"]
_TERMS = {
    "q001": "In July, 1805.",
    "q030": "To Anatole's, and then back to Moscow.",
    "q082": "General Kutúzov; Borís got no place on his staff.",
    "q095": "The count is his godfather.",
    "q109": "Hélène, Hippolyte and Anatole.",
    "q124": "Anna Pávlovna is the hostess; Anna Mikháylovna is Borís's mother.",
}
_CONTEXT = (
    "- Anna Pávlovna Schérer\n"
    "<untrusted_document>\n[CHUNK C0 | war_and_peace_book1.txt]\n"
    "[S1] It was in July, 1805, and the speaker was Anna Pávlovna Schérer.\n"
    "[S2] Kutúzov gave Borís no place on his staff.\n"
    "[S3] Count Bezúkhov is Borís's godfather, and Hélène, Hippolyte and Anatole are his children.\n"
    "[S4] Anna Pávlovna is the hostess; Anna Mikháylovna is Borís's mother; Pierre went to Anatole's, then back to Moscow.\n"
    "</untrusted_document>\n"
    "<untrusted_document>\n[CHUNK C1 | war_and_peace_book1.txt]\n"
    "[S1] The rain had not stopped since morning.\n"
    "</untrusted_document>"
)


def _mode_row(
    model_id: str, mode: str, right: set[str], *, cite: str | None = "C0", label: str | None = None
) -> dict[str, Any]:
    """A chat row under ``mode`` whose per_query answers get ``right`` right (the rest decline)."""
    row = _chat_row(model_id, [])
    if label:
        row["model_label"] = label
    suffix = "" if mode == "graphrag" else f"__retrieval_{mode}"
    row["dataset_id"] = f"{FIXTURE}__chat__ollama_gemma4:31b__ollama_qwen3-embedding:0.6b{suffix}"
    per_query = []
    for qid in _CMP_IDS:
        answer = _TERMS[qid] if qid in right else "That is not in the sources."
        if qid in right and cite:
            answer += f" [{cite}]"
        per_query.append(
            {
                "query_id": qid,
                "band": "graph_advantage" if qid >= "q082" else "factual_single_hop",
                "retrieved_entity_ids": [],
                "finish_reason": "stop",
                "output_tokens": 5,
                "context_text": _CONTEXT,
                "answer": answer,
            }
        )
    row["extras"] = {
        "per_query": per_query,
        "retrieval": mode,
        "retrieval_pins": dict(PINS),
        "run_settings": dict(RUN_SETTINGS),
    }
    # verdicts as the suite writes them (the band and the flagged question included)
    row["metrics"]["verdicts"] = [
        {
            "id": qid,
            "probe": qid,
            "section": "graph_advantage" if qid >= "q082" else "factual_single_hop",
            "tier": "hard",
            "passed": qid in right,
            "details": [],
            "citation": (
                "correct"
                if qid in right and cite == "C0"
                else ("wrong" if qid in right and cite else "uncited")
            ),
        }
        for qid in _CMP_IDS
        if qid != "q030"
    ]
    row["metrics"]["probes_total"] = 5
    row["metrics"]["probes_passed"] = sum(1 for q in _CMP_IDS if q in right and q != "q030")
    return row


def _three_modes(
    model_id: str, graphrag: set[str], hybrid: set[str], naive: set[str], **kw: Any
) -> list[dict[str, Any]]:
    return [
        _mode_row(model_id, "graphrag", graphrag, **kw),
        _mode_row(model_id, "hybrid", hybrid, **kw),
        _mode_row(model_id, "naive", naive, **kw),
    ]


ALL = set(_CMP_IDS)


def test_mode_rows_group_by_mode_and_only_graphrag_feeds_the_chat_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three rows for one model, one per mode: the chat column sees the graphrag row alone."""
    script = _load_script()
    chat = _write(
        tmp_path / "c.json",
        [
            *_three_modes("ollama/m:1b", graphrag=ALL, hybrid={"q001"}, naive={"q001"}),
            _mode_row("ollama/n:1b", "graphrag", ALL),  # graphrag only: not in the comparison
        ],
    )

    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat))

    by_id = {m["id"]: m for m in page["models"]}
    m = by_id["ollama/m:1b"]["chat"]
    # the chat column: the board bands only (q001; q030 is excluded by the audit), graphrag only
    assert (m["passed"], m["total"], m["runs"]) == (1, 1, 1)
    assert m["citation_accuracy"] == 100.0
    cmp = page["suites"]["chat"]["comparison"]
    assert [x["id"] for x in cmp["models"]] == ["ollama/m:1b"]
    assert cmp["kinds"] == [
        "cross_chapter_join",
        "relationship_path",
        "cast_aggregate",
        "disambiguation",
    ]
    assert cmp["groups"] == [*cmp["kinds"], "graph_advantage", "original"]


def test_per_group_counts_lifts_and_intervals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counts per kind, the band and the original 80 (answerable off); lifts in questions with ci90."""
    script = _load_script()
    chat = _write(
        tmp_path / "c.json",
        _three_modes("ollama/m:1b", graphrag=ALL, hybrid={"q001", "q082"}, naive={"q001"}),
    )

    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat))

    (model,) = page["suites"]["chat"]["comparison"]["models"]
    modes = model["modes"]
    assert modes["graphrag"]["graph_advantage"] == {"passed": 4, "total": 4}
    assert modes["hybrid"]["graph_advantage"] == {"passed": 1, "total": 4}
    assert modes["naive"]["graph_advantage"] == {"passed": 0, "total": 4}
    assert modes["graphrag"]["cross_chapter_join"] == {"passed": 1, "total": 1}
    assert modes["hybrid"]["relationship_path"] == {"passed": 0, "total": 1}
    # the original questions are scored with the answerable flags off: q030 counts
    assert modes["graphrag"]["original"] == {"passed": 2, "total": 2}
    assert modes["naive"]["original"] == {"passed": 1, "total": 2}
    assert modes["graphrag"]["citation_accuracy"] == 100.0
    lift = model["lift"]
    assert lift["graph_advantage"]["naive"]["value"] == 4
    assert lift["graph_advantage"]["hybrid"]["value"] == 3
    assert lift["original"]["naive"]["value"] == 1
    for group in ("graph_advantage", "original", "cross_chapter_join"):
        for baseline in ("naive", "hybrid"):
            lo, hi = lift[group][baseline]["ci90"]
            assert lo <= lift[group][baseline]["value"] <= hi, (group, baseline)
    # an all-equal pair has no spread
    assert lift["cross_chapter_join"]["hybrid"] == {"value": 0, "ci90": [0, 0]}


def test_examples_pick_the_largest_lift_questions_across_kinds_with_trimmed_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Up to three band questions, distinct kinds first, from the best local row and the best harness row."""
    script = _load_script()
    local_a = _three_modes("ollama/m:1b", graphrag=ALL, hybrid={"q001"}, naive={"q001", "q109"})
    local_b = _three_modes(
        "ollama/n:1b", graphrag=ALL - {"q124"}, hybrid={"q001"}, naive={"q001", "q109"}
    )
    harness = _three_modes(
        "mcp/claude-code/claude-x",
        graphrag=ALL,
        hybrid={"q001"},
        naive={"q001"},
        label="X via Claude Code",
    )
    for row in harness:
        row.update(
            {
                "harness": "mcp:claude-code",
                "pins_applied": False,
                "harness_settings": {"effort": "high"},
            }
        )
    # probe rows give the local models an Overall: n beats m on extraction
    probes = _write(
        tmp_path / "p.json",
        [
            _probe_row("ollama/m:1b", {"A1": True, "A2": False, "H1": True}),
            _probe_row("ollama/n:1b", {"A1": True, "A2": True, "H1": True}),
        ],
    )
    carrier = _write(
        tmp_path / "h.json",
        [
            _probe_row("ollama/m:1b", {"H1": True}, section="H"),
            _probe_row("ollama/n:1b", {"H1": True}, section="H"),
        ],
    )
    chat = _write(tmp_path / "c.json", local_a + local_b + harness)

    page = _export_files(
        script, monkeypatch, tmp_path, native=[probes], carrier=[carrier], chat=[chat]
    )

    cmp = page["suites"]["chat"]["comparison"]
    examples = cmp["examples"]
    assert len(examples) == 3
    # q109 is won by naive too (no lift); the three with lift across every model are
    # q082, q095, q124 (q124 by two of three) - distinct kinds first, then the rest
    assert [e["id"] for e in examples] == ["q082", "q095", "q124"]
    assert {e["kind"] for e in examples} == {
        "cross_chapter_join",
        "relationship_path",
        "disambiguation",
    }
    e = examples[0]
    assert e["question"].startswith("Anna Mikháylovna wanted Prince Vasíli")
    assert e["gold_answer"].startswith("General Kutúzov")
    assert [m["id"] for m in e["models"]] == ["ollama/n:1b", "mcp/claude-code/claude-x"]
    rung = e["models"][0]["rungs"]
    assert set(rung) == {"graphrag", "hybrid", "naive"}
    assert rung["graphrag"]["passed"] is True and rung["naive"]["passed"] is False
    assert rung["graphrag"]["answer"] == "General Kutúzov; Borís got no place on his staff. [C0]"
    assert rung["graphrag"]["citation"] == "correct"
    # the context is trimmed to the lines holding an answer term plus two lines each side
    ctx = rung["graphrag"]["context"]
    assert "Kutúzov" in ctx and "rain had not stopped" not in ctx
    assert ctx.count("\n") < _CONTEXT.count("\n")
    assert {q["id"] for q in cmp["questions"]} >= {"q082", "q095", "q109", "q124"}
    assert all(set(q) == {"id", "kind", "question"} for q in cmp["questions"])


def test_no_comparison_without_mode_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A board whose chat rows are all graphrag writes no comparison key."""
    script = _load_script()
    chat = _write(tmp_path / "c.json", [_chat_row("ollama/m:1b", ["q2"])])

    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat))

    assert "comparison" not in page["suites"]["chat"]
    assert page["models"][0]["chat"]["citation_accuracy"] is None


def test_value_block_measures_the_graph_four_ways_and_emits_only_true_headlines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The value block (2026-10-02): questions only the graph answers, lift on the band against
    the ordinary questions, errors removed per model with the sign count, and the smallest model
    that matches a bigger one's plain-RAG score; a headline is written only when its bar is met.
    """
    script = _load_script()
    small = _three_modes("ollama/gemma4:12b", graphrag=ALL, hybrid={"q001"}, naive={"q001"})
    big = _three_modes(
        "ollama/gemma4:31b", graphrag=ALL, hybrid={"q001", "q082"}, naive={"q001", "q082"}
    )
    other = _three_modes("ollama/m:1b", graphrag=ALL - {"q124"}, hybrid=set(), naive={"q001"})
    chat = _write(tmp_path / "c.json", [*small, *big, *other])

    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat))

    value = page["suites"]["chat"]["comparison"]["value"]
    assert value["models"] == 3
    # q082 is answered on naive by the big model; the other three band questions by nobody
    # without the graph, and by at least half the models with it
    assert value["only_with_graph"] == {"ids": ["q095", "q109", "q124"], "count": 3, "of": 4}
    assert value["unanswered_on_naive"]["ids"] == ["q095", "q109", "q124"]
    lift = value["lift"]
    # pass-rate points: small +100 on the band (0 -> 4 of 4), +50 on the original (1 -> 2 of 2)
    assert lift["graph_advantage"]["naive"]["per_model"]["ollama/gemma4:12b"] == 100.0
    assert lift["original"]["naive"]["per_model"]["ollama/gemma4:12b"] == 50.0
    assert lift["graph_advantage"]["naive"]["models_improved"] == 3
    assert lift["differential"]["naive"] == round(
        lift["graph_advantage"]["naive"]["mean"] - lift["original"]["naive"]["mean"], 1
    )
    errors = value["errors_removed"]["graph_advantage"]["naive"]
    assert errors["per_model"] == {
        "ollama/gemma4:12b": 100.0,
        "ollama/gemma4:31b": 100.0,
        "ollama/m:1b": 75.0,
    }
    assert errors["median_pct"] == 100.0
    # the population rule: all three clear the 25% bar against both baselines, so the
    # headline holds and says on how many models it rests
    assert errors["models_above_bar"] == 3
    assert value["errors_removed"]["graph_advantage"]["hybrid"]["models_above_bar"] == 3
    best = value["size_substitution"]["best"]
    assert (best["small"], best["big"]) == ("ollama/gemma4:12b", "ollama/gemma4:31b")
    assert best["ratio"] == round(20.0 / 7.6, 1)
    assert best["small_graphrag_pct"] >= best["big_naive_pct"]
    # the ordinary questions (q001, q030): every model fails q030 on naive and nothing with
    # the graph, so all three remove every failed answer there
    ordinary = value["errors_removed"]["original"]["naive"]
    assert ordinary["median_pct"] == 100.0 and ordinary["models_above_bar"] == 3
    assert (ordinary["fail_pct_baseline"], ordinary["fail_pct_graphrag"]) == (50.0, 0.0)
    # the size pair: the best plain-RAG model on the ordinary questions (a three-way tie at
    # 50%, the biggest wins it) against the smallest model the graph lifts to that score
    assert best["big_naive_pct"] == 50.0 and best["small_graphrag_pct"] == 100.0
    assert best["models_matching"] == 3
    holds = {h["key"]: h["holds"] for h in value["headlines"]}
    # 100% fewer failed answers both ways and a 2.6x size pair hold; three graph-only questions
    # are under the five the bar asks for
    assert holds == {
        "failures_ordinary": True,
        "errors_removed": True,
        "only_with_graph": False,
        "size_substitution": True,
    }
    texts = {h["key"]: h["text"] for h in value["headlines"]}
    assert texts["failures_ordinary"] == (
        "100% fewer failed answers than plain vector RAG on ordinary questions "
        "(fewer on 3 of 3 models; failed answers 50% → 0%)"
    )
    ordinary_values = next(h for h in value["headlines"] if h["key"] == "failures_ordinary")
    assert ordinary_values["values"]["models_fewer"] == 3
    assert texts["errors_removed"].startswith("100% fewer failed answers")
    assert "wrong answers" not in " ".join(texts.values())
    assert (
        "3 of 3 remove at least 25% against plain RAG and against hybrid search"
        in texts["errors_removed"]
    )
    assert texts["size_substitution"] == (
        "Gemma 4 12B with the graph answers better than Gemma 4 31B, the best model on plain "
        "vector RAG (100% vs 50% on ordinary questions); 3 of 3 models match it with the graph"
    )


def test_a_headline_needs_a_majority_of_models_not_the_median_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-10-03: a headline that rests on one or two models is not a headline. With the graph
    removing all errors for one model and none for two, the median is 0 and one model clears the
    bar, so the errors-removed headline must not hold; and a small model that matches the best
    plain-RAG score without the graph lifting it is no size pair (2026-10-21: the pair is the
    best plain-RAG model against the smallest model the graph lifts to it, held by a majority).
    """
    script = _load_script()
    # the star answers every band question with the graph and misses one ordinary question,
    # so it beats the big model's plain RAG and not its perfect hybrid search
    star = _three_modes(
        "ollama/gemma4:12b", graphrag=ALL - {"q001"}, hybrid={"q001"}, naive={"q001"}
    )
    flat_a = _three_modes("ollama/m:1b", graphrag={"q001"}, hybrid={"q001"}, naive={"q001"})
    flat_b = _three_modes("ollama/n:1b", graphrag={"q001"}, hybrid={"q001"}, naive={"q001"})
    big = _three_modes(
        "ollama/gemma4:31b", graphrag={"q001"}, hybrid=ALL, naive={"q001"}
    )  # the small model beats it on naive, not on hybrid
    chat = _write(tmp_path / "c.json", [*star, *flat_a, *flat_b, *big])

    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat))

    value = page["suites"]["chat"]["comparison"]["value"]
    errors = value["errors_removed"]["graph_advantage"]["naive"]
    assert errors["models_above_bar"] == 1
    holds = {h["key"]: h["holds"] for h in value["headlines"]}
    assert holds["errors_removed"] is False
    # no model fails fewer ordinary questions with the graph than without it
    assert holds["failures_ordinary"] is False
    # every model scores the same with and without the graph on the ordinary questions, so no
    # small model is lifted to the best plain-RAG score: matching it without the graph's help
    # is no size substitution
    assert holds["size_substitution"] is False
    assert value["size_substitution"]["best"] is None


# ---------------------------------------------------------------------------
# Rows that are not one finished, pinned measurement are refused (audit H4, H8, H9)
# ---------------------------------------------------------------------------


def test_a_timed_out_or_errored_chat_row_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """p2-lean-3090's qwen3:14b row held 120 connection errors and pooled as a 0.0 (2026-10-06)."""
    script = _load_script()
    timed_out = _chat_row("ollama/slow:70b", [], success=False, error="TimeoutError: 5400s")
    chat = _write(tmp_path / "chat.json", [_chat_row("ollama/gemma4:31b", ["q1"]), timed_out])
    with pytest.raises(SystemExit, match=r"ollama/slow:70b.*timed out.*--include-errored"):
        _export(script, monkeypatch, tmp_path, "--chat", str(chat))

    errored = _mode_row("ollama/qwen3:14b", "graphrag", set(_CMP_IDS))
    errored["extras"]["per_query"][1]["error"] = "LLMServiceError: connection refused"
    chat = _write(tmp_path / "errored.json", [errored])
    with pytest.raises(SystemExit, match=r"1 question\(s\) errored \(first q030: LLMServiceError"):
        _export(script, monkeypatch, tmp_path, "--chat", str(chat))
    # told to, the row pools as before: the errored question is a fail
    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat), "--include-errored")
    assert page["models"][0]["chat"]["passed"] is not None


def test_rescoring_refuses_an_errored_row_too(tmp_path: Path) -> None:
    """variant_compare rescores rows one by one: the refusal lives in _rescore as well."""
    script = _load_script()
    errored = _mode_row("ollama/qwen3:14b", "graphrag", set(_CMP_IDS))
    errored["extras"]["per_query"][0]["error"] = "TimeoutError"
    runs = script._chat_runs([_write(tmp_path / "e.json", [errored])], include_errored=True)
    items = next(iter(runs[FIXTURE].values()))
    with pytest.raises(SystemExit, match="errored"):
        script._rescore(items, script._fixture_queries(FIXTURE))
    assert script._rescore(items, script._fixture_queries(FIXTURE), include_errored=True)


def test_a_rung_without_pins_or_mixing_extraction_recipes_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The board's chat column pooled unpinned rows of six builds (H4); two extraction recipes are two graphs."""
    script = _load_script()
    unpinned = _chat_row("ollama/gemma4:31b", ["q1"])
    del unpinned["extras"]["retrieval_pins"]
    chat = _write(tmp_path / "unpinned.json", [unpinned])
    with pytest.raises(
        SystemExit, match=r"graphrag: rows without retrieval pins.*--allow-unpinned"
    ):
        _export(script, monkeypatch, tmp_path, "--chat", str(chat))
    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat), "--allow-unpinned")
    assert page["models"][0]["chat"]["passed"] == 4

    old, new = _chat_row("ollama/gemma4:31b", ["q1"]), _chat_row("ollama/qwen3:14b", ["q1"])
    old["extras"]["run_settings"]["extraction_recipe"] = "a" * 12
    new["extras"]["run_settings"]["extraction_recipe"] = "b" * 12
    chat = _write(tmp_path / "mixed.json", [old, new])
    with pytest.raises(
        SystemExit, match=r"graphrag: mixes extraction_recipe: a{12}.*b{12}.*--allow-mixed"
    ):
        _export(script, monkeypatch, tmp_path, "--chat", str(chat))
    _export(script, monkeypatch, tmp_path, "--chat", str(chat), "--allow-mixed")

    a, b = _chat_row("ollama/gemma4:31b", ["q1"]), _chat_row("ollama/gemma4:31b", ["q2"])
    b["timestamp"] = "2026-09-26T00:00:00+00:00"
    b["extras"]["retrieval_pins"] = {**PINS, "fingerprint": "e" * 16}
    chat = _write(tmp_path / "repeats.json", [a, b])
    with pytest.raises(
        SystemExit, match=r"pins: ollama/gemma4:31b \(local\) .* 2 different graphrag pins"
    ):
        _export(script, monkeypatch, tmp_path, "--chat", str(chat))


def test_oracle_rows_and_holdout_rows_are_refused_from_the_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit wave 2: a ceiling run, or a row asked another question file, is not a board score."""
    script = _load_script()
    ceiling = _chat_row("ollama/gemma4:31b", ["q1"])
    ceiling["extras"]["retrieval_pins"] = {**PINS, "oracle": True}
    chat = _write(tmp_path / "oracle.json", [ceiling])
    with pytest.raises(SystemExit, match=r"graphrag: rows served oracle pins.*--allow-oracle"):
        _export(script, monkeypatch, tmp_path, "--chat", str(chat))
    _export(script, monkeypatch, tmp_path, "--chat", str(chat), "--allow-oracle")

    holdout = tmp_path / "holdout.yaml"
    holdout.write_text("version: h\nqueries: []\n", encoding="utf-8")
    main_row, held = _chat_row("ollama/gemma4:31b", ["q1"]), _chat_row("ollama/qwen3:14b", ["q1"])
    held["extras"]["queries_file"] = str(holdout)
    chat = _write(tmp_path / "holdout.json", [main_row, held])
    with pytest.raises(SystemExit, match=r"graphrag: mixes .*question files.*--allow-mixed"):
        _export(script, monkeypatch, tmp_path, "--chat", str(chat))
    # a row that records the dataset's own file is the dataset fixture
    import chaoscypher_cli.benchmark as bm

    own = Path(bm.__file__).parent / "data" / "datasets" / FIXTURE / "queries.yaml"
    main_row["extras"]["queries_file"] = str(own)
    chat = _write(tmp_path / "own.json", [main_row])
    _export(script, monkeypatch, tmp_path, "--chat", str(chat))


def test_a_question_that_overflowed_the_context_window_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """num_predict 65536 against num_ctx 32768: 30 rows produced more than the window (H9)."""
    script = _load_script()
    row = _mode_row("ollama/qwen3:14b", "graphrag", set(_CMP_IDS))
    row["extras"]["run_settings"] = {"num_ctx": 32768}
    row["extras"]["per_query"][0].update({"prompt_tokens": 3000, "output_tokens": 30000})
    row["extras"]["per_query"][1].update({"prompt_tokens": 3000, "output_tokens": 29000})
    chat = _write(tmp_path / "chat.json", [row])
    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat))
    assert page["models"][0]["chat"]["context_overflows"] == 1
    assert "overflowed its context window on 1 question(s)" in capsys.readouterr().out


ARCHIVE = Path(__file__).resolve().parents[5] / "internal" / "benchmarks" / "runs"


@pytest.mark.skipif(
    not (ARCHIVE / "2026-10-05-p2-u-v10-5090.json").is_file(),
    reason="the archived runs are internal (not on the public export)",
)
def test_the_rows_sampled_at_0_3_are_refused_as_a_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit wave 3: p2-u-v10 ran at temperature 0.3 with no seed; one value across them is no measurement."""
    script = _load_script()
    files = [str(ARCHIVE / f"2026-10-05-p2-u-v10-{card}.json") for card in ("5090", "3090")]
    with pytest.raises(
        SystemExit, match=r"run_settings: unrecorded.*temperature 0\.3.*--allow-mixed"
    ):
        _export(script, monkeypatch, tmp_path, "--chat", *files)
    page, _ = _export(script, monkeypatch, tmp_path, "--chat", *files, "--allow-mixed")
    assert sum(1 for m in page["models"] if "chat" in m) == 5


@pytest.mark.parametrize(
    ("field", "value", "named"),
    [
        ("temperature", 0.3, r"temperature: 0\.0 .*0\.3"),
        ("seed", 43, r"seed: the rows ran at seeds 42, 43"),
        ("thinking", False, r"thinking: False"),
        ("run_settings.extraction_recipe", "b" * 12, r"extraction_recipe: .*b{12}"),
    ],
)
def test_the_board_uses_the_shared_comparability_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: Any, named: str
) -> None:
    """Decoding, seed, thinking and the graph's recipe: the exporter refuses what variant_compare does.

    The scorer fields are not a lever here: the board rescores every row
    (:data:`STORED_SCORER_FIELDS`), and the app version is recorded, not compared.
    """
    script = _load_script()
    a, b = _chat_row("ollama/gemma4:31b", ["q1"]), _chat_row("ollama/qwen3:14b", ["q2"])
    if field.startswith("run_settings."):
        b["extras"]["run_settings"][field.removeprefix("run_settings.")] = value
    else:
        b[field] = value
    chat = _write(tmp_path / "chat.json", [a, b])
    with pytest.raises(SystemExit, match=named):
        _export(script, monkeypatch, tmp_path, "--chat", str(chat))
    _export(script, monkeypatch, tmp_path, "--chat", str(chat), "--allow-mixed")


def test_rows_asked_two_fixture_contents_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit wave 3: one file name, two contents (a fixture edit between the runs)."""
    script = _load_script()
    a, b = _chat_row("ollama/gemma4:31b", ["q1"]), _chat_row("ollama/qwen3:14b", ["q2"])
    a["extras"]["queries_sha256"], b["extras"]["queries_sha256"] = "a" * 64, "b" * 64
    chat = _write(tmp_path / "chat.json", [a, b])
    with pytest.raises(SystemExit, match=r"queries_sha256: a{64} .*b{64}"):
        _export(script, monkeypatch, tmp_path, "--chat", str(chat))
    b["extras"]["queries_sha256"] = "a" * 64
    _export(script, monkeypatch, tmp_path, "--chat", str(_write(tmp_path / "same.json", [a, b])))


def test_an_errored_models_row_can_be_dropped_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Audit wave 3: one errored question no longer blocks the whole board; refusal stays the default."""
    script = _load_script()
    errored = _mode_row("ollama/qwen3:14b", "graphrag", set(_CMP_IDS))
    errored["extras"]["per_query"][1]["error"] = "LLMServiceError: connection refused"
    clean = _mode_row("ollama/gemma4:31b", "graphrag", set(_CMP_IDS))
    chat = _write(tmp_path / "chat.json", [clean, errored])
    with pytest.raises(SystemExit, match=r"--drop-errored-models"):
        _export(script, monkeypatch, tmp_path, "--chat", str(chat))
    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat), "--drop-errored-models")
    assert [m["id"] for m in page["models"] if "chat" in m] == ["ollama/gemma4:31b"]
    err = capsys.readouterr().err
    assert "DROPPED" in err and "ollama/qwen3:14b" in err and "1 question(s) errored" in err


# ---------------------------------------------------------------------------
# Audit wave 4: one scorer for every row, provenance, cross-rung checks
# ---------------------------------------------------------------------------


def test_the_board_scores_the_answers_never_the_stored_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row whose stored verdicts all pass but whose answers fail scores what the answers say."""
    script = _load_script()
    row = _chat_row("ollama/qwen3:14b", ["q2", "q5"])
    for v in row["metrics"]["verdicts"]:
        v["passed"] = True  # what an older scorer stored; the board must not believe it
    page, _ = _export(
        script, monkeypatch, tmp_path, "--chat", str(_write(tmp_path / "c.json", [row]))
    )
    chat = page["models"][0]["chat"]
    assert (chat["passed"], chat["total"]) == (3, 5)
    provenance = page["provenance"]
    from chaoscypher_core.benchmark.scorers.chat_probe_recipe import scorer_recipe

    assert provenance["scorer_recipe"] == scorer_recipe()
    assert provenance["overrides"] == [] and provenance["findings"] == []
    assert provenance["dropped_models"] == []
    assert provenance["fixture_sha256"] == script._fixture_queries(FIXTURE).sha256
    stored_only = _chat_row("ollama/gemma4:31b", ["q1"])
    stored_only["extras"]["per_query"] = []
    with pytest.raises(SystemExit, match="stores verdicts but no answers to score"):
        _export(
            script, monkeypatch, tmp_path, "--chat", str(_write(tmp_path / "s.json", [stored_only]))
        )


def test_a_file_and_its_rescored_copy_are_one_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dedup on (model, rung, timestamp): a rescore that moved a verdict no longer doubles the run."""
    script = _load_script()
    original = _chat_row("ollama/qwen3:14b", ["q2"])
    rescored = _chat_row("ollama/qwen3:14b", ["q2"])
    rescored["metrics"]["verdicts"][0]["passed"] = False  # a stored verdict the rescore moved
    files = [
        str(_write(tmp_path / "a.json", [original])),
        str(_write(tmp_path / "b.json", [rescored])),
    ]
    page, _ = _export(script, monkeypatch, tmp_path, "--chat", *files)
    assert page["models"][0]["chat"]["runs"] == 1


def test_overrides_and_drops_are_written_into_the_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rebuild_board.sh's flags leave a trace: the overrides, the lifted findings, the dropped rows."""
    script = _load_script()
    old, new = _chat_row("ollama/gemma4:31b", ["q1"]), _chat_row("ollama/qwen3:14b", ["q1"])
    old["extras"]["run_settings"]["extraction_recipe"] = "a" * 12
    new["extras"]["run_settings"]["extraction_recipe"] = "b" * 12
    del new["extras"]["retrieval_pins"]
    slow = _chat_row("ollama/slow:70b", [], success=False, error="TimeoutError: 5400s")
    chat = _write(tmp_path / "c.json", [old, new, slow])
    page, _ = _export(
        script,
        monkeypatch,
        tmp_path,
        "--chat",
        str(chat),
        "--allow-unpinned",
        "--allow-mixed",
        "--drop-timed-out-models",
    )
    provenance = page["provenance"]
    assert provenance["overrides"] == [
        "--allow-unpinned",
        "--allow-mixed",
        "--drop-timed-out-models",
    ]
    assert any("rows without retrieval pins" in f for f in provenance["findings"])
    assert any("extraction_recipe" in f for f in provenance["findings"])
    assert [d["model"] for d in provenance["dropped_models"]] == ["ollama/slow:70b"]
    assert provenance["dropped_models"][0]["flag"] == "--drop-timed-out-models"


def test_drop_errored_never_drops_a_timed_out_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two flags, each named: a timed-out row needs --drop-timed-out-models."""
    script = _load_script()
    slow = _chat_row("ollama/slow:70b", [], success=False, error="TimeoutError: 5400s")
    chat = _write(tmp_path / "c.json", [_chat_row("ollama/gemma4:31b", ["q1"]), slow])
    with pytest.raises(SystemExit, match=r"ollama/slow:70b.*timed out.*--drop-timed-out-models"):
        _export(script, monkeypatch, tmp_path, "--chat", str(chat), "--drop-errored-models")
    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat), "--drop-timed-out-models")
    assert [m["id"] for m in page["models"] if "chat" in m] == ["ollama/gemma4:31b"]
    assert "(--drop-timed-out-models)" in capsys.readouterr().err


def test_the_three_rungs_are_checked_across_the_rungs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A naive rung on another graph beside the graphrag rung is refused (each rung alone passed)."""
    script = _load_script()
    rows = _three_modes("ollama/m:1b", graphrag=ALL, hybrid={"q001"}, naive={"q001"})
    rows[2]["extras"]["run_settings"]["extraction_recipe"] = "b" * 12
    rows[1]["extras"]["retrieval_pins"] = {**PINS, "file": "hybrid-f.json"}  # pins differ by rung
    chat = _write(tmp_path / "c.json", rows)
    with pytest.raises(
        SystemExit, match=r"across the rungs: extraction_recipe: .*b{12}.*--allow-mixed"
    ):
        _export(script, monkeypatch, tmp_path, "--chat", str(chat))
    del rows[2]["extras"]["run_settings"]["extraction_recipe"]  # the graph the other two rungs read
    page, _ = _export(
        script, monkeypatch, tmp_path, "--chat", str(_write(tmp_path / "d.json", rows))
    )
    assert page["suites"]["chat"]["comparison"]["models"][0]["id"] == "ollama/m:1b"


# ---------------------------------------------------------------------------
# Audit wave 5: provenance unknown is not clean, overrides withhold headlines,
# the stored scorer fields are not the board's, named drops, the newest copy
# ---------------------------------------------------------------------------

BOARD_FILE = "packages/docs/src/data/leaderboard.json"
PROVENANCE_TS_FILE = "packages/docs/src/lib/leaderboardProvenance.ts"


def test_the_committed_boards_provenance_block_exists_and_is_self_consistent() -> None:
    """Guard: the committed board carries a provenance block, sound or explicitly unrecorded.

    The 2026-10-04 board predates the block, so it carries the explicit
    unrecorded form (every key null), which the page reads as unknown and
    bannered. Whether a sound block matches this checkout is ``--check``'s
    job (rebuild_board.sh), not this test's: that needs the GPU rows. Skipped
    where the docs tree is not shipped (the Docker test image).
    """
    board_path = repo_file_or_skip(BOARD_FILE)
    script = _load_script()
    board = json.loads(board_path.read_text(encoding="utf-8"))
    problems = script.provenance_problems(board)
    assert problems in ([], [script.UNRECORDED_PROVENANCE]), (
        f"{board_path}: {problems}; rebuild it with {script.REBUILD_COMMAND}"
    )


def test_the_page_reads_the_keys_the_exporter_writes() -> None:
    """One list of provenance keys: the page's PROVENANCE_KEYS is the exporter's."""
    provenance_ts = repo_file_or_skip(PROVENANCE_TS_FILE)
    script = _load_script()
    block = re.search(
        r"PROVENANCE_KEYS = \[(.*?)\]", provenance_ts.read_text(encoding="utf-8"), re.S
    )
    assert block is not None
    assert tuple(re.findall(r'"([a-z0-9_]+)"', block.group(1))) == script.PROVENANCE_KEYS
    assert set(script.PROVENANCE_KEYS) == set(script._provenance(_args(), [], [], None))
    # audit wave 6: the flags and the chat keys the page checks are the exporter's too
    text = provenance_ts.read_text(encoding="utf-8")
    for name, expected in (
        ("OVERRIDE_FLAGS", script.OVERRIDE_FLAGS),
        ("CHAT_PROVENANCE_KEYS", script.CHAT_PROVENANCE_KEYS),
    ):
        listed = re.search(rf"export const {name} = \[(.*?)\]", text, re.S)
        assert listed is not None, name
        assert tuple(re.findall(r'"([-a-z0-9_]+)"', listed.group(1))) == expected, name


def _provenance_fixtures(script: Any) -> dict[str, dict[str, Any]]:
    """Boards the parity test runs both rules on: every check's pass and fail, and odd shapes."""
    sound = {
        "overrides": [],
        "findings": [],
        "dropped_models": [],
        "scorer_version": 6,
        "scorer_recipe": "r" * 12,
        "fixture_sha256": "f" * 64,
    }
    chat = {"suites": {"chat": {"total": 1}}}
    nulls = dict.fromkeys(script.PROVENANCE_KEYS)
    fixtures: dict[str, dict[str, Any]] = {
        "no block": {},
        "block not a mapping": {"provenance": ["overrides"]},
        "a key missing": {"provenance": {k: v for k, v in sound.items() if k != "findings"}},
        "unrecorded": {"provenance": nulls},
        "unrecorded with a stray key": {"provenance": {**nulls, "scorer_recipe": "r"}},
        "sound, no chat": {
            "provenance": {**nulls, **{k: [] for k in ("overrides", "findings", "dropped_models")}}
        },
        "sound chat": {**chat, "provenance": sound},
        "chat without its recipe": {**chat, "provenance": {**sound, "scorer_recipe": ""}},
        "chat without its version": {**chat, "provenance": {**sound, "scorer_version": None}},
        "chat at scorer version 0": {**chat, "provenance": {**sound, "scorer_version": 0}},
        "an empty chat suite": {
            "suites": {"chat": {}},
            "provenance": {**sound, "scorer_recipe": None},
        },
        "findings without an override": {**chat, "provenance": {**sound, "findings": ["x"]}},
        "findings beside an override": {
            **chat,
            "provenance": {**sound, "findings": ["x"], "overrides": ["--allow-mixed"]},
        },
        "an unknown flag": {**chat, "provenance": {**sound, "overrides": ["--allow-anything"]}},
        "a drop by a flag not passed": {
            **chat,
            "provenance": {
                **sound,
                "dropped_models": [{"model": "m", "why": "w", "flag": "--drop-errored-models"}],
            },
        },
        "a drop by a flag passed": {
            **chat,
            "provenance": {
                **sound,
                "overrides": ["--drop-errored-models"],
                "dropped_models": [{"model": "m", "why": "w", "flag": "--drop-errored-models"}],
            },
        },
        "a drop without a flag": {
            **chat,
            "provenance": {
                **sound,
                "dropped_models": [{"model": "m", "why": "judged", "flag": None}],
            },
        },
        "a drop that is not a record": {**chat, "provenance": {**sound, "dropped_models": ["m"]}},
        "overrides not a list": {**chat, "provenance": {**sound, "overrides": "--allow-mixed"}},
        "dropped_models null in a recorded block": {
            **chat,
            "provenance": {**sound, "dropped_models": None},
        },
    }
    fixtures = {name: {"board_format": script.BOARD_FORMAT, **b} for name, b in fixtures.items()}
    # the layout is part of what is known: an old or newer one is not a board this page reads
    fixtures["format 1 (no board_format)"] = {**chat, "provenance": sound}
    fixtures["a newer format"] = {**chat, "board_format": 3, "provenance": sound}
    fixtures["a format that is not a number"] = {**chat, "board_format": "2", "provenance": sound}
    board_path = repo_root() / BOARD_FILE
    if board_path.is_file():
        fixtures["the committed board"] = json.loads(board_path.read_text(encoding="utf-8"))
    return fixtures


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_page_and_the_exporter_judge_every_board_alike(tmp_path: Path) -> None:
    """Audit wave 6: ``readProvenance`` was weaker than ``provenance_problems``; one rule, run both ways.

    The page's module runs under node (type stripping) on the same boards: the
    same problem count for each, and ``known`` exactly when the exporter finds
    nothing wrong.
    """
    script = _load_script()
    fixtures = _provenance_fixtures(script)
    page = judge_boards(
        tmp_path,
        {"leaderboardProvenance": ["provenanceProblems", "readProvenance"]},
        "{ problems: provenanceProblems(board).length, known: readProvenance(board).known,"
        " provisional: readProvenance(board).provisional }",
        fixtures,
    )
    for name, board in fixtures.items():
        problems = script.provenance_problems(board)
        block = board.get("provenance")
        overrides = block.get("overrides") if isinstance(block, dict) else None
        assert page[name]["problems"] == len(problems), (name, problems)
        assert page[name]["known"] is (not problems), name
        assert page[name]["provisional"] is (
            bool(problems) or bool(isinstance(overrides, list) and overrides)
        ), name


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_a_provisional_board_shows_no_comparison(tmp_path: Path) -> None:
    """The retrieval comparison is a claim: on a provisional board none of it shows.

    2026-10-19: the page withheld the headlines on a provisional board but still
    rendered the value grid, the comparison table and the worked examples from
    the same rows (the pre-audit "41% fewer wrong answers"). One rule,
    ``comparisonShows``, now decides every surface, and ``headlineHolds`` reads it.
    """
    page = judge_boards(
        tmp_path,
        {"leaderboardProvenance": ["comparisonShows", "headlineHolds", "readProvenance"]},
        "{ provisional: readProvenance(board).provisional,"
        " shows: comparisonShows(readProvenance(board)),"
        ' held: headlineHolds({ holds: true, text: "x" }, readProvenance(board)) }',
        _provenance_fixtures(_load_script()),
    )
    assert any(v["provisional"] for v in page.values())
    assert any(not v["provisional"] for v in page.values())
    for name, verdict in page.items():
        assert verdict["shows"] is (not verdict["provisional"]), name
        assert verdict["held"] is verdict["shows"], name


PAGE_TSX_FILE = "packages/docs/src/pages/leaderboard.tsx"
TEASER_TSX_FILE = "packages/docs/src/components/LeaderboardTeaser.tsx"


def test_the_comparison_renders_only_through_the_shared_gate() -> None:
    """Both surfaces decide "may the comparison show" by ``comparisonShows``, never by hand.

    The page renders "Where the graph matters" only under the gate, and the
    teaser's one line reads the same function, so a third reading of
    ``provenance.provisional`` cannot drift from the other two.
    """
    page = repo_file_or_skip(PAGE_TSX_FILE).read_text(encoding="utf-8")
    teaser = repo_file_or_skip(TEASER_TSX_FILE).read_text(encoding="utf-8")
    for name, text in (("page", page), ("teaser", teaser)):
        imports = re.search(r"import \{([^}]*)\} from \"[^\"]*leaderboardProvenance\"", text)
        assert imports is not None, name
        assert "comparisonShows" in imports.group(1), name
    assert re.search(r"comparisonShows\(provenance\)\s*\?\s*\(?\s*<GraphAdvantage\b", page), (
        "the page renders <GraphAdvantage> only when comparisonShows(provenance)"
    )
    line = re.search(r"function graphAdvantageLine\(.*?\n\}\n", teaser, re.S)
    assert line is not None
    assert "comparisonShows(provenance)" in line.group(0)
    assert "provenance.provisional" not in line.group(0)


def _args(**flags: bool) -> Any:
    """An argparse namespace with every override flag off unless named."""
    import argparse

    names = {f.removeprefix("--").replace("-", "_") for f in _load_script().OVERRIDE_FLAGS}
    return argparse.Namespace(**{n: flags.get(n, False) for n in names})


def test_check_passes_a_fresh_board_and_names_the_rebuild_for_a_stale_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--check compares the recorded scorer recipe, version and fixture hash with this checkout."""
    script = _load_script()
    chat = _write(tmp_path / "c.json", [_chat_row("ollama/qwen3:14b", ["q2"])])
    _export(script, monkeypatch, tmp_path, "--chat", str(chat))
    page = tmp_path / "page.json"

    def check(path: Path) -> None:
        monkeypatch.setattr(sys, "argv", ["export_leaderboard.py", "--check", str(path)])
        script.main()

    check(page)
    assert "provenance matches this checkout" in capsys.readouterr().out
    board = json.loads(page.read_text(encoding="utf-8"))
    stale = tmp_path / "stale.json"
    for mutate, named in [
        (lambda b: b["provenance"].update(scorer_recipe="0" * 16), "scorer recipe 0{16}"),
        (lambda b: b["provenance"].update(fixture_sha256="f" * 64), "sha256 f{64}"),
        (lambda b: b["provenance"].update(scorer_version=0), "scorer v0"),
        (lambda b: b.pop("provenance"), "no provenance block"),
        (
            lambda b: b.update(provenance=dict.fromkeys(script.PROVENANCE_KEYS)),
            "unrecorded",
        ),
    ]:
        copy = json.loads(json.dumps(board))
        mutate(copy)
        stale.write_text(json.dumps(copy), encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            check(stale)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert re.search(named, err), err
        assert f"rebuild it with {script.REBUILD_COMMAND}" in err


def test_a_provenance_block_that_contradicts_itself_is_named() -> None:
    """Findings without an override, a drop by a flag not passed, a chat board without its scorer."""
    script = _load_script()
    block = {
        "overrides": [],
        "findings": ["graphrag: rows without retrieval pins (x)"],
        "dropped_models": [{"model": "m", "flag": "--drop-timed-out-models"}],
        "scorer_version": None,
        "scorer_recipe": None,
        "fixture_sha256": None,
    }
    problems = script.provenance_problems(
        {"board_format": 2, "provenance": block, "suites": {"chat": {"total": 1}}}
    )
    assert any("no override is recorded" in p for p in problems)
    assert any("dropped by --drop-timed-out-models, not passed" in p for p in problems)
    assert any("without scorer_recipe" in p for p in problems)
    half = {**dict.fromkeys(script.PROVENANCE_KEYS), "scorer_recipe": "r"}
    assert (
        "records scorer_recipe"
        in script.provenance_problems({"board_format": 2, "provenance": half})[0]
    )


def test_an_override_withholds_every_headline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A headline whose bar is met over rows an override let through does not hold."""
    script = _load_script()
    small = _three_modes("ollama/gemma4:12b", graphrag=ALL, hybrid={"q001"}, naive={"q001"})
    big = _three_modes(
        "ollama/gemma4:31b", graphrag=ALL, hybrid={"q001", "q082"}, naive={"q001", "q082"}
    )
    other = _three_modes("ollama/m:1b", graphrag=ALL - {"q124"}, hybrid=set(), naive={"q001"})
    chat = _write(tmp_path / "c.json", [*small, *big, *other])
    clean, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat))
    assert any(h["holds"] for h in clean["suites"]["chat"]["comparison"]["value"]["headlines"])
    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat), "--allow-mixed")
    headlines = page["suites"]["chat"]["comparison"]["value"]["headlines"]
    assert not any(h["holds"] for h in headlines)
    assert {tuple(h.get("withheld_by", ())) for h in headlines if h["text"]} >= {("--allow-mixed",)}


def test_the_board_ignores_what_scored_the_stored_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It rescores every row: a stored scorer version, recipe or rescore hash is not a finding."""
    script = _load_script()
    a, b = _chat_row("ollama/gemma4:31b", ["q1"]), _chat_row("ollama/qwen3:14b", ["q2"])
    a["scorer_version"], b["scorer_version"] = 5, 6
    a["scorer_recipe"], b["scorer_recipe"] = "r5", "r6"
    b["extras"]["rescored_queries_sha256"] = "c" * 64
    page, _ = _export(
        script, monkeypatch, tmp_path, "--chat", str(_write(tmp_path / "c.json", [a, b]))
    )
    assert page["provenance"]["findings"] == []
    assert sorted(m["id"] for m in page["models"] if "chat" in m) == [
        "ollama/gemma4:31b",
        "ollama/qwen3:14b",
    ]


def test_a_judged_row_left_out_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A judged row is not a probe-scored row; it leaves the board by name, with no flag."""
    script = _load_script()
    judged = _chat_row("ollama/judged:7b", [])
    judged["extras"]["judge_model"] = "gemma4:31b"
    chat = _write(tmp_path / "c.json", [_chat_row("ollama/qwen3:14b", ["q2"]), judged])
    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat))
    assert [m["id"] for m in page["models"] if "chat" in m] == ["ollama/qwen3:14b"]
    assert page["provenance"]["dropped_models"] == [
        {
            "model": "ollama/judged:7b",
            "corpus": FIXTURE,
            "file": str(chat),
            "why": "judged by gemma4:31b (the board scores the probe checks)",
            "flag": None,
        }
    ]
    assert "DROPPED" in capsys.readouterr().err
    assert script.provenance_problems(page) == []


def test_of_two_copies_of_one_run_the_newest_file_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The newest copy, wherever it is named on the command line (it was the first named)."""
    import os

    script = _load_script()
    older = _chat_row("ollama/qwen3:14b", ["q2", "q3"])
    newer = _chat_row("ollama/qwen3:14b", ["q2"])
    old_path = _write(tmp_path / "old.json", [older])
    new_path = _write(tmp_path / "new.json", [newer])
    os.utime(old_path, (1_000_000, 1_000_000))
    os.utime(new_path, (2_000_000, 2_000_000))
    for order in ([new_path, old_path], [old_path, new_path]):
        page, _ = _export(script, monkeypatch, tmp_path, "--chat", *map(str, order))
        chat = page["models"][0]["chat"]
        assert (chat["runs"], chat["passed"]) == (1, 4)


def test_a_superseded_copy_is_dropped_before_its_problems_are_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Audit wave 6: an errored older copy of a run was listed as dropped beside its newer copy."""
    import os

    script = _load_script()
    older = _chat_row("ollama/qwen3:14b", ["q2"], success=False, error="RuntimeError: boom")
    newer = _chat_row("ollama/qwen3:14b", ["q2"])
    old_path = _write(tmp_path / "old.json", [older])
    new_path = _write(tmp_path / "new.json", [newer])
    os.utime(old_path, (1_000_000, 1_000_000))
    os.utime(new_path, (2_000_000, 2_000_000))
    page, _ = _export(
        script,
        monkeypatch,
        tmp_path,
        "--chat",
        str(old_path),
        str(new_path),
        "--drop-errored-models",
    )
    assert page["models"][0]["chat"]["runs"] == 1
    assert page["provenance"]["dropped_models"] == []
    assert "DROPPED" not in capsys.readouterr().err
    # without the flag the superseded copy refuses nothing either
    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(old_path), str(new_path))
    assert page["models"][0]["chat"]["runs"] == 1


def test_board_minutes_leave_out_the_retry_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An older row's total holds the backoff (subtracted); a newer one already leaves it out."""
    from chaoscypher_core.benchmark.results import LATENCY_EXCLUDES_RETRY_WAIT

    script = _load_script()
    row = _chat_row("ollama/qwen3:14b", ["q2"])
    row["extras"]["per_query"][0]["retry_wait_ms"] = 60000
    page, _ = _export(
        script, monkeypatch, tmp_path, "--chat", str(_write(tmp_path / "a.json", [row]))
    )
    assert page["models"][0]["chat"]["minutes"] == 2.0
    row["extras"][LATENCY_EXCLUDES_RETRY_WAIT] = True
    page, _ = _export(
        script, monkeypatch, tmp_path, "--chat", str(_write(tmp_path / "b.json", [row]))
    )
    assert page["models"][0]["chat"]["minutes"] == 3.0


METHODS_MD_FILE = "packages/docs/docs/reference/extraction-benchmark.md"


def test_the_methods_page_states_the_exporters_headline_bars() -> None:
    """The page's "headlines and their bars" paragraph quotes the bars the exporter applies."""
    script = _load_script()
    text = repo_file_or_skip(METHODS_MD_FILE).read_text(encoding="utf-8")
    para = text.split("**The headlines and their bars.**", 1)[1].split("**Citation accuracy.**")[0]
    para = " ".join(para.split())
    assert f"at least {script.HEADLINE_ERRORS_REMOVED_PCT:.0f}%" in para
    assert f"clear {script.HEADLINE_ERRORS_REMOVED_PCT:.0f}% too" in para
    assert f"at least {script.HEADLINE_SIZE_RATIO} times" in para
    assert f"it needs {script.HEADLINE_ONLY_WITH_GRAPH}." in para


# ---------------------------------------------------------------------------
# Each corpus is reported on its own (2026-10-22): never pooled across corpora
# ---------------------------------------------------------------------------

DOCS = "synthetic_docs"
"""A second, invented corpus: a user dataset written under the test's data dir."""

DOCS_LABEL = "the Synthetic Docs"

_DOCS_ANSWERS = {
    "d001": "Lumen.",
    "d002": "Vesper.",
    "d003": "Orrin.",
    "d004": "Through Vesper.",
    "d005": "Quill and Mirth.",
    "d006": "Tally.",
}
_DOCS_CONTEXT = (
    "<untrusted_document>\n[CHUNK C0 | synthetic_docs.txt]\n"
    "[S1] The server is called Lumen.\n"
    "[S2] Lumen reads Vesper, and Vesper needs Orrin.\n"
    "[S3] Quill and Mirth use Orrin.\n"
    "[S4] Tally is the flag; Tally is also the tool.\n"
    "</untrusted_document>"
)
_DOCS_QUERIES = """\
version: "1.1"
queries:
  - id: d001
    band: factual_single_hop
    question: "What is the server called?"
    gold_entities: ["Lumen"]
    answer_terms: ["Lumen"]
    gold_answer: "Lumen."
  - id: d002
    band: multi_hop
    question: "What does the server read?"
    gold_entities: ["Vesper"]
    answer_terms: ["Vesper"]
    gold_answer: "Vesper."
  - id: d003
    band: graph_advantage
    kind: cross_chapter_join
    question: "What does the thing the server reads need?"
    gold_entities: ["Orrin"]
    answer_terms: ["Orrin"]
    gold_answer: "Orrin."
  - id: d004
    band: graph_advantage
    kind: relationship_path
    question: "How is the server connected to Orrin?"
    gold_entities: ["Vesper"]
    answer_terms: ["Vesper"]
    gold_answer: "Through Vesper."
  - id: d005
    band: graph_advantage
    kind: cast_aggregate
    question: "Which tools use Orrin?"
    gold_entities: ["Quill", "Mirth"]
    answer_terms: ["Quill", "Mirth"]
    gold_answer: "Quill and Mirth."
  - id: d006
    band: graph_advantage
    kind: disambiguation
    question: "Which name is both a flag and a tool?"
    gold_entities: ["Tally"]
    answer_terms: ["Tally"]
    gold_answer: "Tally."
"""
DOCS_ALL = set(_DOCS_ANSWERS)
DOCS_BAND = {"d003", "d004", "d005", "d006"}


def _docs_dataset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, label: str | None = DOCS_LABEL
) -> Path:
    """Write :data:`DOCS` as a user dataset under a fresh data dir; return its manifest."""
    root = tmp_path / "data"
    pack = root / "benchmark" / "datasets" / DOCS
    pack.mkdir(parents=True)
    (pack / f"{DOCS}.txt").write_text(_DOCS_CONTEXT, encoding="utf-8")
    (pack / "queries.yaml").write_text(_DOCS_QUERIES, encoding="utf-8")
    manifest = [
        f"id: {DOCS}",
        "kind: extraction",
        'version: "1.0"',
        "domain: technical",
        f"corpus_path: {DOCS}.txt",
        "queries_path: queries.yaml",
    ]
    if label is not None:
        manifest.append(f'label: "{label}"')
    (pack / "manifest.yaml").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(root))
    return pack / "manifest.yaml"


def _load_script_per_row() -> Any:
    """The script with every row scored on its own dataset's questions (nothing patched)."""
    spec = importlib.util.spec_from_file_location("export_leaderboard_corpora", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _docs_row(model_id: str, mode: str, right: set[str]) -> dict[str, Any]:
    """A chat row of :data:`DOCS` under ``mode`` that gets ``right`` right (the rest decline)."""
    row = _chat_row(model_id, [])
    suffix = "" if mode == "graphrag" else f"__retrieval_{mode}"
    row["dataset_id"] = f"{DOCS}__chat__ollama_gemma4:31b__ollama_qwen3-embedding:0.6b{suffix}"
    row["extras"] = {
        "per_query": [
            {
                "query_id": qid,
                "band": "graph_advantage" if qid in DOCS_BAND else "factual_single_hop",
                "finish_reason": "stop",
                "output_tokens": 5,
                "context_text": _DOCS_CONTEXT,
                "answer": (_DOCS_ANSWERS[qid] + " [C0]")
                if qid in right
                else "That is not in the sources.",
            }
            for qid in sorted(_DOCS_ANSWERS)
        ],
        "retrieval": mode,
        "retrieval_pins": dict(PINS),
        "run_settings": dict(RUN_SETTINGS),
    }
    row["metrics"] = {"verdicts": []}
    return row


def _docs_modes(
    model_id: str, graphrag: set[str], hybrid: set[str], naive: set[str]
) -> list[dict[str, Any]]:
    return [
        _docs_row(model_id, "graphrag", graphrag),
        _docs_row(model_id, "hybrid", hybrid),
        _docs_row(model_id, "naive", naive),
    ]


def _two_corpora(tmp_path: Path) -> Path:
    """Book One under three models and the synthetic docs under two, in one file, docs first.

    On Book One ``m`` answers more of the chat column than ``n``; on the docs ``n``
    answers more than ``m``, so a statistic that pooled the corpora, or ranked one
    corpus's models by the other's score, would show.
    """
    rows = [
        *_docs_modes("ollama/n:1b", graphrag=DOCS_ALL, hybrid={"d001"}, naive={"d001"}),
        *_docs_modes("ollama/m:1b", graphrag=DOCS_BAND, hybrid=set(), naive=set()),
        *_three_modes("ollama/m:1b", graphrag=ALL, hybrid={"q001"}, naive={"q001"}),
        *_three_modes("ollama/n:1b", graphrag=ALL - {"q001"}, hybrid=set(), naive=set()),
        *_three_modes("ollama/gemma4:12b", graphrag=ALL - {"q001"}, hybrid=set(), naive=set()),
    ]
    return _write(tmp_path / "two.json", rows)


def test_two_corpora_are_reported_each_on_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every headline, count and example is one corpus's; the board names each corpus.

    The board corpus (Book One) keeps ``suites.chat``, the table's chat column and
    Overall; the synthetic docs get their own entry in ``corpora`` with their own
    chat suite, comparison, per-model chat objects and fixture hash.
    """
    _docs_dataset(monkeypatch, tmp_path)
    script = _load_script_per_row()

    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(_two_corpora(tmp_path)))

    assert page["board_format"] == script.BOARD_FORMAT == 2
    board, docs = page["corpora"]
    assert {k: v for k, v in board.items() if k != "last_run"} == {
        "id": FIXTURE,
        "label": "War and Peace, Book One",
        "board": True,
        "review": None,  # no hand review of this corpus's board answers was given
    }
    assert docs["review"] is None
    assert (docs["id"], docs["label"], docs["board"]) == (DOCS, DOCS_LABEL, False)
    # the board corpus: the table's chat column and its comparison, Book One's questions only
    chat = page["suites"]["chat"]
    assert chat["fixture"] == FIXTURE
    assert {q["id"] for q in chat["questions"]} <= {
        q.id for q in script._fixture_queries(FIXTURE).queries
    }
    book = chat["comparison"]
    assert {q["id"] for q in book["questions"]} >= {"q082", "q095", "q109", "q124"}
    assert not {q["id"] for q in book["questions"]} & DOCS_ALL
    assert book["value"]["models"] == 3
    assert {m["id"] for m in book["models"]} == {"ollama/m:1b", "ollama/n:1b", "ollama/gemma4:12b"}
    # the docs: their own suite, comparison and model count, never Book One's rows
    other = docs["chat"]
    assert other["fixture"] == DOCS
    assert {q["id"] for q in other["questions"]} == {"d001", "d002"}
    cmp = other["comparison"]
    assert {q["id"] for q in cmp["questions"]} == DOCS_BAND
    assert cmp["value"]["models"] == 2
    assert {m["id"] for m in cmp["models"]} == {"ollama/m:1b", "ollama/n:1b"}
    ordinary = next(h for h in cmp["value"]["headlines"] if h["key"] == "failures_ordinary")
    assert ordinary["values"]["models"] == 2 and ordinary["values"]["questions"] == 2
    (n_docs,) = (m for m in cmp["models"] if m["id"] == "ollama/n:1b")
    assert n_docs["modes"]["graphrag"]["graph_advantage"] == {"passed": 4, "total": 4}
    assert n_docs["modes"]["naive"]["original"] == {"passed": 1, "total": 2}
    # per-model chat objects: Book One on the model rows, the docs in their entry
    by_id = {m["id"]: m for m in page["models"]}
    assert (by_id["ollama/m:1b"]["chat"]["passed"], by_id["ollama/m:1b"]["chat"]["total"]) == (1, 1)
    assert (by_id["ollama/n:1b"]["chat"]["passed"], by_id["ollama/n:1b"]["chat"]["total"]) == (0, 1)
    assert (docs["models"]["ollama/m:1b"]["passed"], docs["models"]["ollama/m:1b"]["total"]) == (
        0,
        2,
    )
    assert docs["models"]["ollama/n:1b"]["passed"] == 2
    assert not any(k.startswith("_") for m in docs["models"].values() for k in m)
    # each corpus's worked examples come from its own best model: m on Book One, n on the docs
    assert book["examples"][0]["models"][0]["id"] == "ollama/m:1b"
    assert cmp["examples"][0]["models"][0]["id"] == "ollama/n:1b"
    assert {e["id"] for e in cmp["examples"]} <= DOCS_BAND
    # what scored each corpus: the board corpus's fixture in the provenance, the docs' in its entry
    assert page["provenance"]["fixture_sha256"] == script._fixture_queries(FIXTURE).sha256
    assert docs["fixture_sha256"] == script._fixture_queries(DOCS).sha256


def test_rows_of_two_corpora_never_pool_into_one_statistic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard: every statistic refuses rows from two corpora, naming both."""
    _docs_dataset(monkeypatch, tmp_path)
    script = _load_script_per_row()
    rows = script.load_results(_two_corpora(tmp_path))
    book = next(r for r in rows if r.dataset_id.startswith(FIXTURE) and r.model_id.endswith("m:1b"))
    docs = next(r for r in rows if r.dataset_id.startswith(DOCS) and r.model_id.endswith("m:1b"))
    named = rf"2 corpora \({DOCS}, {FIXTURE}\).*never pooled"
    with pytest.raises(SystemExit, match=named):
        script.one_corpus([book, docs], "test")
    items = [(book, [], False), (docs, [], False)]
    with pytest.raises(SystemExit, match=named):
        script._pool_chat_runs(items)
    with pytest.raises(SystemExit, match=named):
        script._rescore(items, script._fixture_queries(FIXTURE))
    pooled = {
        "ollama/m:1b": script._pool_chat_runs([(book, [], False)]),
        "ollama/n:1b": script._pool_chat_runs([(docs, [], False)]),
    }
    with pytest.raises(SystemExit, match=named):
        script._chat_suite(pooled)
    with pytest.raises(SystemExit, match=named):
        script._comparison({("ollama/m:1b", "graphrag"): items}, FIXTURE, [])
    # the export itself never gets there: it splits the rows by corpus first
    grouped = script._chat_runs([tmp_path / "two.json"])
    assert list(grouped) == [DOCS, FIXTURE]
    for corpus, runs in grouped.items():
        assert {script.row_corpus_id(row) for items in runs.values() for row, _, _ in items} == {
            corpus
        }


def test_one_run_per_corpus_even_at_one_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model's rows on two corpora at one timestamp are two runs, never one run's two copies."""
    _docs_dataset(monkeypatch, tmp_path)
    script = _load_script_per_row()
    book = _mode_row("ollama/m:1b", "graphrag", ALL)
    docs = _docs_row("ollama/m:1b", "graphrag", DOCS_ALL)
    assert book["timestamp"] == docs["timestamp"]
    grouped = script._chat_runs([_write(tmp_path / "same.json", [book, docs])])
    assert {c: len(runs[("ollama/m:1b", "graphrag")]) for c, runs in grouped.items()} == {
        FIXTURE: 1,
        DOCS: 1,
    }


def test_a_corpus_without_a_label_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The board names the corpus every number is about, so a dataset with no label stops it."""
    manifest = _docs_dataset(monkeypatch, tmp_path, label=None)
    script = _load_script_per_row()
    chat = _write(tmp_path / "docs.json", _docs_modes("ollama/m:1b", DOCS_ALL, set(), set()))
    with pytest.raises(SystemExit, match=rf"{DOCS}.*no label.*{re.escape(str(manifest))}"):
        _export(script, monkeypatch, tmp_path, "--chat", str(chat))


def test_a_board_of_one_corpus_other_than_book_one_is_the_board_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows of the docs alone: the docs are the board corpus, there is nothing else to report."""
    _docs_dataset(monkeypatch, tmp_path)
    script = _load_script_per_row()
    chat = _write(
        tmp_path / "docs.json",
        [
            *_docs_modes("ollama/m:1b", DOCS_ALL, {"d001"}, {"d001"}),
            *_docs_modes("ollama/n:1b", DOCS_ALL, set(), set()),
        ],
    )
    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat))
    assert [{k: v for k, v in c.items() if k != "last_run"} for c in page["corpora"]] == [
        {"id": DOCS, "label": DOCS_LABEL, "board": True, "review": None}
    ]
    assert page["suites"]["chat"]["fixture"] == DOCS


def test_check_reads_each_corpus_and_refuses_any_other_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--check judges every corpus's fixture and refuses format 1 and any newer one (no legacy)."""
    _docs_dataset(monkeypatch, tmp_path)
    script = _load_script_per_row()
    _export(script, monkeypatch, tmp_path, "--chat", str(_two_corpora(tmp_path)))
    board = json.loads((tmp_path / "page.json").read_text(encoding="utf-8"))
    assert script.check_board(board) == []
    stale = json.loads(json.dumps(board))
    stale["corpora"][1]["fixture_sha256"] = "f" * 64
    assert any(f"{DOCS}" in p and "f" * 64 in p for p in script.check_board(stale))
    # format 1 (no board_format, no corpora): an old layout is unknown, never clean
    v1 = json.loads(json.dumps(board))
    v1.pop("board_format")
    v1.pop("corpora")
    assert any("board format 1" in p for p in script.check_board(v1))
    assert any("board format 1" in p for p in script.provenance_problems(v1))
    for mutate, named in [
        (lambda b: b.update(board_format=3), "board format 3"),
        (lambda b: b["corpora"][0].update(id=DOCS), "board corpus"),
        (lambda b: b["corpora"][1].update(board=True), "board corpus"),
    ]:
        bad = json.loads(json.dumps(board))
        mutate(bad)
        assert any(named in p for p in script.check_board(bad)), named


def test_model_rows_come_from_the_board_corpus_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model's metadata and its place in the table are the board corpus's (and the probes').

    A model run on the docs alone is in the docs' comparison, not in the table or the
    interface picker; a docs row's build never reaches a Book One model's row.
    """
    _docs_dataset(monkeypatch, tmp_path)
    script = _load_script_per_row()
    rows = json.loads(_two_corpora(tmp_path).read_text(encoding="utf-8"))["results"]
    for row in rows:
        if row["dataset_id"].startswith(DOCS):
            row["app_version"] = "9.9.9+docs"
    only_docs = _docs_modes("ollama/gemma4:31b", DOCS_ALL, set(), set())
    chat = _write(tmp_path / "two-more.json", [*rows, *only_docs])

    page, interface = _export(script, monkeypatch, tmp_path, "--chat", str(chat))

    table = {m["id"] for m in page["models"]}
    assert "ollama/gemma4:31b" not in table
    assert "gemma4:31b" not in {m["id"] for m in interface["models"]}
    assert table == {"ollama/qwen3:14b", "ollama/m:1b", "ollama/n:1b", "ollama/gemma4:12b"}
    assert all("9.9.9+docs" not in m["app_versions"] for m in page["models"])
    docs = page["corpora"][1]
    cmp = {m["id"]: m for m in docs["chat"]["comparison"]["models"]}
    assert cmp["ollama/gemma4:31b"]["label"] == "Gemma 4 31B"
    assert docs["chat"]["comparison"]["value"]["models"] == 3


def test_the_board_says_when_its_runs_were_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``last_run``: the newest run behind the table (the board corpus and the probes), per corpus too.

    ``generated`` is when the file was exported; a re-export is not a new run.
    """
    _docs_dataset(monkeypatch, tmp_path)
    script = _load_script_per_row()
    rows = json.loads(_two_corpora(tmp_path).read_text(encoding="utf-8"))["results"]
    for row in rows:
        book = row["dataset_id"].startswith(FIXTURE)
        row["timestamp"] = "2026-10-19T23:00:00+00:00" if book else "2026-10-24T08:00:00+00:00"
    chat = _write(tmp_path / "dated.json", rows)

    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat))

    # the probe row of _export is 2026-09-25, Book One's rows 2026-10-19; the docs' are their own
    assert page["last_run"] == "2026-10-19"
    assert [c["last_run"] for c in page["corpora"]] == ["2026-10-19", "2026-10-24"]
    assert page["generated"] != page["last_run"] or page["generated"] == "2026-10-19"


def test_mixed_corpora_are_refused_even_with_allow_mixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard is no finding an override lifts.

    Rung checks refuse two corpora under --allow-mixed, and an export with
    --allow-mixed still reports each corpus on its own.
    """
    _docs_dataset(monkeypatch, tmp_path)
    script = _load_script_per_row()
    grouped = script._chat_runs([_two_corpora(tmp_path)])
    mixed: dict[tuple[str, str], list[Any]] = {}
    for runs in grouped.values():
        for key, items in runs.items():
            mixed.setdefault(key, []).extend(items)
    with pytest.raises(SystemExit, match=r"2 corpora .*never pooled"):
        script.check_rung_provenance(mixed, allow_mixed=True, allow_unpinned=True)
    page, _ = _export(
        script, monkeypatch, tmp_path, "--chat", str(tmp_path / "two.json"), "--allow-mixed"
    )
    assert [c["id"] for c in page["corpora"]] == [FIXTURE, DOCS]
    assert page["suites"]["chat"]["comparison"]["value"]["models"] == 3
    assert page["corpora"][1]["chat"]["comparison"]["value"]["models"] == 2


def test_drops_and_overflow_warnings_name_their_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _docs_dataset(monkeypatch, tmp_path)
    script = _load_script_per_row()
    errored = _docs_row("ollama/m:1b", "graphrag", DOCS_ALL)
    errored["extras"]["per_query"][0]["error"] = "TimeoutError"
    dropped: list[dict[str, Any]] = []
    script._chat_runs([_write(tmp_path / "e.json", [errored])], drop_errored=True, dropped=dropped)
    assert [d["corpus"] for d in dropped] == [DOCS]
    long = _docs_row("ollama/m:1b", "graphrag", DOCS_ALL)
    long["extras"]["per_query"][0].update({"prompt_tokens": 32000, "output_tokens": 30000})
    runs = script._chat_runs([_write(tmp_path / "l.json", [long])])
    capsys.readouterr()
    script._warn_context_overflows(DOCS, runs[DOCS])
    assert capsys.readouterr().out.startswith(f"WARNING {DOCS} graphrag: ")


# ---------------------------------------------------------------------------
# Under a full hand review the board shows the hand-graded numbers (2026-10-23)
# ---------------------------------------------------------------------------

_DECLINE = "That is not in the sources."
_BAND = {"q082", "q095", "q109", "q124"}


def _value_rows() -> list[dict[str, Any]]:
    """The value-block board: on the scorer every headline but only_with_graph holds."""
    return [
        *_three_modes("ollama/gemma4:12b", graphrag=ALL, hybrid={"q001"}, naive={"q001"}),
        *_three_modes(
            "ollama/gemma4:31b", graphrag=ALL, hybrid={"q001", "q082"}, naive={"q001", "q082"}
        ),
        *_three_modes("ollama/m:1b", graphrag=ALL - {"q124"}, hybrid=set(), naive={"q001"}),
    ]


def _declining_rows() -> list[dict[str, Any]]:
    """The value-block board with the two Gemma rows declining the band with the graph.

    On the scorer errors_removed fails (only the 1B model removes any band failure).
    """
    return [
        *_three_modes("ollama/gemma4:12b", graphrag=ALL - _BAND, hybrid={"q001"}, naive={"q001"}),
        *_three_modes(
            "ollama/gemma4:31b",
            graphrag=ALL - _BAND,
            hybrid={"q001", "q082"},
            naive={"q001", "q082"},
        ),
        *_three_modes("ollama/m:1b", graphrag=ALL - {"q124"}, hybrid=set(), naive={"q001"}),
    ]


def _review_of(
    rows: list[dict[str, Any]],
    fail: set[tuple[str, str, str]] = frozenset(),
    drop: int = 0,
    passes: set[tuple[str, str, str]] = frozenset(),
) -> dict[str, Any]:
    """A hand review of every answer of ``rows``: the scorer's verdict, except ``fail`` keys FAIL.

    ``fail`` and ``passes`` hold ``(model id, rung, question)`` the reviewers fail or pass
    against the scorer; ``drop`` leaves that many answers unreviewed.
    """
    verdicts = []
    for row in rows:
        mode = row["extras"]["retrieval"]
        for record in row["extras"]["per_query"]:
            qid, answer = record["query_id"], record["answer"]
            key = (row["model_id"], mode, qid)
            ok = key in passes or (not answer.startswith(_DECLINE) and key not in fail)
            verdicts.append(
                {
                    "key": f"{qid}|{row['model_label']}|{mode}",
                    "answer_start": answer[:90],
                    "should_be": "PASS" if ok else "FAIL",
                    "reason": "",
                }
            )
    return {"verdicts": verdicts[drop:]}


_REVIEWERS_FAIL_THE_BAND = {
    (mid, "graphrag", qid) for mid in ("ollama/gemma4:12b", "ollama/gemma4:31b") for qid in _BAND
}
"""The reviewers fail every graph-shaped answer two of the three models give with the graph."""


def _reviewed_export(
    script: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    review: dict[str, Any],
    name: str = "2026-10-23-book-review-verdicts.json",
    rows: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], Path]:
    reviews_dir = tmp_path / "runs"
    reviews_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(script, "REVIEWS_DIR", reviews_dir)
    path = reviews_dir / name
    path.write_text(json.dumps(review), encoding="utf-8")
    chat = _write(tmp_path / "c.json", _value_rows() if rows is None else rows)
    page, _ = _export(
        script, monkeypatch, tmp_path, "--chat", str(chat), "--reviews", f"{FIXTURE}={path}"
    )
    return page, path


def _comparison_of(page: dict[str, Any]) -> dict[str, Any]:
    return page["suites"]["chat"]["comparison"]


def _headline_map(page: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {h["key"]: h for h in _comparison_of(page)["value"]["headlines"]}


def _model_rows(cmp: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["id"]: m for m in cmp["models"]}


def test_under_a_full_review_the_board_shows_the_hand_graded_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the scorer errors_removed holds (3 of 3 models); hand-graded 1 of 3, which the board shows."""
    import hashlib

    script = _load_script()
    chat = _write(tmp_path / "c.json", _value_rows())
    plain, _ = _export(script, monkeypatch, tmp_path, "--chat", str(chat))
    # no review: the corpus says so, and nothing carries a second reading
    assert plain["corpora"][0]["review"] is None
    assert "scorer" not in _comparison_of(plain)
    assert not any("scorer" in h["values"] for h in _headline_map(plain).values())

    page, path = _reviewed_export(
        script, monkeypatch, tmp_path, _review_of(_value_rows(), _REVIEWERS_FAIL_THE_BAND)
    )

    # 3 models x 3 rungs x 6 questions, every one matched
    assert page["corpora"][0]["review"] == {
        "answers": 54,
        "matched": 54,
        "full": True,
        "file": path.name,
        "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    heads, scorer = _headline_map(page), _headline_map(plain)
    removed = heads["errors_removed"]
    # the text and the values are the hand-graded reading; the scorer's moves to values.scorer
    assert (
        removed["values"]["models_above_bar_naive"],
        removed["values"]["models_above_bar_hybrid"],
    ) == (1, 1)
    assert "1 of 3 remove at least" in removed["text"]
    assert removed["text"] != scorer["errors_removed"]["text"]
    assert removed["values"]["scorer"] == {"holds": True, **scorer["errors_removed"]["values"]}
    # holds follows the hand-graded reading alone; held_back says why the scorer's would not show
    assert removed["holds"] is False
    assert "1 of 3" in removed["held_back"] and "hand-graded" in removed["held_back"]
    assert "wrong answers" not in removed["held_back"]
    # the ordinary questions and the size pair hold on both readings
    for key in ("failures_ordinary", "size_substitution"):
        assert heads[key]["holds"] is True, key
        assert heads[key]["values"]["scorer"]["holds"] is True, key
        assert "held_back" not in heads[key]
    assert heads["only_with_graph"]["holds"] is False
    assert "held_back" not in heads["only_with_graph"]  # it fails on the scorer already
    # no headline carries the earlier rule's second reading
    assert not any("review" in h["values"] for h in heads.values())

    # the whole comparison block follows the review: the value block, the per-model rows,
    # their lifts and the worked examples, so no figure disagrees with the table under it
    cmp, before = _comparison_of(page), _comparison_of(plain)
    value = cmp["value"]
    assert value["errors_removed"]["graph_advantage"]["hybrid"]["models_above_bar"] == 1
    assert "scorer" not in value["size_substitution"]["best"]
    assert not any("scorer" in pair for pair in value["size_substitution"]["pairs"])
    rows, rows_before = _model_rows(cmp), _model_rows(before)
    for mid in ("ollama/gemma4:12b", "ollama/gemma4:31b"):
        band = rows[mid]["modes"]["graphrag"]["graph_advantage"]
        assert band["passed"] == 0, mid
        assert rows_before[mid]["modes"]["graphrag"]["graph_advantage"]["passed"] == 4, mid
        lift = rows[mid]["lift"]["graph_advantage"]["naive"]["value"]
        assert lift < rows_before[mid]["lift"]["graph_advantage"]["naive"]["value"], mid
        # citations are the scorer's check either way: the reviewers grade the verdict only
        assert (
            rows[mid]["modes"]["graphrag"]["citation_accuracy"]
            == rows_before[mid]["modes"]["graphrag"]["citation_accuracy"]
        )
    assert rows["ollama/m:1b"] == rows_before["ollama/m:1b"]
    assert cmp["examples"]
    for example in cmp["examples"]:
        for model in example["models"]:
            rung = model["rungs"]["graphrag"]
            if (model["id"], "graphrag", example["id"]) in _REVIEWERS_FAIL_THE_BAND:
                assert rung["passed"] is False, (model["id"], example["id"])
    # the scorer's comparison block is kept for provenance, as a plain export writes it
    assert cmp["scorer"] == {"models": before["models"], "value": before["value"]}


def test_under_a_full_review_a_headline_the_scorer_misses_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review is the more accurate reading: a headline holds on it alone."""
    script = _load_script()
    rows = _declining_rows()
    passes = {
        (mid, "graphrag", qid)
        for mid in ("ollama/gemma4:12b", "ollama/gemma4:31b")
        for qid in _BAND
    }
    page, _ = _reviewed_export(
        script, monkeypatch, tmp_path, _review_of(rows, passes=passes), rows=rows
    )
    removed = _headline_map(page)["errors_removed"]
    assert removed["values"]["scorer"]["holds"] is False
    assert removed["holds"] is True
    assert "held_back" not in removed
    assert removed["values"]["models_above_bar_hybrid"] == 3
    assert script.check_board(page) == []


def test_a_review_short_of_every_answer_stops_the_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A review must match every comparison answer; short of that nothing is written."""
    script = _load_script()
    with pytest.raises(SystemExit) as exc:
        _reviewed_export(
            script,
            monkeypatch,
            tmp_path,
            _review_of(_value_rows(), _REVIEWERS_FAIL_THE_BAND, drop=1),
        )
    message = str(exc.value.code)
    assert FIXTURE in message and "53 of 54" in message
    assert "every" in message
    # it names an answer the review does not cover
    assert "q001|" in message
    assert not (tmp_path / "page.json").exists()


def test_a_review_names_a_measured_corpus_and_an_archived_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _load_script()
    reviews_dir = tmp_path / "runs"
    reviews_dir.mkdir()
    monkeypatch.setattr(script, "REVIEWS_DIR", reviews_dir)
    chat = _write(tmp_path / "c.json", _value_rows())
    archived = reviews_dir / "r-verdicts.json"
    archived.write_text(json.dumps(_review_of(_value_rows())), encoding="utf-8")
    stray = tmp_path / "r-verdicts.json"
    stray.write_text(archived.read_text(encoding="utf-8"), encoding="utf-8")
    for spec, named in (
        (f"no_such_corpus={archived}", "no_such_corpus"),
        (f"{FIXTURE}={stray}", str(reviews_dir)),
    ):
        with pytest.raises(SystemExit) as exc:
            _export(script, monkeypatch, tmp_path, "--chat", str(chat), "--reviews", spec)
        assert named in str(exc.value.code), spec


def test_check_verifies_the_review_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--check: the review file's content, its coverage, and the two readings of every headline."""
    script = _load_script()
    page, path = _reviewed_export(
        script, monkeypatch, tmp_path, _review_of(_value_rows(), _REVIEWERS_FAIL_THE_BAND)
    )
    assert script.check_board(page) == []

    def headline(board: dict[str, Any], key: str) -> dict[str, Any]:
        return _headline_map(board)[key]

    def earlier_rule(board: dict[str, Any]) -> None:
        # the board as the first review rule wrote it: the scorer's text, values.review beside it
        h = headline(board, "failures_ordinary")
        h["values"]["review"] = h["values"].pop("scorer")

    for mutate, named in [
        (lambda b: b["corpora"][0]["review"].update(matched=53), "53 of 54"),
        (lambda b: b["corpora"][0]["review"].update(file_sha256="f" * 64), "f" * 64),
        (lambda b: b["corpora"][0]["review"].update(file="gone-verdicts.json"), "gone-verdicts"),
        (lambda b: headline(b, "errors_removed").pop("held_back"), "errors_removed"),
        (lambda b: headline(b, "size_substitution").update(held_back="x"), "size_substitution"),
        (lambda b: headline(b, "failures_ordinary")["values"].pop("scorer"), "failures_ordinary"),
        (
            lambda b: headline(b, "failures_ordinary")["values"]["scorer"].update(median_pct=1.0),
            "failures_ordinary",
        ),
        (earlier_rule, "failures_ordinary"),
        (lambda b: _comparison_of(b).pop("scorer"), "scorer"),
        (lambda b: b["corpora"][0].update(review=None), "review"),
        (lambda b: b["corpora"][0].pop("review"), "review"),
    ]:
        bad = json.loads(json.dumps(page))
        mutate(bad)
        problems = script.check_board(bad)
        assert any(named in p for p in problems), (named, problems)
    # the file itself changed since the export
    path.write_text(json.dumps(_review_of(_value_rows())), encoding="utf-8")
    assert any(path.name in p and "sha256" in p for p in script.check_board(page))
