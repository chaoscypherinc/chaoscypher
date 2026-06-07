# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Single real-LLM smoke test, env-gated.

Almost every other e2e test uses the deterministic fake-ollama
service (see ``packages/docker/e2e/fake-ollama/``). That covers the
pipeline plumbing but not the actual model-quality contracts:
extraction prompt → real LLM → V2 pipe-delimited response parser →
entity / relationship rows. This test runs the whole chain against a
real Ollama instance so a regression in any one of those layers is
caught before release.

Gated by ``E2E_REAL_LLM=1`` because:
  * Pulling models is multi-GB and slow.
  * Real models are non-deterministic, so assertions stay broad
    (entities exist, no errors, source reaches committed).
  * Run on demand by an operator before a release; skipped in
    routine CI.

Operator workflow:
  1. Start Ollama on the host, ensure ``llama3.2:1b`` (or similar
     tiny chat model) and ``nomic-embed-text`` are pulled.
  2. ``docker compose -f packages/docker/e2e/docker-compose.yml \\
       --profile real-llm up -d``
  3. ``E2E_REAL_LLM=1 E2E_BASE_URL=http://localhost:8888 \\
       uv run pytest e2e/api/test_real_llm_smoke.py -v``

The compose ``real-llm`` profile points the cortex at
``host.docker.internal:11434`` (the host's Ollama) instead of the
in-network fake. See ``packages/docker/e2e/settings.real-llm.yaml``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("E2E_REAL_LLM", "0") not in ("1", "true", "yes"),
    reason=(
        "Real-LLM smoke disabled. Set E2E_REAL_LLM=1 with the "
        "``real-llm`` compose profile (Ollama on host, models pulled) "
        "to run."
    ),
)


def _poll_source_status(
    client: httpx.Client,
    source_id: str,
    target: str,
    timeout: int,
) -> dict:
    """Poll until a source reaches ``target`` status or times out."""
    start = time.time()
    while time.time() - start < timeout:
        resp = client.get(f"/api/v1/sources/{source_id}")
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("processing_status") or data.get("status", "")
            if status == target:
                return data
            if status in ("error", "failed"):
                err = data.get("error_message", "(no message)")
                msg = f"source failed at stage={data.get('error_stage')}: {err}"
                raise RuntimeError(msg)
        time.sleep(1.0)
    raise TimeoutError(f"source {source_id} did not reach {target} within {timeout}s")


def test_real_extraction_smoke(client: httpx.Client, sample_data_dir: str) -> None:
    """Real LLM extracts at least one entity from a known-good text.

    Uses the tiny ``war_and_peace_tiny.txt`` fixture (~7 KB, three
    pages of canonical text) so the extraction completes in <60 s
    even on a 1B-parameter model. Assertions are intentionally
    broad — we're not pinning specific entity content, just verifying
    the pipeline produces SOMETHING.
    """
    sample = Path(sample_data_dir) / "war_and_peace_tiny.txt"
    assert sample.exists(), f"missing fixture: {sample}"

    with sample.open("rb") as f:
        upload = client.post(
            "/api/v1/sources",
            files={"file": ("real_llm_smoke.txt", f, "text/plain")},
            data={"extract_entities": "true"},
        )
    assert upload.status_code == 202, upload.text
    source_id = upload.json()["id"]

    # Real extraction can take a while on small models. Give it 5 min.
    detail = _poll_source_status(client, source_id, "committed", timeout=300)

    # Smoke-level assertions — pipeline produced output without
    # erroring, and at least one entity landed.
    assert detail.get("extraction_entities_count", 0) > 0, (
        f"real-llm smoke produced 0 entities — extraction is broken "
        f"upstream of the model. detail={detail}"
    )
    # LLM call counters should reflect that we actually talked to a
    # model (not just no-op'd through the pipeline).
    assert detail.get("llm_total_calls", 0) > 0
    assert detail.get("llm_successful_calls", 0) > 0
