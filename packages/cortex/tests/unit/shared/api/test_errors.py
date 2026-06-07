# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the four FastAPI exception handlers in shared/api/errors.py.

Covers:
- ``chaoscypher_exception_handler``: status_map resolution (404/409/429/500),
  the Retry-After header for rate-limit errors, the ``details`` body branch,
  and the 4xx-vs-5xx logging level switch.
- ``http_exception_handler``: dict-with-code detail, plain-string detail,
  non-dict detail, and the ``field`` merge into ``details``.
- ``validation_exception_handler``: a real ``RequestValidationError`` whose
  ``ctx.error`` is a non-serializable ``ValueError`` (must be coerced to str),
  plus the non-RequestValidationError fall-through.
- ``global_exception_handler``: the fixed 500 envelope shape.

The handlers are pure async functions taking ``(request, exc)`` and returning a
``JSONResponse``. We parse ``response.body`` as JSON to assert the wire shape.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from chaoscypher_core.exceptions import (
    ChaosCypherException,
    LLMNotVerifiedError,
    LLMRateLimitError,
    NotFoundError,
)
from chaoscypher_cortex.shared.api.errors import (
    chaoscypher_exception_handler,
    global_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_request(path: str = "/api/v1/thing", method: str = "GET") -> MagicMock:
    """Build a minimal request stand-in exposing ``.url.path`` and ``.method``.

    The handlers only read ``request.url.path`` and ``request.method`` for
    logging, so a MagicMock with those attributes is sufficient and avoids
    constructing a full ASGI scope.
    """
    request = MagicMock()
    request.url.path = path
    request.method = method
    return request


def _body(response: JSONResponse) -> dict[str, Any]:
    """Decode a JSONResponse body to a dict."""
    return json.loads(bytes(response.body).decode("utf-8"))


# ---------------------------------------------------------------------------
# chaoscypher_exception_handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_domain_handler_not_found_maps_to_404() -> None:
    """NOT_FOUND code → HTTP 404 with the {error, message, details} envelope."""
    exc = NotFoundError("Workflow", "abc123")
    response = await chaoscypher_exception_handler(_fake_request(), exc)

    assert response.status_code == 404
    body = _body(response)
    assert body["error"] == "NOT_FOUND"
    assert body["message"] == "Workflow not found: abc123"
    # NotFoundError always carries details, so the details branch is exercised.
    assert body["details"]["resource_type"] == "Workflow"
    assert body["details"]["identifier"] == "abc123"


@pytest.mark.asyncio
async def test_domain_handler_llm_not_verified_maps_to_409() -> None:
    """LLM_NOT_VERIFIED code → HTTP 409 Conflict."""
    exc = LLMNotVerifiedError("ollama")
    response = await chaoscypher_exception_handler(_fake_request(), exc)

    assert response.status_code == 409
    body = _body(response)
    assert body["error"] == "LLM_NOT_VERIFIED"
    assert body["details"]["provider"] == "ollama"


@pytest.mark.asyncio
async def test_domain_handler_rate_limit_sets_retry_after_header() -> None:
    """LLM_RATE_LIMIT_ERROR → 429 and a Retry-After header from exc.retry_after."""
    exc = LLMRateLimitError("gemini", retry_after=60)
    response = await chaoscypher_exception_handler(_fake_request(), exc)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    body = _body(response)
    assert body["error"] == "LLM_RATE_LIMIT_ERROR"


@pytest.mark.asyncio
async def test_domain_handler_no_retry_after_header_when_absent() -> None:
    """A 429 without retry_after must NOT emit a Retry-After header."""
    # QUEUE_FULL maps to 429 but the exception has no retry_after attribute.
    exc = ChaosCypherException("Queue is full", code="QUEUE_FULL")
    response = await chaoscypher_exception_handler(_fake_request(), exc)

    assert response.status_code == 429
    assert "retry-after" not in {k.lower() for k in response.headers}


@pytest.mark.asyncio
async def test_domain_handler_unknown_code_defaults_to_500() -> None:
    """An unmapped code falls back to HTTP 500 (and logs at error level)."""
    exc = ChaosCypherException("boom", code="TOTALLY_UNKNOWN_CODE")
    response = await chaoscypher_exception_handler(_fake_request(), exc)

    assert response.status_code == 500
    body = _body(response)
    assert body["error"] == "TOTALLY_UNKNOWN_CODE"
    assert body["message"] == "boom"


@pytest.mark.asyncio
async def test_domain_handler_omits_details_when_empty() -> None:
    """No ``details`` key when exc.details is an empty dict."""
    exc = ChaosCypherException("plain message", code="INTERNAL_ERROR")
    response = await chaoscypher_exception_handler(_fake_request(), exc)

    assert response.status_code == 500
    body = _body(response)
    assert "details" not in body


@pytest.mark.asyncio
async def test_domain_handler_logs_warning_for_4xx(
    structlog_for_caplog: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """4xx domain errors log at WARNING (not ERROR)."""
    import logging

    exc = NotFoundError("Source", "s1")
    with caplog.at_level(logging.WARNING):
        await chaoscypher_exception_handler(_fake_request(path="/x", method="POST"), exc)

    records = [r for r in caplog.records if "domain_exception_raised" in r.getMessage()]
    assert records, "expected a domain_exception_raised log record"
    assert any(r.levelno == logging.WARNING for r in records)


@pytest.mark.asyncio
async def test_domain_handler_logs_error_for_5xx(
    structlog_for_caplog: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """5xx domain errors log at ERROR level (with exc_info)."""
    import logging

    exc = ChaosCypherException("kaboom", code="INTERNAL_ERROR")
    with caplog.at_level(logging.ERROR):
        await chaoscypher_exception_handler(_fake_request(), exc)

    records = [r for r in caplog.records if "domain_exception_raised" in r.getMessage()]
    assert records, "expected a domain_exception_raised log record"
    assert any(r.levelno == logging.ERROR for r in records)


# ---------------------------------------------------------------------------
# http_exception_handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_handler_dict_detail_with_code() -> None:
    """Dict detail with code/message/details maps to the unified envelope."""
    exc = HTTPException(
        status_code=404,
        detail={
            "code": "THING_NOT_FOUND",
            "message": "Thing missing",
            "details": {"id": "t1"},
        },
    )
    response = await http_exception_handler(_fake_request(), exc)

    assert response.status_code == 404
    body = _body(response)
    assert body["error"] == "THING_NOT_FOUND"
    assert body["message"] == "Thing missing"
    assert body["details"] == {"id": "t1"}


@pytest.mark.asyncio
async def test_http_handler_dict_detail_field_merges_into_details() -> None:
    """A ``field`` key with no ``details`` synthesizes details={'field': ...}."""
    exc = HTTPException(
        status_code=400,
        detail={"code": "VALIDATION_FAILED", "message": "bad", "field": "email"},
    )
    response = await http_exception_handler(_fake_request(), exc)

    assert response.status_code == 400
    body = _body(response)
    assert body["error"] == "VALIDATION_FAILED"
    assert body["details"]["field"] == "email"


@pytest.mark.asyncio
async def test_http_handler_dict_detail_missing_code_falls_back() -> None:
    """A dict detail with neither code nor message uses HTTP_<status> defaults."""
    exc = HTTPException(status_code=418, detail={"unrelated": "value"})
    response = await http_exception_handler(_fake_request(), exc)

    body = _body(response)
    assert body["error"] == "HTTP_418"
    assert body["message"] == "HTTP 418"


@pytest.mark.asyncio
async def test_http_handler_string_detail() -> None:
    """Plain-string detail → message=detail, error=HTTP_<status>."""
    exc = HTTPException(status_code=404, detail="Resource not found")
    response = await http_exception_handler(_fake_request(), exc)

    assert response.status_code == 404
    body = _body(response)
    assert body["error"] == "HTTP_404"
    assert body["message"] == "Resource not found"
    assert "details" not in body


@pytest.mark.asyncio
async def test_http_handler_non_dict_non_string_detail() -> None:
    """A non-dict, non-string detail is wrapped as details={'raw': str(detail)}."""
    exc = HTTPException(status_code=500, detail=[1, 2, 3])
    response = await http_exception_handler(_fake_request(), exc)

    body = _body(response)
    assert body["error"] == "HTTP_500"
    assert body["message"] == "HTTP 500"
    assert body["details"] == {"raw": "[1, 2, 3]"}


@pytest.mark.asyncio
async def test_http_handler_preserves_headers() -> None:
    """Headers on the HTTPException (e.g. Retry-After) survive into the response."""
    exc = HTTPException(
        status_code=429,
        detail="slow down",
        headers={"Retry-After": "30"},
    )
    response = await http_exception_handler(_fake_request(), exc)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"


# ---------------------------------------------------------------------------
# validation_exception_handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_handler_coerces_ctx_error_to_string() -> None:
    """A RequestValidationError whose ctx.error is a ValueError becomes a 422.

    The raw ``ValueError`` is not JSON-serializable; the handler must coerce
    ``ctx['error']`` to ``str`` so the body encodes cleanly.
    """
    raw_errors = [
        {
            "type": "value_error",
            "loc": ("body", "name"),
            "msg": "Value error, name is bad",
            "input": "xx",
            "ctx": {"error": ValueError("name is bad")},
        }
    ]
    exc = RequestValidationError(raw_errors)
    response = await validation_exception_handler(_fake_request(), exc)

    assert response.status_code == 422
    body = _body(response)
    assert body["error"] == "VALIDATION_FAILED"
    assert body["message"] == "Request body failed validation"
    errors = body["details"]["errors"]
    assert len(errors) == 1
    # ctx.error coerced to a string (the original ValueError repr text).
    assert errors[0]["ctx"]["error"] == "name is bad"
    # loc tuple normalized to a list.
    assert errors[0]["loc"] == ["body", "name"]


@pytest.mark.asyncio
async def test_validation_handler_non_request_validation_error() -> None:
    """A non-RequestValidationError yields an empty errors list (still 422)."""
    response = await validation_exception_handler(_fake_request(), ValueError("nope"))

    assert response.status_code == 422
    body = _body(response)
    assert body["error"] == "VALIDATION_FAILED"
    assert body["details"]["errors"] == []


# ---------------------------------------------------------------------------
# global_exception_handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_handler_returns_fixed_500_envelope(
    structlog_for_caplog: None,
) -> None:
    """Any unhandled exception → 500 with a fixed, leak-free envelope."""
    response = await global_exception_handler(
        _fake_request(path="/boom", method="DELETE"),
        RuntimeError("internal secret leaked path /etc/passwd"),
    )

    assert response.status_code == 500
    body = _body(response)
    assert body == {
        "error": "INTERNAL_ERROR",
        "message": "An unexpected error occurred",
    }
    # The raw exception text must NOT appear in the client body.
    assert "secret" not in json.dumps(body)
