# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage-focused unit tests for OllamaModelsService.

Targets the streaming ``pull_model`` SSE generator (instance_id injection,
non-JSON passthrough, malformed-JSON fallback, non-200 status, and the
ConnectError / HTTPError / generic exception branches) plus the failure
branches of ``list_models`` and ``remove_model`` not covered elsewhere.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from chaoscypher_cortex.features.settings.ollama_models_service import (
    OllamaModelsService,
)


def _make_service(extra_instances: list[dict] | None = None) -> OllamaModelsService:
    """Build a service with one (or more) enabled instance."""
    instances = [
        {
            "id": "default",
            "name": "Default",
            "base_url": "http://localhost:11434",
            "enabled": True,
            "healthy": True,
        }
    ]
    if extra_instances:
        instances.extend(extra_instances)
    return OllamaModelsService(instances=instances, timeout=5)


def _async_iter(lines: list[str]):
    """Build an async iterator yielding the given lines (for aiter_lines)."""

    async def _gen():
        for line in lines:
            yield line

    return _gen()


def _stream_client(*, status_code: int, lines: list[str]):
    """Build a mocked AsyncClient whose .stream() yields a streamed response."""
    response = MagicMock()
    response.status_code = status_code
    response.aiter_lines = lambda: _async_iter(lines)

    @asynccontextmanager
    async def _stream(*_args, **_kwargs):
        yield response

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.stream = _stream
    return client


# ---------------------------------------------------------------------------
# pull_model — success streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_model_injects_instance_id_into_json_lines() -> None:
    """JSON progress lines are augmented with the originating instance_id."""
    service = _make_service()
    client = _stream_client(
        status_code=200,
        lines=[
            json.dumps({"status": "pulling manifest"}),
            "",  # blank line skipped
            json.dumps({"status": "success"}),
        ],
    )

    with patch("httpx.AsyncClient", return_value=client):
        collected = [line async for line in service.pull_model("qwen3:30b", instance_id="default")]

    parsed = [json.loads(line) for line in collected]
    assert all(p["instance_id"] == "default" for p in parsed)
    assert parsed[0]["status"] == "pulling manifest"
    assert parsed[-1]["status"] == "success"


@pytest.mark.asyncio
async def test_pull_model_passes_through_non_json_lines() -> None:
    """Lines that are not JSON objects are yielded verbatim."""
    service = _make_service()
    client = _stream_client(status_code=200, lines=["plain progress text"])

    with patch("httpx.AsyncClient", return_value=client):
        collected = [line async for line in service.pull_model("qwen3:30b")]

    assert collected == ["plain progress text"]


@pytest.mark.asyncio
async def test_pull_model_falls_back_on_malformed_json() -> None:
    """A line that starts with '{' but is invalid JSON is yielded raw."""
    service = _make_service()
    client = _stream_client(status_code=200, lines=["{not valid json"])

    with patch("httpx.AsyncClient", return_value=client):
        collected = [line async for line in service.pull_model("qwen3:30b")]

    assert collected == ["{not valid json"]


@pytest.mark.asyncio
async def test_pull_model_non_200_yields_error_and_skips_stream() -> None:
    """A non-200 status yields a single structured error and continues."""
    service = _make_service()
    client = _stream_client(status_code=500, lines=["should not be read"])

    with patch("httpx.AsyncClient", return_value=client):
        collected = [line async for line in service.pull_model("qwen3:30b")]

    assert len(collected) == 1
    err = json.loads(collected[0])
    assert err["status"] == "error"
    assert err["instance_id"] == "default"
    assert "HTTP 500" in err["error"]


# ---------------------------------------------------------------------------
# pull_model — error branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_model_connect_error_yields_connection_refused() -> None:
    """A ConnectError surfaces a 'Connection refused' structured error."""
    service = _make_service()
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    def _raise_connect(*_a, **_k):
        raise httpx.ConnectError("refused")

    client.stream = _raise_connect

    with patch("httpx.AsyncClient", return_value=client):
        collected = [line async for line in service.pull_model("qwen3:30b")]

    err = json.loads(collected[0])
    assert err["status"] == "error"
    assert err["error"] == "Connection refused"


@pytest.mark.asyncio
async def test_pull_model_http_error_yields_upstream_error() -> None:
    """A generic httpx.HTTPError surfaces an 'Upstream HTTP error' message."""
    service = _make_service()
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    def _raise_http(*_a, **_k):
        raise httpx.ReadTimeout("slow")

    client.stream = _raise_http

    with patch("httpx.AsyncClient", return_value=client):
        collected = [line async for line in service.pull_model("qwen3:30b")]

    err = json.loads(collected[0])
    assert err["error"] == "Upstream HTTP error during pull"


@pytest.mark.asyncio
async def test_pull_model_unexpected_exception_yields_generic_error() -> None:
    """A non-httpx exception surfaces the 'Unexpected pull failure' message."""
    service = _make_service()
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    def _raise_runtime(*_a, **_k):
        raise RuntimeError("boom")

    client.stream = _raise_runtime

    with patch("httpx.AsyncClient", return_value=client):
        collected = [line async for line in service.pull_model("qwen3:30b")]

    err = json.loads(collected[0])
    assert err["error"] == "Unexpected pull failure"


@pytest.mark.asyncio
async def test_pull_model_unknown_instance_raises() -> None:
    """Targeting a missing instance raises ValueError before any HTTP call."""
    service = _make_service()
    with pytest.raises(ValueError, match="Instance 'ghost' not found"):
        async for _ in service.pull_model("qwen3:30b", instance_id="ghost"):
            pass


# ---------------------------------------------------------------------------
# list_models — generic (non-connect) exception branch + multi-instance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_models_generic_exception_marks_unhealthy() -> None:
    """A non-connect exception during listing marks the instance unhealthy."""
    service = _make_service()
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=RuntimeError("kaboom"))

    with patch("httpx.AsyncClient", return_value=client):
        result = await service.list_models()

    assert result.instances[0].healthy is False
    assert result.instances[0].models == []


@pytest.mark.asyncio
async def test_list_models_non_200_yields_no_models_but_healthy() -> None:
    """A non-200 tags response leaves the instance healthy with zero models."""
    service = _make_service()
    response = MagicMock()
    response.status_code = 404
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=response)

    with patch("httpx.AsyncClient", return_value=client):
        result = await service.list_models()

    # No exception raised, so healthy stays True; models list is empty.
    assert result.instances[0].healthy is True
    assert result.instances[0].models == []


# ---------------------------------------------------------------------------
# remove_model — partial failure + exception branches; broadcast to all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_model_reports_failure_on_non_200() -> None:
    """A non-200 delete yields success=False for that instance."""
    service = _make_service()
    response = MagicMock()
    response.status_code = 500
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.request = AsyncMock(return_value=response)

    with patch("httpx.AsyncClient", return_value=client):
        result = await service.remove_model("qwen3:30b", instance_id="default")

    assert result["success"] is False
    assert result["results"][0]["success"] is False


@pytest.mark.asyncio
async def test_remove_model_exception_records_error_entry() -> None:
    """An exception during delete records an error result for the instance."""
    service = _make_service()
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.request = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with patch("httpx.AsyncClient", return_value=client):
        result = await service.remove_model("qwen3:30b", instance_id="default")

    assert result["success"] is False
    assert result["results"][0]["error"] == "Model removal failed"


@pytest.mark.asyncio
async def test_remove_model_broadcasts_to_all_instances() -> None:
    """With no instance_id, remove targets every enabled instance."""
    service = _make_service(
        extra_instances=[
            {
                "id": "secondary",
                "name": "Secondary",
                "base_url": "http://localhost:11435",
                "enabled": True,
            }
        ]
    )
    response = MagicMock()
    response.status_code = 200
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.request = AsyncMock(return_value=response)

    with patch("httpx.AsyncClient", return_value=client):
        result = await service.remove_model("qwen3:30b")

    assert result["success"] is True
    assert {r["instance_id"] for r in result["results"]} == {"default", "secondary"}


def test_disabled_instances_are_filtered_out() -> None:
    """Instances with enabled=False are excluded from the service registry."""
    service = OllamaModelsService(
        instances=[
            {"id": "on", "base_url": "http://a", "enabled": True},
            {"id": "off", "base_url": "http://b", "enabled": False},
        ],
        timeout=5,
    )
    resolved = service._resolve_instances(None)
    assert [inst["id"] for inst in resolved] == ["on"]
