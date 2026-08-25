# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for the Ollama pull SSE endpoint.

Before 2026-04-18, the endpoint had no Request parameter and never
polled is_disconnected(); the server kept pulling multi-GB models
after the client closed the tab. And it used raw StreamingResponse
with no keep-alive, so nginx's 300s proxy_send_timeout killed idle
connections mid-layer-download.

2026-08-12: two of these tests read ``inspect.getsource(pull_ollama_model)``
and asserted the substrings ``"EventSourceResponse"`` and
``"is_disconnected"``. Deleting the bare ``return`` at
ollama_models_api.py:133 — so the loop logs
``ollama_pull_client_disconnected`` and then keeps iterating and yielding,
which is exactly the multi-GB-after-tab-close bug the module docstring
names — leaves ``"is_disconnected"`` in the source and both tests green. A
stray ``# is_disconnected`` comment would satisfy it too. Both endpoints
are trivially behaviour-testable (``request`` is an injected parameter and
``service.pull_model`` an injected async iterator), which is what they do
now.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sse_starlette import EventSourceResponse

from chaoscypher_cortex.features.settings import ollama_models_api


class _FakeRequest:
    """A Request stand-in whose disconnect flips after ``disconnect_after`` polls."""

    def __init__(self, disconnect_after: int | None = None) -> None:
        self._disconnect_after = disconnect_after
        self.poll_count = 0

    async def is_disconnected(self) -> bool:
        self.poll_count += 1
        if self._disconnect_after is None:
            return False
        return self.poll_count > self._disconnect_after


def _service_yielding(lines: list[str]) -> tuple[MagicMock, list[str]]:
    """A service whose pull_model yields ``lines``, recording what it produced."""
    produced: list[str] = []

    async def _pull_model(*, model: str, instance_id: str | None) -> AsyncIterator[str]:
        for line in lines:
            produced.append(line)
            yield line

    service = MagicMock()
    service.pull_model = _pull_model
    return service, produced


async def _call_endpoint(request: _FakeRequest, service: Any) -> EventSourceResponse:
    pull_request = MagicMock()
    pull_request.model = "qwen3:30b"
    pull_request.instance_id = None
    return await ollama_models_api.pull_ollama_model(
        "tester",
        request,  # type: ignore[arg-type]
        pull_request,
        service,
    )


async def _drain(response: EventSourceResponse) -> list[str]:
    return [chunk async for chunk in response.body_iterator]


def test_pull_endpoint_accepts_request_parameter() -> None:
    """The endpoint must accept a Request so it can poll is_disconnected()."""
    sig = inspect.signature(ollama_models_api.pull_ollama_model)
    assert any("Request" in str(p.annotation) for p in sig.parameters.values()), (
        "pull_ollama_model has no Request parameter"
    )


@pytest.mark.asyncio
async def test_pull_endpoint_returns_event_source_response() -> None:
    """The endpoint must return EventSourceResponse for auto-pings.

    A plain ``StreamingResponse`` has no keep-alive, so nginx's 300s
    proxy_send_timeout kills idle connections mid-layer-download. Asserting
    the returned *type* pins that rather than the spelling in the source.
    """
    service, _ = _service_yielding(['{"status": "success"}'])
    response = await _call_endpoint(_FakeRequest(), service)

    assert isinstance(response, EventSourceResponse)
    assert response.media_type == "text/event-stream"


@pytest.mark.asyncio
async def test_pull_stream_forwards_every_line_while_connected() -> None:
    """With the client attached, all upstream lines reach the browser."""
    lines = ['{"status": "downloading"}', '{"status": "downloading"}', '{"status": "success"}']
    service, _ = _service_yielding(lines)

    response = await _call_endpoint(_FakeRequest(), service)

    assert await _drain(response) == lines


@pytest.mark.asyncio
async def test_pull_stream_stops_when_the_client_disconnects() -> None:
    """On disconnect the stream RETURNS — it does not log and keep pulling.

    This is the 2026-04-18 regression: deleting the bare ``return`` after
    the ``ollama_pull_client_disconnected`` log leaves the loop iterating
    over a multi-GB download nobody is reading. The upstream iterator here
    would produce five lines; only the one yielded before the disconnect
    may reach the client, and the iterator must be abandoned early.
    """
    lines = [f'{{"status": "downloading", "layer": {i}}}' for i in range(5)]
    service, produced = _service_yielding(lines)
    request = _FakeRequest(disconnect_after=1)

    response = await _call_endpoint(request, service)
    delivered = await _drain(response)

    assert delivered == lines[:1], (
        f"stream kept yielding after the client disconnected: {delivered}"
    )
    assert len(produced) < len(lines), (
        f"upstream pull was driven to completion ({len(produced)}/{len(lines)} lines) "
        f"after the client disconnected"
    )
    assert request.poll_count >= 1, "is_disconnected was never polled"


@pytest.mark.asyncio
async def test_pull_stream_reports_upstream_failure_as_an_error_event() -> None:
    """An exploding pull yields one error event rather than propagating."""

    async def _boom(*, model: str, instance_id: str | None) -> AsyncIterator[str]:
        msg = "ollama exploded"
        raise RuntimeError(msg)
        yield  # pragma: no cover - makes this an async generator

    service = MagicMock()
    service.pull_model = _boom
    service.close = AsyncMock()

    response = await _call_endpoint(_FakeRequest(), service)
    delivered = await _drain(response)

    assert len(delivered) == 1
    payload = json.loads(delivered[0])
    assert payload["status"] == "error"
    # The upstream message must not leak to the browser.
    assert "ollama exploded" not in delivered[0]
