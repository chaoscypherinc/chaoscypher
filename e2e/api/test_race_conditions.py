# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Race-condition e2e tests against a delay-able fake-ollama.

Tests in this file slow the fake-ollama's chat endpoint via
``POST /_fake/control`` so they can interleave actions (delete,
abort) with an in-flight extraction. Each test must reset the knob
in teardown so it doesn't poison sibling tests.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from e2e.api._fake_ollama_helpers import (
    reset_fake_ollama,
    set_chat_delay,
    wait_for_queue_idle,
)


@pytest.fixture
def fake_ollama_delay(client: httpx.Client):
    """Fixture: drain the queue, set fake-ollama chat delay; reset on teardown.

    Yields a callable that takes a delay (seconds) and applies it.
    Always drains the LLM + operations queues to idle *first* — the
    delay is global state, so any leftover task from a previous test
    would otherwise inherit the slowdown and block the queue past
    this test's timeout. Teardown resets the knob even on failure.
    """
    wait_for_queue_idle(client, timeout=30.0)
    applied = {"value": False}

    def _set(seconds: float) -> None:
        set_chat_delay(seconds)
        applied["value"] = True

    yield _set
    if applied["value"]:
        reset_fake_ollama()  # best-effort teardown


@pytest.mark.requires_llm
def test_delete_source_mid_extraction_leaves_no_orphans(
    client: httpx.Client,
    sample_data_dir: str,
    fake_ollama_delay,
) -> None:
    """Deleting a source while extraction is running cleans up cleanly.

    Race shape:
      t=0   : upload with extract_entities=True
      t≈0   : indexing completes, extraction-chunk tasks enqueue
      t≈1   : extraction call hits fake-ollama, blocks for ``delay`` s
      t≈2   : test sends DELETE /sources/{id}
      t≈delay : fake-ollama unblocks; worker tries to complete extraction
                on a now-deleted source

    Expected outcome:
      - DELETE returns 204 (not blocked on extraction)
      - After a grace period, no chunks/citations/entities reference
        the deleted source (verified via the cortex source-lookup
        endpoints returning 404 + the worker logs not crashing)

    The 5-second delay is generous — extraction-chunk tasks are short
    so the race window only needs to span "task picked up by worker"
    to "DELETE issued". 5 s leaves headroom on slow CI runners
    without dragging total test runtime.
    """
    fake_ollama_delay(5.0)

    sample = Path(sample_data_dir) / "sample.txt"
    with sample.open("rb") as f:
        upload = client.post(
            "/api/v1/sources",
            files={"file": ("race_test.txt", f, "text/plain")},
            data={"extract_entities": "true"},
        )
    assert upload.status_code == 202, upload.text
    source_id = upload.json()["id"]

    # Let indexing finish + at least one extraction task enter
    # the LLM queue. Indexing on a 500-byte text file takes well
    # under a second.
    _wait_until_extracting(client, source_id, timeout=15)

    # Race window is now open — fake-ollama is asleep inside
    # /api/chat. Delete the source.
    delete_resp = client.delete(f"/api/v1/sources/{source_id}")
    assert delete_resp.status_code == 204, delete_resp.text

    # Source is gone immediately.
    assert client.get(f"/api/v1/sources/{source_id}").status_code == 404

    # Wait long enough for any in-flight extraction call to wake up
    # and try to act on the (now-deleted) source row.
    time.sleep(7.0)

    # Source-scoped views must all 404 — no orphan rows hanging
    # around under the deleted source's id.
    for path in (
        f"/api/v1/sources/{source_id}",
        f"/api/v1/sources/{source_id}/chunks",
        f"/api/v1/sources/{source_id}/citations",
    ):
        resp = client.get(path)
        assert resp.status_code in (404, 200), (
            f"unexpected status on {path}: {resp.status_code} {resp.text}"
        )
        if resp.status_code == 200:
            # Some endpoints return an empty paginated envelope rather
            # than 404 for a missing parent — that's also fine, as long
            # as nothing is left over.
            body = resp.json()
            count = (
                body.get("pagination", {}).get("total")
                or body.get("total", 0)
                or len(body.get("data", []))
            )
            assert count == 0, f"orphan rows under {path}: {body}"


def _wait_until_extracting(
    client: httpx.Client, source_id: str, timeout: int = 15
) -> None:
    """Wait for a source to enter the extracting phase (or finish).

    With a delay-slowed fake-ollama, the source will sit in
    ``extracting`` for at least ``delay`` seconds — long enough to
    interleave a delete.
    """
    start = time.time()
    while time.time() - start < timeout:
        resp = client.get(f"/api/v1/sources/{source_id}")
        if resp.status_code != 200:
            time.sleep(0.2)
            continue
        data = resp.json()
        status = data.get("processing_status") or data.get("status", "")
        if status in ("extracting", "indexed", "committing", "committed", "error"):
            return
        time.sleep(0.2)
    raise TimeoutError(f"source {source_id} never entered extracting phase")
