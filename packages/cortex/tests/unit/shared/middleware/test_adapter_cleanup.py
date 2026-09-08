# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for AdapterCleanupMiddleware.

The middleware exists to prevent connection-pool exhaustion, so its
contract has two halves that pull in opposite directions and both need
pinning:

* teardown must not happen too EARLY — an adapter must stay connected for
  as long as the handler (including a streaming body generator) can still
  use it;
* teardown must not be MISSED — it has to run on every exit path,
  including a client disconnect and an exception raised mid-stream.

The early-teardown half is the regression guard for the streaming bug:
``BaseHTTPMiddleware`` hands back control when the response object is
produced, not when the body has been sent, so a ``finally`` around
``call_next`` disconnected adapters while an SSE generator was still
running.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from sse_starlette.sse import EventSourceResponse

from chaoscypher_core.database.adapter_factory import _request_adapters
from chaoscypher_cortex.shared.middleware.adapter_cleanup import (
    AdapterCleanupMiddleware,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


class FakeAdapter:
    """Stand-in for SqliteAdapter: records teardown, refuses use once closed."""

    def __init__(self) -> None:
        self.connected = True
        self.disconnect_calls = 0

    def disconnect(self) -> None:
        self.connected = False
        self.disconnect_calls += 1

    def read(self) -> str:
        """Mimic a storage call guarded by SqliteAdapter._ensure_connected."""
        if not self.connected:
            msg = "SqliteAdapter is not connected. Call connect() before ..."
            raise RuntimeError(msg)
        return "ok"


def _register() -> FakeAdapter:
    """Mimic get_sqlite_adapter's registration into the request-scoped list."""
    adapter = FakeAdapter()
    _request_adapters.get().append(adapter)
    return adapter


def _app(
    route_factory: Callable[[list[FakeAdapter], list[str]], Any],
) -> tuple[FastAPI, list, list]:
    """Build an app with the middleware, exposing created adapters + a trace."""
    created: list[FakeAdapter] = []
    trace: list[str] = []
    app = FastAPI()
    route_factory(created, trace)(app)
    app.add_middleware(AdapterCleanupMiddleware)
    return app, created, trace


def test_adapter_disconnected_after_plain_response() -> None:
    """The baseline the middleware exists for: no connection is left open."""

    def routes(created: list[FakeAdapter], trace: list[str]) -> Callable:
        def register(app: FastAPI) -> None:
            @app.get("/plain")
            def plain() -> dict:
                created.append(_register())
                return {"ok": True}

        return register

    app, created, _ = _app(routes)
    assert TestClient(app).get("/plain").status_code == 200
    assert len(created) == 1
    assert created[0].connected is False
    assert created[0].disconnect_calls == 1


def test_adapter_stays_connected_for_the_whole_streaming_body() -> None:
    """Regression guard: teardown must not land while the body is streaming.

    The generator suspends (as the chat SSE endpoint does on ``await
    pubsub.subscribe(...)``) and then uses the adapter again. Under
    ``BaseHTTPMiddleware`` that second use raised RuntimeError, because
    ``call_next`` had already returned and teardown had already run.
    """

    def routes(created: list[FakeAdapter], trace: list[str]) -> Callable:
        def register(app: FastAPI) -> None:
            @app.get("/stream")
            def stream() -> StreamingResponse:
                adapter = _register()
                created.append(adapter)

                async def body() -> AsyncIterator[bytes]:
                    yield b"first\n"
                    await asyncio.sleep(0)  # the suspension point
                    try:
                        adapter.read()
                        trace.append("post_suspend_read_ok")
                    except RuntimeError:
                        trace.append("post_suspend_read_raised")
                    yield b"second\n"

                return StreamingResponse(body())

        return register

    app, created, trace = _app(routes)
    with TestClient(app).stream("GET", "/stream") as response:
        body = b"".join(response.iter_bytes())

    assert body == b"first\nsecond\n"
    assert trace == ["post_suspend_read_ok"]
    # ...and it is still torn down once the body is done.
    assert created[0].connected is False


def test_adapter_stays_connected_for_a_server_sent_events_body() -> None:
    """The same guarantee on the response class the chat endpoint returns."""

    def routes(created: list[FakeAdapter], trace: list[str]) -> Callable:
        def register(app: FastAPI) -> None:
            @app.get("/events")
            def events() -> EventSourceResponse:
                adapter = _register()
                created.append(adapter)

                async def body() -> AsyncIterator[dict]:
                    await asyncio.sleep(0)  # subscribe-shaped suspension
                    try:
                        adapter.read()
                        trace.append("reconcile_ok")
                    except RuntimeError:
                        trace.append("reconcile_raised")
                    yield {"event": "done", "data": "{}"}

                return EventSourceResponse(body())

        return register

    app, created, trace = _app(routes)
    with TestClient(app).stream("GET", "/events") as response:
        b"".join(response.iter_bytes())

    assert trace == ["reconcile_ok"]
    assert created[0].connected is False


def test_adapter_disconnected_when_endpoint_raises() -> None:
    """An exception must not leak the connection."""

    def routes(created: list[FakeAdapter], trace: list[str]) -> Callable:
        def register(app: FastAPI) -> None:
            @app.get("/boom")
            def boom() -> dict:
                created.append(_register())
                msg = "boom"
                raise ValueError(msg)

        return register

    app, created, _ = _app(routes)
    with pytest.raises(ValueError, match="boom"):
        TestClient(app).get("/boom")
    assert created[0].connected is False


def test_adapter_disconnected_when_streaming_body_raises_midway() -> None:
    """A mid-stream failure must not leak the connection either."""

    def routes(created: list[FakeAdapter], trace: list[str]) -> Callable:
        def register(app: FastAPI) -> None:
            @app.get("/halfboom")
            def halfboom() -> StreamingResponse:
                created.append(_register())

                async def body() -> AsyncIterator[bytes]:
                    yield b"first\n"
                    msg = "mid-stream failure"
                    raise ValueError(msg)

                return StreamingResponse(body())

        return register

    app, created, _ = _app(routes)
    with (
        pytest.raises(ValueError, match="mid-stream failure"),
        TestClient(app).stream("GET", "/halfboom") as response,
    ):
        b"".join(response.iter_bytes())
    assert created[0].connected is False


@pytest.mark.asyncio
async def test_adapter_disconnected_when_client_disconnects_mid_stream() -> None:
    """A client hanging up on an open stream must still release the adapter.

    Driven as raw ASGI because a client disconnect cannot be expressed
    through TestClient.
    """
    created: list[FakeAdapter] = []
    app = FastAPI()

    @app.get("/forever")
    def forever() -> StreamingResponse:
        created.append(_register())

        async def body() -> AsyncIterator[bytes]:
            while True:
                yield b"tick\n"
                await asyncio.sleep(0.01)

        return StreamingResponse(body())

    app.add_middleware(AdapterCleanupMiddleware)

    disconnected = asyncio.Event()
    sent_chunks = 0

    async def receive() -> dict:
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        nonlocal sent_chunks
        if message["type"] == "http.response.body" and message.get("body"):
            sent_chunks += 1
            if sent_chunks >= 2:
                disconnected.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/forever",
        "raw_path": b"/forever",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }

    await asyncio.wait_for(app(scope, receive, send), timeout=5.0)

    assert created, "endpoint never ran"
    assert created[0].connected is False


@pytest.mark.asyncio
async def test_non_http_scope_passes_through_without_tracking() -> None:
    """Lifespan/websocket scopes create no adapters and must not be wrapped."""
    seen: list[str] = []

    async def downstream(scope: dict, receive: Any, send: Any) -> None:
        seen.append(scope["type"])
        # No request-scoped list should have been installed for this scope.
        with pytest.raises(LookupError):
            _request_adapters.get()

    middleware = AdapterCleanupMiddleware(downstream)
    await middleware({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"]
