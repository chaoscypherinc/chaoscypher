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


def _stub_run_context(monkeypatch, ds):
    """Neutralise everything ``ProbeDataset.run`` needs except the filter.

    ``run`` defers its imports, so each one is patched at its source module.
    Returns the list ``_run_probe`` appends the probe ids it is handed to.
    """
    from types import SimpleNamespace

    from chaoscypher_cli.benchmark import extraction_dataset as ed_mod
    from chaoscypher_core.services.sources.engine.extraction import orchestration as orch_mod
    from chaoscypher_core.services.sources.engine.extraction.domains import factory as factory_mod
    from chaoscypher_core.services.sources.engine.extraction.utils import ai_entities as ai_mod

    settings = SimpleNamespace(
        llm=SimpleNamespace(extraction_examples_enabled=False, extraction_examples_max_chars=0)
    )
    ctx = SimpleNamespace(settings=settings, database_name="db")

    monkeypatch.setattr(
        ed_mod.ExtractionDataset, "_build_temp_context", lambda self, model: ctx, raising=True
    )
    monkeypatch.setattr(
        factory_mod,
        "get_domain_registry",
        lambda s, d: SimpleNamespace(get_domain=lambda name: SimpleNamespace(name=name)),
    )
    monkeypatch.setattr(
        orch_mod,
        "format_extraction_templates",
        lambda *a, **kw: {"node_templates": "", "edge_templates": ""},
    )
    monkeypatch.setattr(ai_mod, "AIEntityExtractor", lambda settings: SimpleNamespace())
    monkeypatch.setattr(ds, "_teardown", lambda ctx: None)

    ran: list[str] = []

    async def fake_run_probe(probe, extractor, templates, model):
        ran.append(probe.id)
        return {"latency_ms": 1, "input_tokens": 0, "output_tokens": 0}

    monkeypatch.setattr(ds, "_run_probe", fake_run_probe)
    return ran


def _probe_dataset(only):
    from chaoscypher_cli.benchmark.probe_dataset import ProbeDataset

    return ProbeDataset(
        id="p",
        version="1",
        domain="literary",
        probes=[
            _probe("A-easy", "easy", [{"type": "finish_stop"}]),
            _probe("B-easy", "easy", [{"type": "finish_stop"}]),
        ],
        only=only,
    )


@pytest.mark.asyncio
async def test_only_filter_restricts_probe_selection(monkeypatch) -> None:
    """`only` was shipped without a test (review note on #655).

    Drives ``run()`` so the production filter at probe_dataset.py:144 is the
    code under test. The previous version of this test evaluated a copy of
    that comprehension in its own body, so replacing the production line with
    ``list(self.probes)`` left it green.
    """
    from types import SimpleNamespace

    ds = _probe_dataset(frozenset({"B-easy"}))
    ran = _stub_run_context(monkeypatch, ds)
    model = SimpleNamespace(provider="openai", model="m", model_id="openai/m", label="m")

    out = await ds.run(model)

    assert out.error is None, out.error
    assert ran == ["B-easy"], "only the selected probe may reach the extractor"
    assert out.extras["selected_ids"] == ["B-easy"]


@pytest.mark.asyncio
async def test_only_unset_runs_every_probe(monkeypatch) -> None:
    """The ``only is None`` arm of the same filter, and its selected_ids stamp."""
    from types import SimpleNamespace

    ds = _probe_dataset(None)
    ran = _stub_run_context(monkeypatch, ds)
    model = SimpleNamespace(provider="openai", model="m", model_id="openai/m", label="m")

    out = await ds.run(model)

    assert out.error is None, out.error
    assert ran == ["A-easy", "B-easy"]
    assert out.extras["selected_ids"] == ["A-easy", "B-easy"]


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
