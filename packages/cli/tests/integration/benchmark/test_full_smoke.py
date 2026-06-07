# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""End-to-end smoke for the three-stage benchmark.

Skipped by default. To run:

    CHAOSCYPHER_BENCHMARK_INTEGRATION=1 uv run pytest \
        packages/cli/tests/integration/benchmark/test_full_smoke.py -v

Requires Ollama on 127.0.0.1:11434 with the models referenced by the
``full`` config pulled. The smoke run reduces each role list to one
entry and uses the chat candidate as the judge (self-judge) for cost.
"""

from __future__ import annotations

import os
import socket

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("CHAOSCYPHER_BENCHMARK_INTEGRATION") != "1",
    reason="set CHAOSCYPHER_BENCHMARK_INTEGRATION=1 to run",
)


def _ollama_up() -> bool:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", 11434))
        return True
    except OSError:
        return False
    finally:
        s.close()


@pytest.mark.asyncio
async def test_full_pipeline_smoke(tmp_path):
    if not _ollama_up():
        pytest.skip("Ollama not reachable on 127.0.0.1:11434")

    from chaoscypher_cli.benchmark.config import BenchmarkConfig, load_config
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle
    from chaoscypher_cli.benchmark.leaderboard import render_leaderboard
    from chaoscypher_cli.benchmark.orchestrator import default_wiring, run_full_benchmark

    cfg = load_config("full")
    # Reduce to one of each role for the smoke run; self-judge keeps cost local.
    smoke_cfg = BenchmarkConfig(
        name=cfg.name,
        description=cfg.description,
        seed=cfg.seed,
        temperature=cfg.temperature,
        dataset_ids=cfg.dataset_ids,
        extractors=cfg.extractors[:1] if cfg.extractors else None,
        embedders=cfg.embedders[:1] if cfg.embedders else None,
        chats=cfg.chats[:1] if cfg.chats else None,
        judge=cfg.chats[0] if cfg.chats else None,
        config_name=cfg.config_name,
        source=cfg.source,
    )

    bundles = [load_dataset_bundle(d) for d in smoke_cfg.dataset_ids]
    wiring = default_wiring(workspace=tmp_path)
    rows = await run_full_benchmark(smoke_cfg, bundles, wiring=wiring)

    kinds = {r.dataset_kind for r in rows}
    assert "extraction" in kinds, f"missing extraction rows; got {kinds}"
    assert "embedding" in kinds, f"missing embedding rows; got {kinds}"
    if smoke_cfg.chats:
        assert "chat" in kinds, f"missing chat rows; got {kinds}"

    md = render_leaderboard(rows)
    assert "## Extraction Leaderboard" in md
    assert "## Embedding Leaderboard" in md
    if smoke_cfg.chats:
        assert "## Chat Leaderboard" in md
