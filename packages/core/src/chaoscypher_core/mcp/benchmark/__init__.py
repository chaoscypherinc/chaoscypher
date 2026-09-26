# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""MCP benchmark: an MCP client's own model runs a benchmark suite.

Two suites ship: the extraction instruction probes (``probes``,
``probes-carrier``, ``probes-all``) and grounded chat over a reference pack
(``chat``). :class:`BenchmarkBridge` runs any of them; the suites live in
:mod:`chaoscypher_core.mcp.benchmark.suites`.
"""

from __future__ import annotations

from chaoscypher_core.mcp.benchmark.bridge import (
    CLIENT_SETTINGS_MAX_KEYS,
    CLIENT_SETTINGS_MAX_VALUE_CHARS,
    BenchmarkBridge,
)
from chaoscypher_core.mcp.benchmark.errors import (
    ERR_EMPTY_OUTPUT,
    ERR_INCOMPLETE_RUN,
    ERR_INTERNAL,
    ERR_INVALID_ARGUMENT,
    ERR_OUT_OF_ORDER,
    ERR_RUN_FINISHED,
    ERR_TASK_FINAL,
    ERR_UNKNOWN_PROBE,
    ERR_UNKNOWN_REFERENCE,
    ERR_UNKNOWN_RUN,
    ERR_UNKNOWN_SUITE,
    ERR_UNKNOWN_TASK,
    BenchmarkError,
)
from chaoscypher_core.mcp.benchmark.suites import SUITES, SuiteSpec, all_stages
from chaoscypher_core.mcp.benchmark.suites.probes import (
    PROBE_PACK_DIR,
    PROBE_PAD_TO_CHARS,
    PROBE_SEED,
    STAGES,
)


__all__ = [
    "CLIENT_SETTINGS_MAX_KEYS",
    "CLIENT_SETTINGS_MAX_VALUE_CHARS",
    "ERR_EMPTY_OUTPUT",
    "ERR_INCOMPLETE_RUN",
    "ERR_INTERNAL",
    "ERR_INVALID_ARGUMENT",
    "ERR_OUT_OF_ORDER",
    "ERR_RUN_FINISHED",
    "ERR_TASK_FINAL",
    "ERR_UNKNOWN_PROBE",
    "ERR_UNKNOWN_REFERENCE",
    "ERR_UNKNOWN_RUN",
    "ERR_UNKNOWN_SUITE",
    "ERR_UNKNOWN_TASK",
    "PROBE_PACK_DIR",
    "PROBE_PAD_TO_CHARS",
    "PROBE_SEED",
    "STAGES",
    "SUITES",
    "BenchmarkBridge",
    "BenchmarkError",
    "SuiteSpec",
    "all_stages",
]
