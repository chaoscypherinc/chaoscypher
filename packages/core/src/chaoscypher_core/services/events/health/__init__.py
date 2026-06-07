# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Health Probe Registry.

Centralized health monitoring system for ChaosCypher. Probes check
system health (disk space, LLM providers, queue, database, etc.)
and return structured results that drive auto-pause decisions.

Exports:
    HealthPauseEvaluator: Auto-pause consumer with hysteresis trip/clear.
    HealthProbe: Protocol that all health probes implement.
    HealthRegistry: Central registry for probe registration and execution.
    ProbeInfo: Static metadata about a registered probe.
    ProbeResult: Result of a single health check execution.
"""

from chaoscypher_core.services.events.health.models import (
    HealthProbe,
    ProbeInfo,
    ProbeResult,
)
from chaoscypher_core.services.events.health.pause_evaluator import HealthPauseEvaluator
from chaoscypher_core.services.events.health.registry import HealthRegistry


__all__ = [
    "HealthPauseEvaluator",
    "HealthProbe",
    "HealthRegistry",
    "ProbeInfo",
    "ProbeResult",
]
