# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A golden rescore: ten archived-style chat rows whose verdict vector is pinned exactly.

Belt and braces beside the recipe samples (wave 2 of the 2026-10-06 audit):
``data/chat_rescore_golden`` holds an invented fixture (a mill ledger) and a
results file of ten rows as a sweep writes them (per-query answers with the
context each model saw, a truncated answer, a missing and an errored record).
Rescoring them with the shipped scorer must reproduce ``expected.json``
verdict for verdict; a change that moves one fails here, names the row and
the question, and is either a bug or a deliberate scorer change that
regenerates the file (run this module as a script with ``--write``) next
to the version or changelog entry it needs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from chaoscypher_core.benchmark.queries import load_queries
from chaoscypher_core.benchmark.results import load_results
from chaoscypher_core.benchmark.scorers.chat_probes import ChatProbeScorer
from chaoscypher_core.benchmark.types import RawOutput


DATA = Path(__file__).with_name("data") / "chat_rescore_golden"


def rescore_vector() -> dict[str, Any]:
    """Every row rescored: ``model -> {"headline", "verdicts": [[id, passed, fails, citation]]}``."""
    fixture = load_queries(DATA / "queries.yaml")
    out: dict[str, Any] = {}
    for row in load_results(DATA / "rows.json"):
        raw = RawOutput(
            entities=[],
            relationships=[],
            latency_ms=0,
            input_tokens=0,
            output_tokens=0,
            error=None,
            extras=row.extras,
        )
        result = ChatProbeScorer().score(raw, fixture)
        out[row.model_label] = {
            "headline": round(result.headline_score, 4),
            "verdicts": [
                [
                    v["id"],
                    v["passed"],
                    sorted(
                        d.split(":", 1)[0].removeprefix("FAIL ")
                        for d in v["details"]
                        if d.startswith(("FAIL", "no result"))
                    ),
                    v["citation"],
                ]
                for v in result.metrics["verdicts"]
            ],
        }
    return out


def test_the_ten_rows_rescore_to_the_golden_vector() -> None:
    """Each row's verdicts, failing checks, citations and headline match exactly."""
    expected = json.loads((DATA / "expected.json").read_text(encoding="utf-8"))
    got = rescore_vector()
    assert len(got) == 10
    for model, want in expected.items():
        assert got[model] == want, f"{model} moved: rescore and compare with expected.json"
    assert got == expected


def test_the_golden_rows_cover_every_outcome() -> None:
    """The file is worth pinning: passes and fails, every check failing, every citation kind."""
    got = rescore_vector()
    verdicts = [v for row in got.values() for v in row["verdicts"]]
    assert {v[1] for v in verdicts} == {True, False}
    fails = {name for v in verdicts for name in v[2]}
    every_check = {c for checks in ChatProbeScorer.CHECKS_BY_BAND.values() for c in checks}
    assert every_check - {"binds_roles"} <= fails, every_check - fails
    assert {"correct", "wrong", "uncited", None} <= {v[3] for v in verdicts}


def render_expected(vector: dict[str, Any]) -> str:
    """``expected.json`` as written: one row per block, one verdict per line."""
    blocks = []
    for model, row in vector.items():
        lines = ",\n".join(f"   {json.dumps(v, ensure_ascii=False)}" for v in row["verdicts"])
        blocks.append(
            f' {json.dumps(model)}: {{\n  "headline": {json.dumps(row["headline"])},\n'
            f'  "verdicts": [\n{lines}\n  ]\n }}'
        )
    return "{\n" + ",\n".join(blocks) + "\n}\n"


if __name__ == "__main__" and "--write" in sys.argv:
    (DATA / "expected.json").write_text(render_expected(rescore_vector()), encoding="utf-8")
