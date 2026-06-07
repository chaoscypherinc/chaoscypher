# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Framework-agnostic analytics and aggregations for Chaos Cypher Core.

Functions in this package operate on plain data (dicts, sequences) and MUST NOT
import from `chaoscypher_core.adapters.*`. Used by both services and adapters
as a shared neutral module.
"""

from chaoscypher_core.analytics.llm_metrics import LLMMetricsCollector, compute_metrics_summary


__all__ = ["LLMMetricsCollector", "compute_metrics_summary"]
