# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Smoke test: default_wiring() constructs a properly-shaped OrchestratorWiring."""

from __future__ import annotations

from pathlib import Path

from chaoscypher_cli.benchmark.orchestrator import default_wiring


def test_default_wiring_constructs(tmp_path: Path) -> None:
    """default_wiring returns an OrchestratorWiring with all callables present."""
    wiring = default_wiring(workspace=tmp_path)
    assert wiring.cache is not None
    assert callable(wiring.graph_provider_factory)
    assert callable(wiring.embed_query)
    assert callable(wiring.vector_search)
    assert callable(wiring.graphrag_search)
    assert callable(wiring.chat)
    assert callable(wiring.judge_call)
