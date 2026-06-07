# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared helpers for the e2e tests that lean on fake-ollama's
mutable test-control surface (``test_race_conditions.py``,
``test_worker_crash_recovery.py``).

The big load-bearing piece here is ``wait_for_queue_idle`` — both
tests set a global ``chat_delay_seconds`` on the fake-ollama which
slows down EVERY in-flight chat call, not just the one the test
is racing against. If an earlier test left a chunk-extraction task
mid-flight on the LLM queue, our delay would compound on its
latency too and our own task would never reach the worker within
the test's "wait for ``extracting``" timeout. Draining the queue
*before* we set the delay keeps the race scoped to the test's own
upload.
"""

from __future__ import annotations

import os
import time

import httpx


FAKE_OLLAMA_CONTROL_URL = os.environ.get(
    "E2E_FAKE_OLLAMA_CONTROL_URL", "http://localhost:11500"
)


def set_chat_delay(seconds: float) -> None:
    """Set fake-ollama's global ``chat_delay_seconds`` knob."""
    resp = httpx.post(
        f"{FAKE_OLLAMA_CONTROL_URL}/_fake/control",
        json={"chat_delay_seconds": seconds},
        timeout=5.0,
    )
    resp.raise_for_status()


def reset_fake_ollama() -> None:
    """Best-effort reset of all fake-ollama knobs (delay, pass-counter)."""
    try:
        httpx.post(f"{FAKE_OLLAMA_CONTROL_URL}/_fake/reset", timeout=5.0)
    except httpx.HTTPError:
        pass


def wait_for_queue_idle(
    client: httpx.Client, timeout: float = 30.0, stable_for: float = 0.5
) -> None:
    """Block until both Valkey queues are idle (queued == running == 0).

    ``stable_for`` requires the idle reading to persist for that
    long before returning — guards against a task being briefly
    between queues (operations → llm) where it's not visible on
    either side for one polling tick.

    Raises ``TimeoutError`` if the queues don't drain within
    ``timeout`` seconds. That's almost always a sign the test that
    just ran left work behind that's stuck on the slow fake-ollama
    delay — call ``reset_fake_ollama()`` first if you suspect that.
    """

    def _both_idle(body: dict) -> bool:
        for q in body.get("queues", []):
            if q.get("queued", 0) + q.get("running", 0) > 0:
                return False
        return True

    start = time.time()
    stable_since: float | None = None
    while time.time() - start < timeout:
        try:
            resp = client.get("/api/v1/queue/stats", timeout=5.0)
        except httpx.HTTPError:
            stable_since = None
            time.sleep(0.2)
            continue
        if resp.status_code == 200 and _both_idle(resp.json()):
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= stable_for:
                return
        else:
            stable_since = None
        time.sleep(0.2)
    # One last fetch to surface what was holding the queue.
    final = client.get("/api/v1/queue/stats", timeout=5.0)
    raise TimeoutError(
        f"queues did not idle within {timeout}s — last stats: {final.text!r}"
    )
