# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Fake Ollama HTTP service for the e2e Docker stack.

Implements the four Ollama HTTP endpoints the cortex / neuron talk
to during the LLM-gated test paths: ``/api/tags`` (model list),
``/api/chat`` (chat + extraction, supports streaming NDJSON),
``/api/generate`` (single-shot), and ``/api/embed`` /
``/api/embeddings`` (vector embeddings).

The chat handler alternates between V2 pipe-delimited entity / edge
responses so the extraction pipeline's two-pass call (pass 1 = entities,
pass 2 = relationships referencing those entities) gets a coherent
canned answer per pair of calls. Other chat invocations (the
``/api/v1/chats/...`` user-facing flow) get a generic deterministic
reply.

Embeddings are deterministic 768-element vectors derived from a hash
of the input text — small, reproducible, sufficient for sqlite-vec
to index them and for similarity queries to return non-zero scores.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from aiohttp import web


# Models advertised by /api/tags. Must match the ollama_*_model names
# in the e2e settings.yaml so /api/v1/settings/llm/health flips to
# verified=True + missing_models=[].
CHAT_MODEL = "fake-chat:latest"
EXTRACTION_MODEL = "fake-extract:latest"
VISION_MODEL = "fake-vision:latest"
EMBEDDING_MODEL = "fake-embed:latest"

_TAG_MODELS: tuple[dict[str, Any], ...] = tuple(
    {
        "name": name,
        "model": name,
        "modified_at": "2026-01-01T00:00:00Z",
        "size": 1024 * 1024,
        "digest": hashlib.sha256(name.encode()).hexdigest(),
        "details": {
            "format": "gguf",
            "family": "test",
            "families": ["test"],
            "parameter_size": "0B",
            "quantization_level": "Q4_0",
        },
    }
    for name in (CHAT_MODEL, EXTRACTION_MODEL, VISION_MODEL, EMBEDDING_MODEL)
)


# V2 pipe-delimited canned responses for the extraction pipeline.
# Matches ``FakeLLMProvider`` in packages/core/tests/fakes/llm.py.
#   E|name|type|aliases|confidence|sent_ref|description
#   R|src_idx|dst_idx|type|confidence|sent_ref|justification
_EXTRACTION_PASS1 = (
    "E|Alice|Person|alice|0.9|S1|A character in the test fixture\n"
    "E|Bob|Person|bob|0.9|S2|Another character\n"
)
_EXTRACTION_PASS2 = "R|0|1|knows|0.9|S1-S2|They meet in the chunk\n"

_GENERIC_CHAT_REPLY = (
    "This is a deterministic fake-ollama chat response. "
    "The e2e stack uses this in place of a real LLM."
)


_pass_counter = {"value": 0}

# Mutable test-control knobs, set via POST /_fake/control. Lets a test
# slow down chat responses to open a race window (delete-mid-flight,
# worker-crash, abort-while-running) without depending on real LLM
# latency. State is process-global; tests are expected to reset.
_control_state: dict[str, float] = {"chat_delay_seconds": 0.0}


def _is_extraction_call(payload: dict[str, Any]) -> bool:
    """Heuristic: is this an extraction call or a user chat?

    Extraction-pipeline prompts contain the V2 grammar marker; user
    chats don't. Falling back to the generic reply for non-extraction
    paths keeps test_chat.* tests deterministic without breaking the
    extraction pipeline's parser.
    """
    messages = payload.get("messages") or []
    for msg in messages:
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if "E|name|type|" in content or "R|src_idx|dst_idx|" in content:
            return True
    return False


def _next_extraction_content() -> str:
    """Alternate pass1 / pass2 on successive extraction calls."""
    idx = _pass_counter["value"]
    _pass_counter["value"] = idx + 1
    return _EXTRACTION_PASS1 if idx % 2 == 0 else _EXTRACTION_PASS2


def _final_done_frame(model: str) -> dict[str, Any]:
    """Final NDJSON frame on a streaming chat — done=True + usage stats."""
    return {
        "model": model,
        "created_at": "2026-01-01T00:00:00Z",
        "message": {"role": "assistant", "content": ""},
        "done": True,
        "done_reason": "stop",
        "total_duration": 1_000_000,
        "load_duration": 0,
        "prompt_eval_count": 100,
        "prompt_eval_duration": 1_000,
        "eval_count": 200,
        "eval_duration": 1_000_000,
    }


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------


async def handle_tags(request: web.Request) -> web.Response:
    """GET /api/tags — list pulled models."""
    return web.json_response({"models": list(_TAG_MODELS)})


async def handle_show(request: web.Request) -> web.Response:
    """POST /api/show — model details (used by some Ollama clients)."""
    payload = await request.json()
    name = payload.get("name") or payload.get("model") or CHAT_MODEL
    return web.json_response(
        {
            "modelfile": "FROM scratch",
            "parameters": "",
            "template": "{{ .Prompt }}",
            "details": {
                "format": "gguf",
                "family": "test",
                "parameter_size": "0B",
                "quantization_level": "Q4_0",
            },
            "model_info": {
                "general.architecture": "test",
                "general.parameter_count": 0,
                "fake.model_name": name,
            },
        }
    )


async def handle_chat(request: web.Request) -> web.StreamResponse:
    """POST /api/chat — streaming-NDJSON or single-JSON chat completion.

    Honors the process-global ``chat_delay_seconds`` knob set via
    ``POST /_fake/control`` — sleeps that long *before* the first
    response frame. Tests use this to open a race window (delete
    mid-flight, worker crash, abort while running) without depending
    on real LLM latency.
    """
    payload = await request.json()
    model = payload.get("model", CHAT_MODEL)
    stream = payload.get("stream", True)
    delay = max(0.0, min(60.0, _control_state.get("chat_delay_seconds", 0.0)))

    content = _next_extraction_content() if _is_extraction_call(payload) else _GENERIC_CHAT_REPLY

    if delay > 0:
        await asyncio.sleep(delay)

    if not stream:
        return web.json_response(
            {
                "model": model,
                "created_at": "2026-01-01T00:00:00Z",
                "message": {"role": "assistant", "content": content},
                "done": True,
                "done_reason": "stop",
                "total_duration": 1_000_000,
                "load_duration": 0,
                "prompt_eval_count": 100,
                "prompt_eval_duration": 1_000,
                "eval_count": 200,
                "eval_duration": 1_000_000,
            }
        )

    response = web.StreamResponse(
        headers={"Content-Type": "application/x-ndjson"},
    )
    await response.prepare(request)
    # Stream the content as small chunks so the consumer's stream-
    # parser sees realistic frames rather than one big payload.
    chunk_size = 64
    for start in range(0, len(content), chunk_size):
        chunk = content[start : start + chunk_size]
        frame = {
            "model": model,
            "created_at": "2026-01-01T00:00:00Z",
            "message": {"role": "assistant", "content": chunk},
            "done": False,
        }
        await response.write((json.dumps(frame) + "\n").encode())
        await asyncio.sleep(0)  # cooperative yield, keep responses snappy
    await response.write((json.dumps(_final_done_frame(model)) + "\n").encode())
    await response.write_eof()
    return response


async def handle_generate(request: web.Request) -> web.StreamResponse:
    """POST /api/generate — single-shot generation."""
    payload = await request.json()
    model = payload.get("model", CHAT_MODEL)
    stream = payload.get("stream", False)

    content = _GENERIC_CHAT_REPLY

    if not stream:
        return web.json_response(
            {
                "model": model,
                "created_at": "2026-01-01T00:00:00Z",
                "response": content,
                "done": True,
                "done_reason": "stop",
                "context": [],
                "total_duration": 1_000_000,
                "load_duration": 0,
                "prompt_eval_count": 50,
                "prompt_eval_duration": 1_000,
                "eval_count": 100,
                "eval_duration": 1_000_000,
            }
        )

    response = web.StreamResponse(
        headers={"Content-Type": "application/x-ndjson"},
    )
    await response.prepare(request)
    frame = {
        "model": model,
        "created_at": "2026-01-01T00:00:00Z",
        "response": content,
        "done": False,
    }
    await response.write((json.dumps(frame) + "\n").encode())
    final = {
        "model": model,
        "created_at": "2026-01-01T00:00:00Z",
        "response": "",
        "done": True,
        "done_reason": "stop",
        "context": [],
        "total_duration": 1_000_000,
    }
    await response.write((json.dumps(final) + "\n").encode())
    await response.write_eof()
    return response


# Fixed embedding dimensionality. The cortex's default embedding
# model is ``Qwen/Qwen3-Embedding-0.6B`` (1024 dims); the sqlite-vec
# table is created up-front at that width and a wrong-dim embedding
# fails indexing with ``Embedding for chunk ... has wrong dimension:
# expected=1024, actual=N``. Matching the production default keeps
# us insulated from any expected_dim probe done before our first
# /api/embed response lands.
_EMBEDDING_DIMS = 1024


def _embed(text: str) -> list[float]:
    """Deterministic embedding from a SHA-256 of the input text.

    Uses the digest bytes to drive a tiny xorshift generator. Output
    floats are in [-1, 1] and L2-normalized so cosine similarity
    behaves sensibly. Not high-quality semantically — just stable and
    non-zero so similarity queries return ordered results.
    """
    seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big") or 1
    state = seed
    out: list[float] = []
    for _ in range(_EMBEDDING_DIMS):
        # xorshift64
        state ^= (state << 13) & 0xFFFFFFFFFFFFFFFF
        state ^= state >> 7
        state ^= (state << 17) & 0xFFFFFFFFFFFFFFFF
        # Map to [-1, 1].
        out.append(((state & 0xFFFFFFFF) / 0xFFFFFFFF) * 2.0 - 1.0)
    # L2 normalize.
    norm = sum(v * v for v in out) ** 0.5
    if norm == 0:
        return out
    return [v / norm for v in out]


async def handle_embed(request: web.Request) -> web.Response:
    """POST /api/embed — newer Ollama embeddings endpoint.

    Request:  {model, input: str | list[str]}
    Response: {model, embeddings: list[list[float]]}
    """
    payload = await request.json()
    model = payload.get("model", EMBEDDING_MODEL)
    input_data = payload.get("input", "")
    texts = input_data if isinstance(input_data, list) else [input_data]
    return web.json_response(
        {
            "model": model,
            "embeddings": [_embed(t) for t in texts],
            "total_duration": 1_000,
            "load_duration": 0,
            "prompt_eval_count": sum(len(t) for t in texts),
        }
    )


async def handle_embeddings_legacy(request: web.Request) -> web.Response:
    """POST /api/embeddings — legacy single-input endpoint."""
    payload = await request.json()
    model = payload.get("model", EMBEDDING_MODEL)
    prompt = payload.get("prompt", "")
    return web.json_response({"model": model, "embedding": _embed(prompt)})


async def handle_version(request: web.Request) -> web.Response:
    """GET /api/version — version probe."""
    return web.json_response({"version": "fake-ollama-1.0.0"})


async def handle_root(request: web.Request) -> web.Response:
    """GET / — health/liveness."""
    return web.Response(text="Ollama is running (fake)\n")


async def handle_control_get(request: web.Request) -> web.Response:
    """GET /_fake/control — current state of the test-control knobs."""
    return web.json_response(dict(_control_state))


async def handle_control_post(request: web.Request) -> web.Response:
    """POST /_fake/control — set test-control knobs.

    Body: ``{"chat_delay_seconds": <float>}`` (any subset of known
    keys). Used by e2e tests to open a race window for delete /
    crash / abort scenarios without depending on real LLM latency.
    """
    payload = await request.json()
    if "chat_delay_seconds" in payload:
        _control_state["chat_delay_seconds"] = max(
            0.0, min(60.0, float(payload["chat_delay_seconds"]))
        )
    return web.json_response(dict(_control_state))


async def handle_control_reset(request: web.Request) -> web.Response:
    """POST /_fake/reset — return all knobs to defaults.

    Tests should call this in teardown so the next test starts clean.
    """
    _control_state["chat_delay_seconds"] = 0.0
    _pass_counter["value"] = 0
    return web.json_response(dict(_control_state))


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/api/version", handle_version)
    app.router.add_get("/api/tags", handle_tags)
    app.router.add_post("/api/show", handle_show)
    app.router.add_post("/api/chat", handle_chat)
    app.router.add_post("/api/generate", handle_generate)
    app.router.add_post("/api/embed", handle_embed)
    app.router.add_post("/api/embeddings", handle_embeddings_legacy)
    # Test-control surface — used by e2e tests to slow the chat
    # endpoint for race-window scenarios. Not part of the Ollama API.
    app.router.add_get("/_fake/control", handle_control_get)
    app.router.add_post("/_fake/control", handle_control_post)
    app.router.add_post("/_fake/reset", handle_control_reset)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=11434, access_log=None)
