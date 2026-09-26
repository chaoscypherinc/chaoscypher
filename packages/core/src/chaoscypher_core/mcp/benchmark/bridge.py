# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""MCP benchmark bridge: run a benchmark suite against an MCP client's own model.

The client's model answers each task, one tool call per stage, and the
answers are scored by exactly the checks the local runs use:

1. ``start`` picks a suite (extraction probes or grounded chat) and creates a run.
2. ``next_task`` hands out the next pending stage of the next pending task.
3. ``submit`` stores the raw answer; when the task is complete the suite
   builds the same record a local run builds and scores it.
4. ``finish`` scores the whole run and writes a normal results file.

Integrity: the client sees only what a local run's model sees (the prompts,
the stage, neutral meta such as section/tier or band) and never a verdict
before ``finish`` - ``submit`` and ``progress`` report acceptance and
counts only. Every stage is answered once: a stage already answered, or a
completed task, is final (``TASK_FINAL``), and each refusal is counted on the
result row. A finished run takes no more answers.

The bridge knows nothing suite-specific: it asks the run's suite (see
:mod:`chaoscypher_core.mcp.benchmark.suites`). Instructions for the client
live in the tool descriptions and each task's ``answer_format``, never in
the prompts, which are byte-identical to what a local run sends.

This is a harness track: the benchmark cannot pin the client's temperature,
seed or thinking, so result rows carry ``pins_applied=False`` and
``harness="mcp:<client>"``. Run state is a JSON file per run under
``<data_dir>/benchmark/mcp/``, rewritten after every change and re-read on
every call, so a client that stops can resume where it left off.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.benchmark.results import BENCHMARK_VERSION, BenchmarkResult, dump_results
from chaoscypher_core.mcp.benchmark.errors import (
    ERR_EMPTY_OUTPUT,
    ERR_INCOMPLETE_RUN,
    ERR_INTERNAL,
    ERR_INVALID_ARGUMENT,
    ERR_OUT_OF_ORDER,
    ERR_RUN_FINISHED,
    ERR_TASK_FINAL,
    ERR_UNKNOWN_RUN,
    ERR_UNKNOWN_SUITE,
    ERR_UNKNOWN_TASK,
    BenchmarkError,
    error_payload,
)
from chaoscypher_core.mcp.benchmark.suites import SUITES, SuiteContext
from chaoscypher_core.mcp.benchmark.suites.probes import PROBE_PACK_DIR
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from collections.abc import Callable

    from chaoscypher_core.mcp.benchmark.suites import BenchmarkSuite, TaskRef
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


_RUN_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,79}$")

CLIENT_SETTINGS_MAX_KEYS = 20
"""Most ``client_settings`` entries a run may record."""

CLIENT_SETTINGS_MAX_VALUE_CHARS = 80
"""Longest string value a ``client_settings`` entry may carry."""

_SETTING_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,39}$")


def _validate_client_settings(settings: Any) -> dict[str, str | int | float | bool] | None:
    """Check ``client_settings`` is a small flat map of scalars, or raise.

    Keys are 1-40 characters (a letter, then letters, digits and ``_.-``);
    values are strings of at most :data:`CLIENT_SETTINGS_MAX_VALUE_CHARS`
    characters, finite numbers or booleans. Nested values, nulls and more
    than :data:`CLIENT_SETTINGS_MAX_KEYS` entries are rejected, since the map
    is printed on the leaderboard row as-is.
    """
    if settings is None:
        return None
    if not isinstance(settings, dict):
        raise BenchmarkError(ERR_INVALID_ARGUMENT, "client_settings must be an object")
    if len(settings) > CLIENT_SETTINGS_MAX_KEYS:
        raise BenchmarkError(
            ERR_INVALID_ARGUMENT,
            f"client_settings may hold at most {CLIENT_SETTINGS_MAX_KEYS} entries",
        )
    for key, value in settings.items():
        if not isinstance(key, str) or not _SETTING_KEY_RE.match(key):
            raise BenchmarkError(
                ERR_INVALID_ARGUMENT,
                f"client_settings key {key!r} must be 1-40 characters of letters, "
                "digits and _.- starting with a letter",
            )
        if isinstance(value, str):
            ok = 0 < len(value) <= CLIENT_SETTINGS_MAX_VALUE_CHARS
        elif isinstance(value, bool | int):
            ok = True
        elif isinstance(value, float):
            ok = math.isfinite(value)
        else:
            ok = False
        if not ok:
            raise BenchmarkError(
                ERR_INVALID_ARGUMENT,
                f"client_settings[{key!r}] must be a non-empty string of at most "
                f"{CLIENT_SETTINGS_MAX_VALUE_CHARS} characters, a number or a boolean",
            )
    return dict(settings)


class BenchmarkBridge:
    """Serve benchmark tasks to an MCP client and score its answers.

    Args:
        settings: Engine settings. ``paths.data_dir`` locates run state,
            results and reference packs; the extraction settings resolve the
            probe prompts exactly as a local run does.
        pack_dir: Probe fixture directory (defaults to the shipped one).
    """

    def __init__(self, settings: EngineSettings, *, pack_dir: Path | None = None) -> None:
        """Bind settings and the probe fixture; suites are built on first use."""
        self.settings = settings
        self.pack_dir = pack_dir or PROBE_PACK_DIR
        self._suites: dict[tuple[str, str | None], BenchmarkSuite] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    #  Paths and state
    # ------------------------------------------------------------------ #

    @property
    def state_dir(self) -> Path:
        """Directory holding one JSON state file per run."""
        return Path(self.settings.paths.data_dir) / "benchmark" / "mcp"

    @property
    def results_dir(self) -> Path:
        """Directory finished runs write their results files to."""
        return Path(self.settings.paths.data_dir) / "benchmark" / "results"

    def _state_path(self, run_id: str) -> Path:
        """Return the state file for ``run_id`` after validating its shape."""
        if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
            raise BenchmarkError(ERR_UNKNOWN_RUN, f"unknown run_id {run_id!r}")
        return self.state_dir / f"{run_id}.json"

    def _load(self, run_id: str) -> dict[str, Any]:
        """Read a run's state from disk (every call re-reads it).

        Runs started before suites existed carry ``probe_ids`` and no
        ``reference``; they load as ``task_ids`` with no reference.
        """
        path = self._state_path(run_id)
        if not path.exists():
            raise BenchmarkError(ERR_UNKNOWN_RUN, f"unknown run_id {run_id!r}")
        state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if "task_ids" not in state and "probe_ids" in state:
            state["task_ids"] = state.pop("probe_ids")
        state.setdefault("reference", None)
        state.setdefault("verdicts", {})
        state.setdefault("resubmissions_refused", 0)
        state.setdefault("finished_at", None)
        return state

    def _save(self, state: dict[str, Any]) -> None:
        """Write a run's state atomically so an interrupted write cannot corrupt it."""
        path = self._state_path(state["run_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
        tmp.replace(path)

    # ------------------------------------------------------------------ #
    #  Suites and the task loop
    # ------------------------------------------------------------------ #

    def suite(self, name: str, reference: str | None = None) -> BenchmarkSuite:
        """The suite ``name`` over ``reference`` (cached per bridge).

        A suite that needs a reference pack resolves ``None`` to the only pack
        there is; the suite is cached under the reference it resolved to.

        Raises:
            BenchmarkError: On an unknown suite, a reference the suite does
                not take, or a reference the suite cannot resolve.
        """
        if name not in SUITES:
            raise BenchmarkError(
                ERR_UNKNOWN_SUITE, f"unknown suite {name!r}; choose one of {sorted(SUITES)}"
            )
        cached = self._suites.get((name, reference))
        if cached is not None:
            return cached
        built = SUITES[name].factory(
            SuiteContext(settings=self.settings, probe_pack_dir=self.pack_dir, reference=reference)
        )
        if reference is not None and built.reference is None:
            raise BenchmarkError(
                ERR_INVALID_ARGUMENT, f"suite {name!r} does not take a reference pack"
            )
        # Cache under the reference it resolved to, and keep an instance
        # already cached there so anything bound to it (an open engine, a
        # test's fake retriever) stays. A None reference is resolved afresh
        # each time, so a pack exported later is seen.
        return self._suites.setdefault((name, built.reference), built)

    def _run_suite(self, state: dict[str, Any]) -> BenchmarkSuite:
        """The suite a loaded run belongs to."""
        return self.suite(state["suite"], state.get("reference"))

    @staticmethod
    def _run_tasks(state: dict[str, Any], suite: BenchmarkSuite) -> list[TaskRef]:
        """Return the run's tasks in run order."""
        refs = {t.id: t for t in suite.tasks(None)}
        return [refs[tid] for tid in state["task_ids"] if tid in refs]

    async def _next(self, state: dict[str, Any], suite: BenchmarkSuite) -> dict[str, Any]:
        """Describe the next pending stage, or report that the run is done."""
        tasks = self._run_tasks(state, suite)
        remaining = sum(1 for t in tasks if t.id not in state["records"])
        for task in tasks:
            stage = suite.pending_stage(state, task.id)
            if stage is None:
                continue
            prompts = await suite.prompt(state, task.id, stage)
            return {
                "done": False,
                "task_id": task.id,
                # The pre-suite name of task_id; kept for one release.
                "probe_id": task.id,
                "kind": suite.kind,
                **task.meta,
                "stage": stage,
                "system_prompt": prompts["system_prompt"],
                "user_prompt": prompts["user_prompt"],
                "answer_format": prompts.get("answer_format", suite.answer_format),
                "remaining": remaining,
            }
        return {"done": True, "submitted": len(state["records"]), "total": len(tasks)}

    async def _guarded(self, fn: Callable[[], Any], op: str) -> dict[str, Any]:
        """Run ``fn`` under the bridge lock; map every failure to an error payload."""
        async with self._lock:
            try:
                result: dict[str, Any] = await fn()
                return result
            except BenchmarkError as exc:
                return error_payload(exc.code, str(exc))
            except Exception as exc:
                logger.exception("mcp_benchmark_failed", op=op)
                return error_payload(ERR_INTERNAL, f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ #
    #  Tool methods
    # ------------------------------------------------------------------ #

    async def start(
        self,
        suite: str,
        client: str,
        model: str,
        *,
        label: str | None = None,
        only: list[str] | None = None,
        client_settings: dict[str, Any] | None = None,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Create a run for ``suite`` answered by ``model`` inside ``client``.

        ``client_settings`` records what the client controls and the
        benchmark cannot pin (effort, thinking mode, client version); it is
        written on the result row as ``harness_settings``. ``reference``
        names the reference pack a grounded-chat run retrieves from
        (defaulting to the only one there is).

        Returns ``{run_id, suite, total, next}`` where ``next`` is the first
        task (see :meth:`next_task`).
        """

        async def _do() -> dict[str, Any]:
            """Validate arguments, select tasks and persist the new run."""
            if suite not in SUITES:
                raise BenchmarkError(
                    ERR_UNKNOWN_SUITE, f"unknown suite {suite!r}; choose one of {sorted(SUITES)}"
                )
            for name, value in (("client", client), ("model", model)):
                if not isinstance(value, str) or not _NAME_RE.match(value):
                    raise BenchmarkError(
                        ERR_INVALID_ARGUMENT,
                        f"{name} must be 1-80 characters of letters, digits and ._:@+-",
                    )
            settings = _validate_client_settings(client_settings)
            runner = self.suite(suite, reference)
            selected = runner.tasks(only)
            if not selected:
                raise BenchmarkError(ERR_INVALID_ARGUMENT, "no tasks selected")
            # 12 hex chars from the shared id helper (CC-019: no inline uuid4).
            run_id = generate_id().replace("-", "")[:12]
            state: dict[str, Any] = {
                "run_id": run_id,
                "suite": suite,
                "reference": runner.reference,
                "client": client,
                "model": model,
                "label": label,
                "client_settings": settings,
                **runner.run_info(),
                "created_at": datetime.now(tz=UTC).isoformat(),
                "task_ids": [t.id for t in selected],
                "stages": {},
                "records": {},
                "verdicts": {},
                "results_path": None,
                "resubmissions_refused": 0,
                "finished_at": None,
            }
            self._save(state)
            logger.info(
                "mcp_benchmark_started",
                run_id=run_id,
                suite=suite,
                reference=runner.reference,
                total=len(selected),
            )
            nxt = await self._next(state, runner)
            self._save(state)  # the first prompt may have cached work (retrieval)
            return {
                "success": True,
                "run_id": run_id,
                "suite": suite,
                "reference": runner.reference,
                "total": len(selected),
                "next": nxt,
            }

        return await self._guarded(_do, "start")

    async def next_task(self, run_id: str) -> dict[str, Any]:
        """Return the next pending stage of ``run_id``, or ``done`` when none is left."""

        async def _do() -> dict[str, Any]:
            """Load the run and describe its next pending stage."""
            state = self._load(run_id)
            nxt = await self._next(state, self._run_suite(state))
            self._save(state)
            return {"success": True, "run_id": run_id, **nxt}

        return await self._guarded(_do, "next_task")

    async def submit(
        self,
        run_id: str,
        task_id: str | None = None,
        stage: str | None = None,
        output_text: str | None = None,
        *,
        probe_id: str | None = None,
        output_tokens: int | None = None,
        empty_answer: bool = False,
        truncated: bool = False,
    ) -> dict[str, Any]:
        """Store the model's answer for one stage of one task.

        ``probe_id`` is the pre-suite name of ``task_id``, accepted for one
        release. Each stage is answered once: a stage already answered, or
        any stage of a completed task, is refused with ``TASK_FINAL`` (and
        counted); a stage ahead of the task's pending one is
        ``OUT_OF_ORDER``. ``truncated`` says the answer was cut off by the
        client's output limit (grounded chat records it as
        ``finish_reason="length"``). The response reports acceptance and
        progress only - never a verdict - and always carries ``next``.
        """

        async def _do() -> dict[str, Any]:
            """Validate the submission, hand it to the suite and score a completed task."""
            tid = task_id if task_id is not None else probe_id
            if tid is None:
                raise BenchmarkError(ERR_INVALID_ARGUMENT, "task_id is required")
            if task_id is not None and probe_id is not None and task_id != probe_id:
                raise BenchmarkError(
                    ERR_INVALID_ARGUMENT, "task_id and probe_id name different tasks"
                )
            state = self._load(run_id)
            if state.get("finished_at"):
                raise BenchmarkError(ERR_RUN_FINISHED, "this run is finished; start a new one")
            suite = self._run_suite(state)
            known = {t.id for t in suite.tasks(None)}
            if tid not in state["task_ids"] or tid not in known:
                raise BenchmarkError(ERR_UNKNOWN_TASK, f"probe {tid!r} is not in this run")
            if stage not in suite.stages:
                raise BenchmarkError(
                    ERR_INVALID_ARGUMENT,
                    f"stage must be one of {list(suite.stages)}, got {stage!r}",
                )
            self._check_stage_order(state, suite, tid, stage)
            if not isinstance(output_text, str):
                raise BenchmarkError(ERR_INVALID_ARGUMENT, "output_text must be a string")
            if not output_text.strip() and not empty_answer:
                raise BenchmarkError(
                    ERR_EMPTY_OUTPUT,
                    "output_text is empty; if the correct answer really is empty, "
                    "submit an empty output_text with empty_answer=true",
                )
            if output_tokens is not None and (
                not isinstance(output_tokens, int) or output_tokens < 0
            ):
                raise BenchmarkError(
                    ERR_INVALID_ARGUMENT, "output_tokens must be a non-negative integer"
                )
            outcome = await suite.submit(
                state,
                tid,
                stage,
                output_text,
                output_tokens=output_tokens,
                empty_answer=empty_answer,
                truncated=bool(truncated),
            )
            if outcome.record is not None:
                state["records"][tid] = outcome.record
                # Cached for finish and the results file; never returned mid-run.
                state["verdicts"][tid] = suite.verdict(tid, outcome.record)
            self._save(state)
            response: dict[str, Any] = {
                "success": True,
                "accepted": True,
                "task_id": tid,
                "probe_id": tid,
                "stage": stage,
                "submitted": len(state["records"]),
                "total": len(state["task_ids"]),
                "next": await self._next(state, suite),
            }
            self._save(state)
            return response

        return await self._guarded(_do, "submit")

    def _check_stage_order(
        self, state: dict[str, Any], suite: BenchmarkSuite, task_id: str, stage: str
    ) -> None:
        """Refuse a stage that is not the one ``task_id`` is waiting on.

        An earlier stage (or any stage of a completed task) was already
        answered and is final: the refusal is counted on the run, which is
        saved before raising. A later stage is out of order.
        """
        pending = suite.pending_stage(state, task_id)
        if pending == stage:
            return
        if pending is None or suite.stages.index(stage) < suite.stages.index(pending):
            state["resubmissions_refused"] = int(state.get("resubmissions_refused") or 0) + 1
            self._save(state)
            what = "task" if pending is None else f"stage {stage!r} of the task"
            raise BenchmarkError(
                ERR_TASK_FINAL,
                f"{what} {task_id!r} already answered; a run scores the first answer",
            )
        raise BenchmarkError(ERR_OUT_OF_ORDER, f"submit the {pending} stage of {task_id!r} first")

    async def progress(self, run_id: str) -> dict[str, Any]:
        """Report how far ``run_id`` has got and whether it has been finished.

        Counts only: submitted, pending and total - never pass counts.
        """

        async def _do() -> dict[str, Any]:
            """Summarise completed and pending tasks."""
            state = self._load(run_id)
            suite = self._run_suite(state)
            tasks = self._run_tasks(state, suite)
            pending = [
                {"task_id": t.id, "probe_id": t.id, "stage": suite.pending_stage(state, t.id)}
                for t in tasks
                if t.id not in state["records"]
            ]
            return {
                "success": True,
                "run_id": run_id,
                "suite": state["suite"],
                "reference": state.get("reference"),
                "client": state["client"],
                "model": state["model"],
                "submitted": len(state["records"]),
                "total": len(tasks),
                "pending": pending[:10],
                "pending_count": len(pending),
                "results_path": state.get("results_path"),
            }

        return await self._guarded(_do, "progress")

    async def finish(self, run_id: str, *, allow_incomplete: bool = False) -> dict[str, Any]:
        """Score the run and write it as a one-row results file.

        Refuses while tasks are still pending unless ``allow_incomplete``;
        a pending task then scores as a fail, never as a smaller
        denominator. A finished run takes no more answers; finishing again
        rewrites the file. The row's ``extras["run"]`` records when the run
        started and finished, its task count and how many re-submissions
        were refused.
        """

        async def _do() -> dict[str, Any]:
            """Score every record and write the results file."""
            state = self._load(run_id)
            suite = self._run_suite(state)
            tasks = self._run_tasks(state, suite)
            missing = [t.id for t in tasks if t.id not in state["records"]]
            if missing and not allow_incomplete:
                raise BenchmarkError(
                    ERR_INCOMPLETE_RUN,
                    f"{len(missing)} task(s) not complete (next: {missing[0]}); keep going, "
                    "or pass allow_incomplete=true to score them as fails",
                )
            records = [state["records"][t.id] for t in tasks if t.id in state["records"]]
            bundle = suite.score(state, records)
            finished_at = state.get("finished_at") or datetime.now(tz=UTC).isoformat()
            extras = {
                **bundle.extras,
                "run": {
                    "started_at": state.get("created_at"),
                    "finished_at": finished_at,
                    "task_count": len(tasks),
                    "resubmissions_refused": int(state.get("resubmissions_refused") or 0),
                },
            }
            client, model = state["client"], state["model"]
            row = BenchmarkResult(
                model_id=f"mcp/{client}/{model}",
                model_label=state.get("label") or f"{model} via {client} (MCP)",
                dataset_id=bundle.dataset_id,
                dataset_kind=bundle.dataset_kind,
                dataset_version=bundle.dataset_version,
                dataset_source=bundle.dataset_source,
                config_name=bundle.config_name,
                headline_score=bundle.headline_score,
                metrics=bundle.metrics,
                latency_ms_total=0,
                latency_ms_per_chunk_p50=0,
                input_tokens=bundle.input_tokens,
                output_tokens=bundle.output_tokens,
                cost_usd=0.0,
                success=True,
                error=None,
                timestamp=datetime.now(tz=UTC),
                benchmark_version=BENCHMARK_VERSION,
                scorer_version=bundle.scorer_version,
                seed=bundle.seed,
                temperature=bundle.temperature,
                thinking=bundle.thinking,
                thinking_honoured=bundle.thinking_honoured,
                chunks_truncated=bundle.chunks_truncated,
                chunks_aborted_by_loop=bundle.chunks_aborted_by_loop,
                extras=extras,
                pins_applied=False,
                harness=f"mcp:{client}",
                harness_settings=state.get("client_settings") or None,
            )
            self.results_dir.mkdir(parents=True, exist_ok=True)
            out = self.results_dir / f"mcp-{run_id}.json"
            dump_results([row], out)
            state["results_path"] = str(out)
            state["finished_at"] = finished_at
            self._save(state)
            passed = bundle.metrics.get("probes_passed")
            total = bundle.metrics.get("probes_total")
            logger.info(
                "mcp_benchmark_finished",
                run_id=run_id,
                suite=state["suite"],
                headline=bundle.headline_score,
                passed=passed,
                total=total,
            )
            result: dict[str, Any] = {
                "success": True,
                "run_id": run_id,
                "suite": state["suite"],
                "results_path": str(out),
                "headline_score": round(bundle.headline_score, 2),
                "passed": passed,
                "total": total,
                # The pre-suite names of passed/total; kept for one release.
                "probes_passed": passed,
                "probes_total": total,
            }
            if "section_rates" in bundle.metrics:
                result["section_rates"] = bundle.metrics["section_rates"]
            return result

        return await self._guarded(_do, "finish")


__all__ = [
    "CLIENT_SETTINGS_MAX_KEYS",
    "CLIENT_SETTINGS_MAX_VALUE_CHARS",
    "BenchmarkBridge",
]
