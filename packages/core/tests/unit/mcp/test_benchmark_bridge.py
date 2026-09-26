# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""BenchmarkBridge: an MCP client answers the extraction probes itself.

Most tests use a three-probe fixture written to a temp directory; one test
checks the suite sizes of the shipped fixture. The parity tests pin that the
bridge sends the pipeline's own prompts and builds the same per-probe record
``extract_single_chunk`` would from the same model answers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.benchmark.probes import prepare_probe_passage, probe_node_templates
from chaoscypher_core.benchmark.results import load_results
from chaoscypher_core.mcp.benchmark import PROBE_SEED, BenchmarkBridge
from chaoscypher_core.settings import EngineSettings, PathSettings


_SECTION_A = """
section: A
probes:
  - id: T1-easy
    probe: T1
    tier: easy
    instruction: Extract both commanders.
    passage: |
      Kutuzov commanded the Russian army in 1812. Bagration led the Second Army under Kutuzov.
    checks:
      - {type: finish_stop}
      - {type: entities_present, names: [Kutuzov, Bagration]}
      - {type: relationship_count, min: 1}
  - id: T2-hard
    probe: T2
    tier: hard
    instruction: Extract nothing from a passage without names.
    passage: |
      The rain had not stopped since morning. The road beyond the gate was mud.
    checks:
      - {type: finish_stop}
      - {type: entity_count, max: 0}
"""

_SECTION_H = """
section: H
probes:
  - id: T1-easy-carrier
    probe: T1
    tier: hard
    instruction: Extract both commanders inside a chunk.
    carrier: carriers/c.txt
    carrier_cast: [Anna]
    passage: |
      Kutuzov commanded the Russian army in 1812. Bagration led the Second Army under Kutuzov.
    checks:
      - {type: finish_stop}
      - {type: entities_present, names: [Kutuzov, Bagration]}
"""


def _entities(first: int = 1) -> str:
    """A correct pass-1 answer when the T1 passage starts at sentence ``first``."""
    return (
        f"E|Kutuzov|Character||1.0|S{first}|Kutuzov commanded the Russian army during "
        "the campaign of 1812 against the French.\n"
        f"E|Bagration|Character||1.0|S{first + 1}|Bagration led the Second Army of the "
        "Russians under the command of Kutuzov.\n"
    )


def _relationships(first: int = 1) -> str:
    """A correct pass-2 answer when the T1 passage starts at sentence ``first``."""
    return f"R|0|1|commands|1.0|S{first + 1}|Bagration led the Second Army under Kutuzov.\n"


_ENTITIES = _entities()
_RELATIONSHIPS = _relationships()


def _pack(tmp_path: Path) -> Path:
    """Write a tiny probe fixture (two isolated probes, one carrier probe)."""
    pack = tmp_path / "pack"
    (pack / "carriers").mkdir(parents=True)
    (pack / "manifest.yaml").write_text(
        "id: probes\nkind: probes\nversion: '0.1'\ndomain: literary\nsections: [A.yaml, H.yaml]\n",
        encoding="utf-8",
    )
    (pack / "A.yaml").write_text(_SECTION_A, encoding="utf-8")
    (pack / "H.yaml").write_text(_SECTION_H, encoding="utf-8")
    (pack / "carriers" / "c.txt").write_text(
        "Anna received her guests in the drawing room. The evening was cold. "
        "The guests spoke of the war. Anna poured the tea.",
        encoding="utf-8",
    )
    return pack


def _settings(tmp_path: Path) -> EngineSettings:
    """Engine settings whose data dir is a temp directory."""
    return EngineSettings(paths=PathSettings(data_dir=str(tmp_path / "data")))


@pytest.fixture
def bridge(tmp_path: Path) -> BenchmarkBridge:
    """A bridge over the tiny fixture with a temp data dir."""
    return BenchmarkBridge(_settings(tmp_path), pack_dir=_pack(tmp_path))


def _state(b: BenchmarkBridge, run_id: str) -> dict[str, Any]:
    """The run's state file as saved."""
    state: dict[str, Any] = json.loads((b.state_dir / f"{run_id}.json").read_text())
    return state


def _verdict(b: BenchmarkBridge, run_id: str, task_id: str) -> dict[str, Any]:
    """A completed task's verdict, from the state file (never from a tool response)."""
    verdict: dict[str, Any] = _state(b, run_id)["verdicts"][task_id]
    return verdict


async def _answer_t1(b: BenchmarkBridge, run_id: str, probe_id: str) -> dict[str, Any]:
    """Submit both stages of a T1 probe with a correct answer."""
    first = await b.submit(run_id, probe_id, "entities", _ENTITIES)
    assert first["success"], first
    return await b.submit(run_id, probe_id, "relationships", _RELATIONSHIPS)


@pytest.mark.asyncio
async def test_start_on_the_shipped_fixture_sizes_each_suite(tmp_path: Path) -> None:
    """The shipped fixture has 65 isolated probes and 21 carrier probes."""
    b = BenchmarkBridge(_settings(tmp_path))
    sizes = {}
    for suite in ("probes", "probes-carrier", "probes-all"):
        res = await b.start(suite, "claude-code", "test-model")
        assert res["success"], res
        sizes[suite] = res["total"]
    assert sizes == {"probes": 65, "probes-carrier": 21, "probes-all": 86}


@pytest.mark.asyncio
async def test_full_run_scores_each_probe_and_writes_a_harness_track_results_file(
    bridge: BenchmarkBridge,
) -> None:
    """Start -> entities -> relationships -> verdict ... -> finish -> results file."""
    started = await bridge.start("probes", "claude-code", "claude-test")
    assert started["success"] and started["total"] == 2
    run_id = started["run_id"]
    task = started["next"]
    assert task["probe_id"] == "T1-easy" and task["stage"] == "entities"
    assert task["remaining"] == 2

    res = await bridge.submit(run_id, "T1-easy", "entities", _ENTITIES)
    assert res["success"] and res["accepted"] and "verdict" not in res
    assert len(_state(bridge, run_id)["stages"]["T1-easy"]["pass1_entities"]) == 2
    nxt = res["next"]
    assert nxt["probe_id"] == "T1-easy" and nxt["stage"] == "relationships"
    # Pass 2 lists the entities parsed from the submitted pass-1 answer.
    assert "0: Kutuzov (Character)" in nxt["user_prompt"]
    assert "1: Bagration (Character)" in nxt["user_prompt"]

    res = await bridge.submit(run_id, "T1-easy", "relationships", _RELATIONSHIPS)
    assert res["success"], res
    assert "verdict" not in res
    assert _verdict(bridge, run_id, "T1-easy")["passed"]
    assert res["next"]["probe_id"] == "T2-hard"

    # A passage with no names: an empty pass 1 is the correct answer, and the
    # pipeline skips pass 2, so the probe completes on the entities stage.
    res = await bridge.submit(run_id, "T2-hard", "entities", "", empty_answer=True)
    assert res["success"], res
    assert _verdict(bridge, run_id, "T2-hard")["passed"]
    assert res["next"] == {"done": True, "submitted": 2, "total": 2}

    done = await bridge.finish(run_id)
    assert done["success"], done
    assert done["probes_passed"] == 2 and done["probes_total"] == 2
    rows = load_results(Path(done["results_path"]))
    assert len(rows) == 1
    row = rows[0]
    assert row.pins_applied is False
    assert row.harness == "mcp:claude-code"
    assert row.model_id == "mcp/claude-code/claude-test"
    assert row.model_label == "claude-test via claude-code (MCP)"
    assert row.dataset_kind == "probes" and row.config_name == "probes"
    assert row.temperature is None and row.thinking is None
    assert row.metrics["probes_total"] == 2
    assert row.extras["selected_ids"] == ["T1-easy", "T2-hard"]
    assert [v["passed"] for v in row.metrics["verdicts"]] == [True, True]
    run = row.extras["run"]
    assert run["task_count"] == 2 and run["resubmissions_refused"] == 0
    assert run["started_at"] and run["finished_at"]
    assert {r["id"] for r in row.extras["probes"]} == {"T1-easy", "T2-hard"}


@pytest.mark.asyncio
async def test_a_new_bridge_instance_resumes_a_run(tmp_path: Path) -> None:
    """State lives on disk: a fresh bridge continues where the last one stopped."""
    pack = _pack(tmp_path)
    first = BenchmarkBridge(_settings(tmp_path), pack_dir=pack)
    run_id = (await first.start("probes", "cursor", "m1"))["run_id"]
    await first.submit(run_id, "T1-easy", "entities", _ENTITIES)

    second = BenchmarkBridge(_settings(tmp_path), pack_dir=pack)
    task = await second.next_task(run_id)
    assert task["success"] and task["probe_id"] == "T1-easy"
    assert task["stage"] == "relationships"
    res = await second.submit(run_id, "T1-easy", "relationships", _RELATIONSHIPS)
    assert res["success"] and _verdict(second, run_id, "T1-easy")["passed"]
    progress = await first.progress(run_id)
    assert progress["submitted"] == 1 and "passed" not in progress
    assert progress["pending"] == [
        {"task_id": "T2-hard", "probe_id": "T2-hard", "stage": "entities"}
    ]


@pytest.mark.asyncio
async def test_an_answered_stage_is_final_and_refusals_are_counted(
    bridge: BenchmarkBridge,
) -> None:
    """A run scores the first answer: no stage or completed task can be re-submitted."""
    run_id = (await bridge.start("probes", "claude-code", "m"))["run_id"]
    await bridge.submit(run_id, "T1-easy", "entities", _ENTITIES)
    # Pass 2 lists the entities parsed from pass 1; revising pass 1 is refused.
    res = await bridge.submit(run_id, "T1-easy", "entities", _entities())
    assert res["error_code"] == "TASK_FINAL"
    await bridge.submit(run_id, "T1-easy", "relationships", _RELATIONSHIPS)
    for stage, text in (("relationships", _RELATIONSHIPS), ("entities", _ENTITIES)):
        res = await bridge.submit(run_id, "T1-easy", stage, text)
        assert res["error_code"] == "TASK_FINAL", res
    assert _state(bridge, run_id)["resubmissions_refused"] == 3
    assert (await bridge.progress(run_id))["submitted"] == 1

    done = await bridge.finish(run_id, allow_incomplete=True)
    (row,) = load_results(Path(done["results_path"]))
    assert row.extras["run"]["resubmissions_refused"] == 3


@pytest.mark.asyncio
async def test_a_finished_run_takes_no_more_answers(bridge: BenchmarkBridge) -> None:
    """Finishing closes the run; a later finish still rewrites the file."""
    run_id = (await bridge.start("probes", "claude-code", "m"))["run_id"]
    await _answer_t1(bridge, run_id, "T1-easy")
    first = await bridge.finish(run_id, allow_incomplete=True)
    assert first["success"]
    res = await bridge.submit(run_id, "T2-hard", "entities", "", empty_answer=True)
    assert res["error_code"] == "RUN_FINISHED"
    again = await bridge.finish(run_id, allow_incomplete=True)
    assert again["success"] and again["results_path"] == first["results_path"]


@pytest.mark.asyncio
async def test_out_of_order_and_bad_submissions_are_errors_not_exceptions(
    bridge: BenchmarkBridge,
) -> None:
    """Relationships before entities, unknown probe/run, empty text."""
    run_id = (await bridge.start("probes", "claude-code", "m"))["run_id"]

    res = await bridge.submit(run_id, "T1-easy", "relationships", _RELATIONSHIPS)
    assert res == {**res, "success": False, "error_code": "OUT_OF_ORDER"}

    res = await bridge.submit(run_id, "T1-easy-carrier", "entities", _ENTITIES)
    assert res["error_code"] == "UNKNOWN_PROBE"  # carrier probe is not in this suite

    res = await bridge.submit(run_id, "T1-easy", "entities", "   \n")
    assert res["error_code"] == "EMPTY_OUTPUT"

    res = await bridge.submit(run_id, "T1-easy", "pass3", _ENTITIES)
    assert res["error_code"] == "INVALID_ARGUMENT"

    # T2's pass 1 keeps no entity, so the probe is complete after it: no pass 2.
    await bridge.submit(run_id, "T2-hard", "entities", "", empty_answer=True)
    res = await bridge.submit(run_id, "T2-hard", "relationships", _RELATIONSHIPS)
    assert res["error_code"] == "TASK_FINAL"

    for bad in ("0123456789ab", "../../etc/passwd"):
        res = await bridge.next_task(bad)
        assert res["success"] is False and res["error_code"] == "UNKNOWN_RUN"

    assert (await bridge.start("nope", "c", "m"))["error_code"] == "UNKNOWN_SUITE"
    assert (await bridge.start("probes", "bad client/..", "m"))["error_code"] == (
        "INVALID_ARGUMENT"
    )


@pytest.mark.asyncio
async def test_client_settings_are_written_on_the_results_row(bridge: BenchmarkBridge) -> None:
    """What the client reports about its effort/thinking lands on the row as-is."""
    settings = {"effort": "high", "thinking": "adaptive", "client_version": "2.3.1", "budget": 8}
    started = await bridge.start("probes", "claude-code", "m", client_settings=settings)
    assert started["success"], started
    run_id = started["run_id"]
    await _answer_t1(bridge, run_id, "T1-easy")
    await bridge.submit(run_id, "T2-hard", "entities", "", empty_answer=True)

    done = await bridge.finish(run_id)
    assert done["success"], done
    (row,) = load_results(Path(done["results_path"]))
    assert row.harness_settings == settings


@pytest.mark.asyncio
async def test_a_run_without_client_settings_writes_none(bridge: BenchmarkBridge) -> None:
    """No settings reported means None on the row, never an empty guess."""
    run_id = (await bridge.start("probes", "claude-code", "m"))["run_id"]
    done = await bridge.finish(run_id, allow_incomplete=True)
    (row,) = load_results(Path(done["results_path"]))
    assert row.harness_settings is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "effort=high",
        {"effort": {"level": "high"}},
        {"effort": ["high"]},
        {"effort": None},
        {"effort": ""},
        {"effort": "x" * 81},
        {"bad key": "high"},
        {"": "high"},
        {f"k{i}": i for i in range(21)},
        {"budget": float("nan")},
    ],
)
async def test_client_settings_must_be_a_small_flat_map(bridge: BenchmarkBridge, bad: Any) -> None:
    """Nested, null, oversized or non-object settings are rejected before a run exists."""
    res = await bridge.start("probes", "claude-code", "m", client_settings=bad)
    assert res["success"] is False and res["error_code"] == "INVALID_ARGUMENT", res
    assert not bridge.state_dir.exists() or not list(bridge.state_dir.iterdir())


@pytest.mark.asyncio
async def test_finish_refuses_a_partial_run_unless_asked(bridge: BenchmarkBridge) -> None:
    """Unanswered probes block finish, or count as fails with allow_incomplete."""
    run_id = (await bridge.start("probes", "claude-code", "m"))["run_id"]
    await _answer_t1(bridge, run_id, "T1-easy")
    assert (await bridge.finish(run_id))["error_code"] == "INCOMPLETE_RUN"
    done = await bridge.finish(run_id, allow_incomplete=True)
    assert done["success"] and done["probes_passed"] == 1 and done["probes_total"] == 2
    assert done["passed"] == 1 and done["total"] == 2


@pytest.mark.asyncio
async def test_entity_prompt_is_byte_identical_to_the_pipeline_rendering(
    bridge: BenchmarkBridge,
) -> None:
    """The client gets exactly the system and pass-1 prompts a local run sends."""
    run_id = (await bridge.start("probes-carrier", "claude-code", "m"))["run_id"]
    task = await bridge.next_task(run_id)
    suite = bridge.suite("probes-carrier")
    probe = suite._load_fixture()[1]["T1-easy-carrier"]
    passage, offset = prepare_probe_passage(probe, bridge.pack_dir, seed=PROBE_SEED)
    assert offset >= 2 and "Anna poured the tea." in passage  # spliced into the carrier
    templates = suite._get_templates()
    expected = suite._get_extractor().render_harvest_prompts(
        passage,
        probe_node_templates(probe, templates["node_templates"]),
        entity_examples=templates.get("entity_examples"),
        entity_exclusions=None,
        strict_entity_types=probe.strict_types,
    )
    assert task["user_prompt"] == expected.entity_prompt
    assert task["system_prompt"] == expected.system_prompt


@pytest.mark.asyncio
async def test_record_matches_what_extract_single_chunk_produces(
    bridge: BenchmarkBridge,
) -> None:
    """Same answers in, same record out as the local runner's LLM path."""
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        CallLLMResult,
    )

    suite = bridge.suite("probes-carrier")
    probe = suite._load_fixture()[1]["T1-easy-carrier"]
    passage, offset = prepare_probe_passage(probe, bridge.pack_dir, seed=PROBE_SEED)
    ents, rels = _entities(offset), _relationships(offset)
    run_id = (await bridge.start("probes-carrier", "claude-code", "m"))["run_id"]
    await bridge.submit(run_id, "T1-easy-carrier", "entities", ents)
    res = await bridge.submit(run_id, "T1-easy-carrier", "relationships", rels)
    assert res["success"], res
    state = json.loads((bridge.state_dir / f"{run_id}.json").read_text())
    rec = state["records"]["T1-easy-carrier"]
    assert len(rec["entities"]) == 2 and len(rec["relationships"]) == 1

    prompts_seen: list[str] = []
    answers = iter([ents, rels])
    extractor = suite._get_extractor()

    async def _fake_call_llm(prompt: str, **_: Any) -> CallLLMResult:
        """Return the canned answers in pass order and record the prompts."""
        prompts_seen.append(prompt)
        return CallLLMResult(
            content=next(answers),
            input_tokens=1,
            output_tokens=1,
            finish_reason="stop",
            aborted_by_loop=False,
        )

    extractor.call_llm = _fake_call_llm  # type: ignore[method-assign]
    templates = suite._get_templates()
    entities, relationships, _in, _out, metrics = await extractor.extract_single_chunk(
        chunk_content=passage,
        node_templates_formatted=probe_node_templates(probe, templates["node_templates"]),
        edge_templates_formatted=templates["edge_templates"],
        entity_examples=templates.get("entity_examples"),
        relationship_examples=templates.get("relationship_examples"),
        entity_exclusions=None,
        strict_entity_types=probe.strict_types,
        valid_entity_type_names=None,
    )
    assert json.loads(json.dumps(entities)) == rec["entities"]
    assert json.loads(json.dumps(relationships)) == rec["relationships"]
    assert metrics["parser_lines_dropped"] == rec["parser_lines_dropped"]
    assert metrics["invalid_relationship_count"] == rec["invalid_relationship_count"]
    assert metrics["raw_llm_response"] == rec["raw_llm_response"]
    assert metrics["sentences"] == rec["sentences"]
    assert metrics["_prompt_data"]["relationship_instructions"] == rec["relationship_instructions"]
    assert rec["finish_reason"] == "stop" and rec["aborted_by_loop"] is False
    # And the pass-2 prompt the client saw is the one the pipeline sent.
    rel_prompt = suite._relationship_prompt(probe, entities)
    assert prompts_seen[1] == rel_prompt


@pytest.mark.asyncio
async def test_a_runaway_answer_is_cut_by_the_loop_detector(bridge: BenchmarkBridge) -> None:
    """An answer the stream guard would abort fails the completion gate."""
    run_id = (await bridge.start("probes", "claude-code", "m"))["run_id"]
    runaway = "".join(
        f"E|Kutuzov {i}|Character||1.0|S1|Kutuzov commanded the Russian army in 1812.\n"
        for i in range(400)
    )
    await bridge.submit(run_id, "T1-easy", "entities", runaway)
    # The detector stops reading at the entity cap, as the stream would.
    assert 0 < len(_state(bridge, run_id)["stages"]["T1-easy"]["pass1_entities"]) < 400
    await bridge.submit(run_id, "T1-easy", "relationships", _RELATIONSHIPS)
    verdict = _verdict(bridge, run_id, "T1-easy")
    assert verdict["passed"] is False
    assert any("did not complete" in d for d in verdict["details"])
    state = _state(bridge, run_id)
    rec = state["records"]["T1-easy"]
    assert rec["aborted_by_loop"] is True
    assert rec["finish_reason"] == "unknown"


@pytest.mark.asyncio
async def test_entity_pass_is_computed_once_and_verdicts_once_per_task(
    bridge: BenchmarkBridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pass 1 is replayed/parsed once per probe; each completed probe is scored once.

    It used to be recomputed by every next_task, both submit branches and the
    record build.
    """
    entity_passes = 0
    verdicts = 0
    suite = bridge.suite("probes")
    real_entity_pass = suite._entity_pass
    real_verdict = suite.verdict

    async def counting_entity_pass(*args: Any, **kwargs: Any) -> Any:
        """Count pass-1 computations."""
        nonlocal entity_passes
        entity_passes += 1
        return await real_entity_pass(*args, **kwargs)

    def counting_verdict(*args: Any, **kwargs: Any) -> Any:
        """Count probe scorings."""
        nonlocal verdicts
        verdicts += 1
        return real_verdict(*args, **kwargs)

    monkeypatch.setattr(suite, "_entity_pass", counting_entity_pass)
    monkeypatch.setattr(suite, "verdict", counting_verdict)

    run_id = (await bridge.start("probes", "claude-code", "m"))["run_id"]
    first = await bridge.submit(run_id, "T1-easy", "entities", _ENTITIES)
    assert first["next"]["stage"] == "relationships"
    assert entity_passes == 1
    assert (await bridge.next_task(run_id))["stage"] == "relationships"
    res = await bridge.submit(run_id, "T1-easy", "relationships", _RELATIONSHIPS)
    assert res["success"] and _verdict(bridge, run_id, "T1-easy")["passed"]
    assert entity_passes == 1
    assert verdicts == 1

    for _ in range(3):
        assert (await bridge.progress(run_id))["submitted"] == 1
    assert verdicts == 1


@pytest.mark.asyncio
async def test_task_id_and_the_deprecated_probe_id_name_the_same_task(
    bridge: BenchmarkBridge,
) -> None:
    """Tasks carry both names; submit takes either, and refuses two different ones."""
    started = await bridge.start("probes", "claude-code", "m")
    run_id, task = started["run_id"], started["next"]
    assert task["task_id"] == task["probe_id"] == "T1-easy"
    assert task["kind"] == "probes" and task["section"] == "A" and task["tier"] == "easy"
    assert "pipe-format" in task["answer_format"]

    res = await bridge.submit(run_id, stage="entities", output_text=_ENTITIES, probe_id="T1-easy")
    assert res["success"] and res["task_id"] == "T1-easy", res
    res = await bridge.submit(
        run_id, "T1-easy", "relationships", _RELATIONSHIPS, probe_id="T2-hard"
    )
    assert res["error_code"] == "INVALID_ARGUMENT"
    res = await bridge.submit(run_id, stage="relationships", output_text=_RELATIONSHIPS)
    assert res["error_code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_a_run_saved_before_suites_existed_resumes(bridge: BenchmarkBridge) -> None:
    """Old state files carry probe_ids and no reference; they load as task_ids."""
    run_id = (await bridge.start("probes", "claude-code", "m"))["run_id"]
    path = bridge.state_dir / f"{run_id}.json"
    state = json.loads(path.read_text())
    state["probe_ids"] = state.pop("task_ids")
    del state["reference"]
    path.write_text(json.dumps(state))

    await _answer_t1(bridge, run_id, "T1-easy")
    progress = await bridge.progress(run_id)
    assert progress["success"] and progress["submitted"] == 1 and progress["total"] == 2
    saved = json.loads(path.read_text())
    assert saved["task_ids"] == ["T1-easy", "T2-hard"] and "probe_ids" not in saved


@pytest.mark.asyncio
async def test_a_probe_suite_takes_no_reference_pack(bridge: BenchmarkBridge) -> None:
    """Only a suite that reads a reference pack accepts one."""
    res = await bridge.start("probes", "claude-code", "m", reference="book")
    assert res["success"] is False and res["error_code"] == "INVALID_ARGUMENT"


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
    "checks",
    "names",
    "instruction",
    "carrier_cast",
    "entity_exclusions",
    "answer_terms",
    "must_not_contain",
    "gold_entities",
    "gold_answer",
    "verdict",
    "verdicts",
    "passed",
    "details",
}


@pytest.mark.asyncio
async def test_no_probe_payload_carries_the_fixture_or_a_verdict(
    bridge: BenchmarkBridge,
) -> None:
    """Tasks, submits and progress show the prompts and counts, never checks or verdicts."""
    started = await bridge.start("probes-all", "claude-code", "m")
    run_id = started["run_id"]
    payloads = [started, await bridge.next_task(run_id)]
    payloads.append(await bridge.submit(run_id, "T1-easy", "entities", _ENTITIES))
    payloads.append(await bridge.submit(run_id, "T1-easy", "relationships", _RELATIONSHIPS))
    payloads.append(await bridge.submit(run_id, "T2-hard", "entities", "", empty_answer=True))
    payloads.append(await bridge.progress(run_id))
    assert all(p["success"] for p in payloads), payloads
    text = json.dumps(payloads)
    for key, value in _walk(json.loads(text)):
        assert key not in _FIXTURE_KEYS, key
        assert value not in ("entities_present", "relationship_count", "entity_count"), value
    for leak in ("Extract both commanders", "Extract nothing from", "entities_present"):
        assert leak not in text, leak
    task = started["next"]
    assert set(task) == {
        "done",
        "task_id",
        "probe_id",
        "kind",
        "section",
        "tier",
        "stage",
        "system_prompt",
        "user_prompt",
        "answer_format",
        "remaining",
    }
