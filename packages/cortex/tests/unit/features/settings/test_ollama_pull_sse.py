# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for the Ollama pull SSE endpoint.

Before 2026-04-18, the endpoint had no Request parameter and never
polled is_disconnected(); the server kept pulling multi-GB models
after the client closed the tab. And it used raw StreamingResponse
with no keep-alive, so nginx's 300s proxy_send_timeout killed idle
connections mid-layer-download.
"""

from __future__ import annotations

import inspect

from chaoscypher_cortex.features.settings import ollama_models_api


def test_pull_endpoint_accepts_request_parameter() -> None:
    """The endpoint must accept a Request so it can poll is_disconnected()."""
    sig = inspect.signature(ollama_models_api.pull_ollama_model)
    assert any("Request" in str(p.annotation) for p in sig.parameters.values()), (
        "pull_ollama_model has no Request parameter"
    )


def test_pull_endpoint_uses_event_source_response() -> None:
    """The endpoint must return EventSourceResponse for auto-pings."""
    src = inspect.getsource(ollama_models_api.pull_ollama_model)
    assert "EventSourceResponse" in src, (
        "pull_ollama_model still uses plain StreamingResponse; switch to "
        "EventSourceResponse for 15s keep-alive pings"
    )


def test_pull_endpoint_polls_is_disconnected() -> None:
    """The endpoint's event_stream must call request.is_disconnected()."""
    src = inspect.getsource(ollama_models_api.pull_ollama_model)
    assert "is_disconnected" in src, (
        "pull_ollama_model's event_stream does not poll is_disconnected()"
    )
