# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for top-level Cortex middleware."""

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from chaoscypher_cortex.middleware import enforce_body_size_limit


def _request_with_content_length(value: str, path: str = "/api/v1/test") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"content-length", value.encode("ascii"))],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


@pytest.mark.asyncio
async def test_body_size_middleware_rejects_malformed_content_length() -> None:
    async def call_next(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    response = await enforce_body_size_limit(_request_with_content_length("nope"), call_next)

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_body_size_middleware_rejects_negative_content_length() -> None:
    async def call_next(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    response = await enforce_body_size_limit(_request_with_content_length("-1"), call_next)

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_body_size_middleware_rejects_oversize_on_non_upload_route() -> None:
    """At the 128 MB default a 256 MB POST to a JSON endpoint is rejected."""

    async def call_next(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    oversize = str(256 * 1024 * 1024)  # 256 MB
    response = await enforce_body_size_limit(
        _request_with_content_length(oversize, path="/api/v1/chats/c1/messages"),
        call_next,
    )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_body_size_middleware_skips_check_on_upload_route() -> None:
    """Upload routes are exempted — they enforce ``max_upload_bytes`` themselves.

    A 1 GB POST to ``/api/v1/sources`` must pass through middleware untouched
    (the route's own UploadService cap takes over inside the handler).
    """

    async def call_next(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    gigabyte = str(1024 * 1024 * 1024)
    response = await enforce_body_size_limit(
        _request_with_content_length(gigabyte, path="/api/v1/sources"),
        call_next,
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_body_size_middleware_skips_check_on_batch_upload_route() -> None:
    async def call_next(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    gigabyte = str(1024 * 1024 * 1024)
    response = await enforce_body_size_limit(
        _request_with_content_length(gigabyte, path="/api/v1/sources/batch"),
        call_next,
    )

    assert response.status_code == 200


def _chunked_request(
    chunks: list[bytes],
    *,
    path: str = "/api/v1/test",
    method: str = "POST",
) -> Request:
    headers = [(b"transfer-encoding", b"chunked")]
    pending = list(chunks)

    async def receive() -> dict[str, object]:
        if pending:
            chunk = pending.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(pending)}
        return {"type": "http.disconnect"}

    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": headers,
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        },
        receive=receive,
    )


@pytest.mark.asyncio
async def test_body_size_middleware_rejects_oversize_chunked_body() -> None:
    """At the 128 MB default a 256 MB chunked POST is rejected."""

    async def call_next(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    one_mb = b"x" * (1024 * 1024)
    oversize_chunks = [one_mb for _ in range(256)]  # 256 MB
    response = await enforce_body_size_limit(
        _chunked_request(oversize_chunks, path="/api/v1/chats/c1/messages"),
        call_next,
    )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_body_size_middleware_passes_small_chunked_body() -> None:
    """A small chunked POST below the cap is forwarded with body cached."""
    captured_body: dict[str, bytes] = {}

    async def call_next(request: Request) -> JSONResponse:
        captured_body["value"] = await request.body()
        return JSONResponse({"ok": True})

    payload = [b"hello, ", b"chunked ", b"world"]
    response = await enforce_body_size_limit(
        _chunked_request(payload, path="/api/v1/chats/c1/messages"),
        call_next,
    )

    assert response.status_code == 200
    assert captured_body["value"] == b"hello, chunked world"


@pytest.mark.asyncio
async def test_body_size_middleware_skips_chunked_check_on_upload_route() -> None:
    """Upload routes bypass the chunked path too."""

    async def call_next(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    one_mb = b"x" * (1024 * 1024)
    oversize_chunks = [one_mb for _ in range(256)]
    response = await enforce_body_size_limit(
        _chunked_request(oversize_chunks, path="/api/v1/sources"),
        call_next,
    )

    assert response.status_code == 200
