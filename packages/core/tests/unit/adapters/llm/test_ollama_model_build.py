# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""What a benchmark row records about the Ollama build it ran on (audit wave 4, 2026-10-06).

An ``ollama pull`` or a server upgrade between a baseline and a variant changed
the weights or the Modelfile defaults with no row field moving. These tests
drive :func:`ollama_model_build` through a fake server answering with the real
``/api/version``, ``/api/tags`` and ``/api/show`` shapes, and pin the stream
error chunks that tell a model error from a dropped connection.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from ollama._types import ResponseError

from chaoscypher_core.adapters.llm.providers.ollama_provider import (
    MODEL_BUILD_KEYS,
    ollama_model_build,
    ollama_runtime_options,
    parse_modelfile_parameters,
    stream_error_chunk,
)
from chaoscypher_core.exceptions import LLMError, LLMModelError, LLMServiceError
from chaoscypher_core.utils.stream_errors import error_from_stream_chunk, retry_budget


# /api/show as Ollama 0.12 answers it (abridged: modelfile, template, model_info dropped)
_SHOW = {
    "license": "Apache License",
    "parameters": (
        "repeat_penalty                 1\n"
        'stop                           "<|im_start|>"\n'
        'stop                           "<|im_end|>"\n'
        "temperature                    0.6\n"
        "top_k                          20\n"
        "top_p                          0.95"
    ),
    "details": {"family": "qwen3", "parameter_size": "8.2B", "quantization_level": "Q4_K_M"},
    "capabilities": ["completion", "tools", "thinking"],
    "modified_at": "2026-10-01T10:00:00.000000000+02:00",
}
_TAGS = {
    "models": [
        {"name": "gemma4:26b", "model": "gemma4:26b", "digest": "aaaa", "size": 1},
        {"name": "qwen3:8b", "model": "qwen3:8b", "digest": "500a1f067a9f", "size": 2},
    ]
}
_VERSION = {"version": "0.12.3"}


def _server(seen: list[tuple[str, str, Any]]) -> httpx.MockTransport:
    """A fake Ollama answering the three calls; records each request."""

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        payload = {"/api/version": _VERSION, "/api/tags": _TAGS, "/api/show": _SHOW}.get(
            request.url.path
        )
        return httpx.Response(200, json=payload) if payload else httpx.Response(404)

    return httpx.MockTransport(handle)


def test_the_modelfile_parameters_parse_to_values_and_a_stop_list() -> None:
    """Padded names, numbers as numbers, quotes dropped, a repeated name a list."""
    assert parse_modelfile_parameters(_SHOW["parameters"]) == {
        "repeat_penalty": 1,
        "stop": ["<|im_start|>", "<|im_end|>"],
        "temperature": 0.6,
        "top_k": 20,
        "top_p": 0.95,
    }
    assert parse_modelfile_parameters(None) == {} and parse_modelfile_parameters("") == {}


@pytest.mark.asyncio
async def test_the_build_holds_the_digest_the_server_and_the_defaults() -> None:
    """Every key, from the three calls; the model is named to /api/show."""
    seen: list[tuple[str, str, Any]] = []
    build = await ollama_model_build(
        "http://gpu:11434/",
        "qwen3:8b",
        options={"num_batch": 512, "num_thread": None, "num_parallel": 2},
        transport=_server(seen),
    )
    assert tuple(build) == MODEL_BUILD_KEYS
    assert build["digest"] == "500a1f067a9f"
    assert build["server_version"] == "0.12.3"
    assert build["parameters"]["repeat_penalty"] == 1
    assert build["parameters"]["stop"] == ["<|im_start|>", "<|im_end|>"]
    assert (build["num_batch"], build["num_thread"], build["num_parallel"]) == (512, None, 2)
    assert ("POST", "/api/show", {"model": "qwen3:8b"}) in seen
    assert {path for _, path, _ in seen} == {"/api/version", "/api/tags", "/api/show"}


@pytest.mark.asyncio
async def test_an_untagged_name_finds_its_latest_digest() -> None:
    """``qwen3`` is ``qwen3:latest`` in /api/tags."""
    tags = {"models": [{"name": "qwen3:latest", "model": "qwen3:latest", "digest": "ffff"}]}
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json=tags if r.url.path == "/api/tags" else {})
    )
    build = await ollama_model_build("http://h:11434", "qwen3", transport=transport)
    assert build["digest"] == "ffff"
    assert build["server_version"] is None and build["parameters"] == {}


@pytest.mark.asyncio
async def test_an_unreachable_server_records_nothing_and_does_not_raise() -> None:
    """A fact the server cannot report is None; the sweep goes on."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    build = await ollama_model_build(
        "http://h:11434",
        "qwen3:8b",
        options={"num_batch": 256},
        transport=httpx.MockTransport(refuse),
    )
    assert build == {
        "digest": None,
        "server_version": None,
        "parameters": None,
        "num_batch": 256,
        "num_thread": None,
        "num_parallel": None,
        "repeat_penalty": None,
    }


def test_the_runtime_options_are_what_the_provider_sends() -> None:
    """From the provider config or the settings alike; an unset (or 0) option is not sent."""
    config = {"ollama_num_batch": 512, "ollama_num_thread": 0, "ollama_num_ctx": 8192}
    assert ollama_runtime_options(config) == {
        "num_batch": 512,
        "num_thread": None,
        "num_parallel": None,
    }
    settings = SimpleNamespace(ollama_num_batch=None, ollama_num_thread=8, ollama_num_parallel=2)
    assert ollama_runtime_options(settings) == {
        "num_batch": None,
        "num_thread": 8,
        "num_parallel": 2,
    }


def test_the_provider_builds_its_request_options_from_the_same_helper() -> None:
    """The recorded values are the sent ones: ``_init_llm`` passes exactly the helper's set."""
    captured: list[dict[str, Any]] = []

    def fake_chat_ollama(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return SimpleNamespace()

    from chaoscypher_core.adapters.llm.providers.ollama_provider import OllamaProvider

    config = {
        "chat_provider": "ollama",
        "llm_max_concurrent": 1,
        "llm_reserved_interactive": 0,
        "llm_enable_priority": False,
        "llm_request_timeout": 30,
        "base_url": "http://localhost:11434",
        "ollama_chat_model": "qwen3:8b",
        "stream_chunk_timeout": 30.0,
        "ollama_health_check_timeout": 5.0,
        "ollama_recovery_delay": 0.0,
        "ollama_num_batch": 512,
        "ollama_num_parallel": 2,
    }
    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.ChatOllama",
        side_effect=fake_chat_ollama,
    ):
        OllamaProvider(config)
    sent = {k: captured[-1].get(k) for k in ("num_batch", "num_thread", "num_parallel")}
    assert sent == ollama_runtime_options(config)
    assert "num_thread" not in captured[-1]


@pytest.mark.parametrize(
    ("raised", "kind", "budget"),
    [
        (ResponseError("model 'x' not found", 404), LLMModelError, 0),
        # a crashed runner usually comes back once (audit wave 5; wave 4 made it fatal)
        (ResponseError("llama runner process has terminated", 500), LLMServiceError, 1),
        (ResponseError("server busy, please try again", 503), LLMServiceError, 3),
        (ResponseError("too many requests", 429), LLMError, 3),
        (httpx.ReadError("peer reset"), LLMServiceError, 3),
        (TimeoutError(), LLMServiceError, 3),
        (ResponseError("invalid option"), LLMError, 0),
    ],
)
def test_a_stream_failure_reads_back_as_the_error_the_provider_classified(
    raised: Exception, kind: type[LLMError], budget: int
) -> None:
    """Audit waves 4-5: the chunk carries the one classifier's verdict, read back unchanged."""
    error = error_from_stream_chunk(stream_error_chunk(raised, "qwen3:14b"))
    assert isinstance(error, kind)
    assert retry_budget(error, 3) == budget
    assert error.model == "qwen3:14b"


def test_both_provider_factories_copy_the_runtime_options_from_one_source() -> None:
    """The single-instance factory and the load balancer build providers from ``ollama_runtime_config``.

    Two hand-written lists drifted once (audit wave 4): what a provider sends is
    what the settings say only when both copy the same keys, read back by
    ``ollama_runtime_options``.
    """
    from chaoscypher_core.adapters.llm.factory import ProviderFactory
    from chaoscypher_core.adapters.llm.providers.ollama_provider import (
        OLLAMA_RUNTIME_OPTIONS,
        ollama_runtime_config,
        ollama_runtime_options,
    )
    from chaoscypher_core.settings import EngineSettings, LLMSettings

    llm = LLMSettings(
        ollama_num_batch=96, ollama_num_thread=7, ollama_num_parallel=2, ollama_num_ctx=4096
    )
    factory = ProviderFactory(EngineSettings(llm=llm))
    config = ollama_runtime_config(llm)
    assert config == {
        "ollama_num_batch": 96,
        "ollama_num_thread": 7,
        "ollama_num_parallel": 2,
        "ollama_num_ctx": 4096,
    }
    assert ollama_runtime_options(config) == {
        name: config[f"ollama_{name}"] for name in OLLAMA_RUNTIME_OPTIONS
    }
    assert {k: v for k, v in factory._get_provider_config().items() if k in config} == config


def test_the_options_chatollama_drops_are_recorded_as_not_sent() -> None:
    """Audit wave 5: num_batch and num_parallel were recorded as sent; ChatOllama ignores both."""
    from langchain_ollama import ChatOllama

    from chaoscypher_core.adapters.llm.providers.ollama_provider import (
        OLLAMA_OPTIONS_NOT_SENT,
        ollama_runtime_options_record,
    )

    # read from ChatOllama itself: a langchain-ollama that adds the field moves this
    assert {"num_batch", "num_parallel"} <= OLLAMA_OPTIONS_NOT_SENT
    assert "num_batch" not in ChatOllama.model_fields
    record = ollama_runtime_options_record(
        {"ollama_num_batch": 512, "ollama_num_thread": 8, "ollama_num_parallel": None}
    )
    assert record == {
        "num_batch": {"value": 512, "sent": False},
        "num_thread": {"value": 8, "sent": True},
        "num_parallel": {"value": None, "sent": False},
        # a per-call option: sent only under an override (the retry ladder's rung 2)
        "repeat_penalty": {"value": None, "sent": False},
    }
