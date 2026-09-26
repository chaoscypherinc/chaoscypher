# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The suite contract the benchmark bridge runs.

The bridge owns run state, the task loop and the results file; a suite owns
everything about its tasks: which exist, what each stage's prompt is, how an
answer becomes a record, and how records are scored. Adding a suite means one
module implementing :class:`BenchmarkSuite` and one entry in
:data:`~chaoscypher_core.mcp.benchmark.suites.SUITES`.

A suite reads and writes only ``state["stages"][task_id]`` (its per-task
scratch space, persisted with the run); the bridge keeps ``records`` and
``verdicts``, and never shows a verdict to the client before ``finish``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.settings import EngineSettings


@dataclass(frozen=True)
class TaskRef:
    """One task of a suite: its id and what the client is shown about it.

    Attributes:
        id: Task id (a probe id, a query id).
        meta: Shown with every task, e.g. ``{"section", "tier"}`` or ``{"band", "tier"}``.
    """

    id: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SubmitOutcome:
    """What a suite's ``submit`` produced.

    Attributes:
        record: The completed task's record, or None when another stage follows.
    """

    record: dict[str, Any] | None


@dataclass(frozen=True)
class ScoreBundle:
    """Everything suite-specific a finished run's result row needs.

    The bridge adds the rest: model id and label, harness fields, timestamp.
    """

    headline_score: float
    metrics: dict[str, Any]
    dataset_id: str
    dataset_kind: str
    dataset_version: str
    dataset_source: str
    config_name: str
    scorer_version: int
    seed: int | None
    temperature: float | None
    thinking: bool | None
    thinking_honoured: bool | None
    input_tokens: int
    output_tokens: int
    chunks_truncated: int
    chunks_aborted_by_loop: int
    extras: dict[str, Any]


@dataclass(frozen=True)
class SuiteContext:
    """What a suite factory is built from.

    Attributes:
        settings: The bridge's engine settings (``paths.data_dir`` locates packs).
        probe_pack_dir: The extraction probe fixture directory.
        reference: The reference pack the client asked for, or None.
    """

    settings: EngineSettings
    probe_pack_dir: Path
    reference: str | None = None


class BenchmarkSuite(Protocol):
    """A kind of benchmark task the bridge can hand to an MCP client."""

    name: str
    """Registry key, e.g. ``probes`` or ``chat``."""
    kind: str
    """``dataset_kind`` of the result row: ``probes`` or ``chat``."""
    stages: tuple[str, ...]
    """Stage names, in the order a task goes through them."""
    answer_format: str
    """One sentence telling the client what shape its answer takes."""
    reference: str | None
    """The reference pack the suite reads, or None."""

    def run_info(self) -> dict[str, Any]:
        """Fields recorded on a new run's state (``dataset_id``, ``dataset_version``)."""
        ...

    def tasks(self, only: list[str] | None) -> list[TaskRef]:
        """The suite's tasks in run order, or the ``only`` subset (unknown ids raise)."""
        ...

    def pending_stage(self, state: dict[str, Any], task_id: str) -> str | None:
        """The stage ``task_id`` waits on, or None when it is complete.

        The bridge refuses a stage before this one (already answered: final)
        and after it (out of order), so ``submit`` only sees this stage.
        """
        ...

    async def prompt(self, state: dict[str, Any], task_id: str, stage: str) -> dict[str, Any]:
        """The ``system_prompt`` and ``user_prompt`` for one stage of one task.

        Only what a local run's model would see: never the fixture's expected
        answers or checks.
        """
        ...

    async def submit(
        self,
        state: dict[str, Any],
        task_id: str,
        stage: str,
        output_text: str,
        *,
        output_tokens: int | None,
        empty_answer: bool,
        truncated: bool,
    ) -> SubmitOutcome:
        """Store one stage's answer; return the record once the task is complete."""
        ...

    def verdict(self, task_id: str, record: dict[str, Any]) -> dict[str, Any]:
        """Score one record: ``{"passed": bool, "details": [...]}``."""
        ...

    def score(self, state: dict[str, Any], records: list[dict[str, Any]]) -> ScoreBundle:
        """Score the whole run (missing tasks count as fails)."""
        ...


__all__ = [
    "BenchmarkSuite",
    "ScoreBundle",
    "SubmitOutcome",
    "SuiteContext",
    "TaskRef",
]
