# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""scripts/benchmark/export_leaderboard.py: harness rows apart, grounded-chat board."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest


SCRIPT = Path(__file__).resolve().parents[5] / "scripts" / "benchmark" / "export_leaderboard.py"


def _load_script() -> Any:
    """Import the script as a module without running main()."""
    spec = importlib.util.spec_from_file_location("export_leaderboard_script", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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


def _chat_row(
    model_id: str,
    failed: list[str],
    *,
    success: bool = True,
    error: str | None = None,
    unsupported: tuple[str, ...] = (),
) -> dict[str, Any]:
    """One grounded-chat row over five questions; ``failed`` ids fail."""
    ids = ["q1", "q2", "q3", "q4", "q5"]
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
            "dataset_id": f"{FIXTURE}__chat__{model_id}__ollama_qwen3-embedding:0.6b",
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
        "minutes": 3.0,
        "truncated": 2,
        "timed_out": False,
        "thinking": True,
        "unsupported_names": 1,
        "verdicts": {"q1": True, "q2": False, "q3": True, "q4": True, "q5": False},
    }
    suite = page["suites"]["chat"]
    assert (suite["total"], suite["fixture"], suite["retrieval_floor"]) == (5, FIXTURE, None)
    assert suite["retrieval_floor_ids"] == []
    # The fixture's questions travel with the board: id, section, tier and text, never answer terms.
    assert len(suite["questions"]) == 80
    assert set(suite["questions"][0]) == {"id", "section", "tier", "question"}
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

    page, interface = _export(script, monkeypatch, tmp_path, "--chat", str(chat))

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
    """q2 and q4 fail for both scored models; a later file overrides an earlier row."""
    script = _load_script()
    first = _write(
        tmp_path / "first.json", [_chat_row("ollama/qwen3:14b", ["q1", "q2", "q3", "q4", "q5"])]
    )
    second = _write(
        tmp_path / "second.json",
        [
            _chat_row("ollama/qwen3:14b", ["q2", "q4", "q5"]),
            _chat_row("ollama/gemma4:31b", ["q1", "q2", "q4"]),
            # a timed-out model has no verdicts and does not lower the floor
            _chat_row("ollama/slow:70b", [], success=False, error="TimeoutError"),
        ],
    )

    page, _ = _export(script, monkeypatch, tmp_path, "--chat", str(first), str(second))

    assert page["suites"]["chat"]["retrieval_floor"] == 2
    assert page["suites"]["chat"]["retrieval_floor_ids"] == ["q2", "q4"]
    by_id = {m["id"]: m for m in page["models"]}
    assert by_id["ollama/qwen3:14b"]["chat"]["passed"] == 2


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

    page, interface = _export(script, monkeypatch, tmp_path, "--chat", str(chat))

    by_id = {m["id"]: m["scores"] for m in page["models"]}
    # extraction = 2/3 * 100 + 1/3 * 50 = 83.3; overall = 0.6 * 83.3 + 0.4 * 80 = 82.0
    assert by_id["ollama/qwen3:14b"] == {"extraction": 83.3, "chat": 80.0, "overall": 82.0}
    assert by_id["ollama/gemma4:31b"] == {"extraction": None, "chat": 80.0, "overall": None}
    # a chat run that did not finish scores 0 there, so it still gets an overall
    assert by_id["ollama/phi4:14b"] == {"extraction": None, "chat": 0.0, "overall": None}
    assert page["score_weights"] == {
        "extraction": {"carrier": 2 / 3, "native": 1 / 3},
        "overall": {"extraction": 0.6, "chat": 0.4},
    }
    picker = {m["id"]: m["scores"] for m in interface["models"]}
    assert picker["qwen3:14b"] == {"extraction": 83.3, "chat": 80.0, "overall": 82.0}
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
