# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Smoke tests for the canonical LLM metrics module.

Canonical location is `chaoscypher_core.analytics.llm_metrics`.  The Phase 1
shim at `chaoscypher_core.adapters.llm.metrics` has been deleted.
"""

import ast
from pathlib import Path


def test_canonical_import():
    from chaoscypher_core.analytics import compute_metrics_summary

    assert callable(compute_metrics_summary)


def test_canonical_module_path():
    from chaoscypher_core.analytics import llm_metrics

    assert (
        llm_metrics.compute_metrics_summary.__module__ == "chaoscypher_core.analytics.llm_metrics"
    )


def test_collector_canonical_home():
    from chaoscypher_core.analytics import LLMMetricsCollector

    collector = LLMMetricsCollector(provider="ollama", model="test")
    collector.record_attempt(success=True, input_tokens=10, output_tokens=5, duration_ms=100)
    summary = collector.get_summary()
    assert summary["total_calls"] == 1
    assert summary["successful_calls"] == 1


def test_shim_module_deleted():
    """The Phase 1 shim at adapters/llm/metrics.py must be gone."""
    shim_path = (
        Path(__file__).resolve().parents[4]
        / "src"
        / "chaoscypher_core"
        / "adapters"
        / "llm"
        / "metrics.py"
    )
    assert not shim_path.exists(), f"Phase 1 shim still present at {shim_path}"


def test_no_runtime_imports_of_shim():
    """No runtime import of chaoscypher_core.adapters.llm.metrics under src/ or cortex/cli."""
    repo_root = Path(__file__).resolve().parents[5].parent
    targets = [
        repo_root / "packages" / "core" / "src",
        repo_root / "packages" / "cortex" / "src",
        repo_root / "packages" / "cli" / "src",
        repo_root / "packages" / "neuron" / "src",
    ]
    bad: list[str] = []
    for root in targets:
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):  # fmt: skip
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == (
                    "chaoscypher_core.adapters.llm.metrics"
                ):
                    bad.append(f"{path}:{node.lineno}")
    assert not bad, "Unexpected imports of the deleted shim:\n" + "\n".join(bad)
