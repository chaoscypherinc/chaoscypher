# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the branded error template + content-negotiation helper."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from chaoscypher_cortex.shared.errors.branded_page import (
    negotiated_error_response,
    render_branded_error,
)


def _request(accept: str = "") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"accept", accept.encode())] if accept else [],
        "query_string": b"",
    }
    return Request(scope)


def test_render_returns_full_html_document() -> None:
    html = render_branded_error(
        status_code=421,
        title="Test title",
        lead="Test lead.",
        http_label="HTTP 421 Test",
    )
    assert html.lstrip().startswith("<!doctype html>")
    assert "</html>" in html
    assert "Test title" in html
    assert "Test lead." in html


def test_render_escapes_html_in_every_field() -> None:
    payload = {
        "status_code": 500,
        "title": "<script>alert('t')</script>",
        "lead": "<img src=x onerror=alert(1)>",
        "details": [("<b>label</b>", "<i>value</i>")],
        "why": "<svg onload=alert(2)>",
        "fix": ["<script>1</script>"],
        "http_label": "<x>",
    }
    html = render_branded_error(**payload)
    assert "<script>alert('t')</script>" not in html
    assert "&lt;script&gt;alert(&#x27;t&#x27;)&lt;/script&gt;" in html
    assert "<img src=x" not in html
    assert "<svg onload" not in html
    assert "<b>label</b>" not in html
    assert "&lt;b&gt;label&lt;/b&gt;" in html


def test_render_includes_details_rows() -> None:
    html = render_branded_error(
        status_code=421,
        title="t",
        lead="l",
        details=[("You tried", "example.com"), ("Allowed", "localhost")],
    )
    assert "You tried" in html
    assert "example.com" in html
    assert "Allowed" in html
    assert "localhost" in html


def test_render_includes_fix_steps_as_ordered_list() -> None:
    html = render_branded_error(
        status_code=421,
        title="t",
        lead="l",
        fix=["First step", "Second step"],
    )
    assert "<ol" in html
    assert "First step" in html
    assert "Second step" in html


def test_negotiated_returns_html_for_text_html_accept() -> None:
    request = _request(accept="text/html,application/xhtml+xml")
    response = negotiated_error_response(
        request,
        status_code=421,
        error_code="host_not_allowed",
        json_payload={"error": "host_not_allowed"},
        html_kwargs={"title": "t", "lead": "l"},
    )
    assert isinstance(response, HTMLResponse)
    assert response.status_code == 421


def test_negotiated_returns_json_for_application_json_accept() -> None:
    request = _request(accept="application/json")
    response = negotiated_error_response(
        request,
        status_code=429,
        error_code="rate_limited",
        json_payload={"error": "rate_limited"},
        html_kwargs={"title": "t", "lead": "l"},
    )
    assert isinstance(response, JSONResponse)
    assert response.status_code == 429


def test_negotiated_defaults_to_json_when_accept_missing() -> None:
    request = _request(accept="")
    response = negotiated_error_response(
        request,
        status_code=413,
        error_code="body_too_large",
        json_payload={"error": "body_too_large"},
        html_kwargs={"title": "t", "lead": "l"},
    )
    assert isinstance(response, JSONResponse)


def test_negotiated_forwards_headers() -> None:
    request = _request(accept="text/html")
    response = negotiated_error_response(
        request,
        status_code=429,
        error_code="rate_limited",
        json_payload={"error": "rate_limited"},
        html_kwargs={"title": "t", "lead": "l"},
        headers={"Retry-After": "60"},
    )
    assert response.headers["retry-after"] == "60"
