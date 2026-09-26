# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ProbeDataset runner tests: probe selection and what reaches the extractor.

The checker unit tests live with the scorer in Core
(``packages/core/tests/unit/benchmark/test_probe_scorer.py``).
"""

from __future__ import annotations

import pytest

from chaoscypher_cli.benchmark.probe_dataset import Probe


def _probe(pid, tier, checks, section="E"):
    return Probe(
        id=pid,
        probe=pid.split("-")[0],
        section=section,
        tier=tier,
        instruction="i",
        passage="p.",
        checks=tuple(checks),
    )


def test_only_filter_restricts_probe_selection() -> None:
    """`only` was shipped without a test (review note on #655)."""
    from chaoscypher_cli.benchmark.probe_dataset import ProbeDataset

    probes = [
        _probe("A-easy", "easy", [{"type": "finish_stop"}]),
        _probe("B-easy", "easy", [{"type": "finish_stop"}]),
    ]
    ds = ProbeDataset(
        id="p", version="1", domain="literary", probes=probes, only=frozenset({"B-easy"})
    )
    selected = [p for p in ds.probes if ds.only is None or p.id in ds.only]
    assert [p.id for p in selected] == ["B-easy"]


@pytest.mark.asyncio
async def test_run_probe_sends_the_transformed_passage_to_the_extractor(tmp_path) -> None:
    """Regression: the extractor must receive the padded/spliced text.

    2026-09-24: _run_probe computed the transformed passage and then passed
    probe.passage anyway, so pad_to_chars and carrier were silent no-ops - the
    "padded" run sent byte-identical input to the native run.
    """
    from types import SimpleNamespace

    from chaoscypher_cli.benchmark.probe_dataset import ProbeDataset

    seen: dict = {}

    async def fake_extract(**kw):
        seen["chunk_content"] = kw["chunk_content"]
        return [], [], 10, 5, {"finish_reason": "stop", "sentences": [], "_prompt_data": {}}

    (tmp_path / "carrier.txt").write_text(
        "Anna received her guests. Vasili arrived late. The abbe spoke."
    )
    probe = Probe(
        id="X-easy-carrier",
        probe="X",
        section="H",
        tier="hard",
        instruction="i",
        passage="Kutuzov slept.",
        checks=({"type": "finish_stop"},),
        carrier="carrier.txt",
    )
    ds = ProbeDataset(
        id="p", version="1", domain="literary", probes=[probe], pack_dir=tmp_path, pad_to_chars=800
    )
    model = SimpleNamespace(provider="ollama", model="m", model_id="ollama/m", label="m")
    rec = await ds._run_probe(
        probe,
        SimpleNamespace(extract_single_chunk=fake_extract),
        {"node_templates": "", "edge_templates": ""},
        model,
    )
    sent = seen["chunk_content"]
    assert "Kutuzov slept." in sent and "Vasili arrived late." in sent  # spliced
    assert len(sent) >= 800  # padded
    assert rec["probe_offset"] >= 2
