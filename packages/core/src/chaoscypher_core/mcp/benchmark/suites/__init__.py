# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The benchmark suite registry.

Adding a suite: implement :class:`~chaoscypher_core.mcp.benchmark.suites.base.BenchmarkSuite`
in one module and add a :class:`SuiteSpec` for it here. The MCP tool
schemas (suite and stage enums, the suite description) are built from this
registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from chaoscypher_core.mcp.benchmark.suites.base import (
    BenchmarkSuite,
    ScoreBundle,
    SubmitOutcome,
    SuiteContext,
    TaskRef,
)
from chaoscypher_core.mcp.benchmark.suites.chat import ChatSuite, chat_suite_factory
from chaoscypher_core.mcp.benchmark.suites.probes import ProbeSuite, probe_suite_factory


if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class SuiteSpec:
    """One registry entry.

    Attributes:
        factory: Builds the suite from the bridge's context.
        stages: The suite's stage names (for the submit tool's enum).
        description: One line for the start tool's ``suite`` argument.
    """

    factory: Callable[[SuiteContext], BenchmarkSuite]
    stages: tuple[str, ...]
    description: str


SUITES: dict[str, SuiteSpec] = {
    "probes": SuiteSpec(
        probe_suite_factory("probes"),
        ProbeSuite.stages,
        "extraction probes, isolated (sections A-E)",
    ),
    "probes-carrier": SuiteSpec(
        probe_suite_factory("probes-carrier"),
        ProbeSuite.stages,
        "the same extraction instructions inside real production-size chunks (section H)",
    ),
    "probes-all": SuiteSpec(
        probe_suite_factory("probes-all"),
        ProbeSuite.stages,
        "both extraction probe sets",
    ),
    "chat": SuiteSpec(
        chat_suite_factory,
        ChatSuite.stages,
        "grounded chat: answer each question of a reference pack from context "
        "retrieved from its fixed graph",
    ),
}
"""Suite name -> how to build it."""


def all_stages() -> list[str]:
    """Every stage name any registered suite uses, in first-seen order."""
    stages: list[str] = []
    for spec in SUITES.values():
        stages.extend(s for s in spec.stages if s not in stages)
    return stages


__all__ = [
    "SUITES",
    "BenchmarkSuite",
    "ScoreBundle",
    "SubmitOutcome",
    "SuiteContext",
    "SuiteSpec",
    "TaskRef",
    "all_stages",
]
