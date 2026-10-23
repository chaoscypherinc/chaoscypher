# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cache keys: exact, over what the model actually sees.

A completion key is the sha256 of canonical JSON over the model build, the
normalised messages, the effective decoding and the tool definitions; an
embedding key over the model build and the NFC text. Normalising drops
everything that is not model input (message ids, timestamps, request ids,
per-call metadata) and collapses whitespace runs in message content, so a
history replayed with fresh ids hits; anything the model does see (roles,
order, tool calls, every decoding option) is in the key, so a different
question never gets another question's answer.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from chaoscypher_core.utils.query_text import normalize_query


VOLATILE_MESSAGE_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "message_id",
        "timestamp",
        "created_at",
        "request_id",
        "correlation_id",
        "metadata",
    }
)
"""Message fields that are never model input (dropped, with any other field not kept)."""

KEPT_MESSAGE_FIELDS: tuple[str, ...] = ("role", "content", "name", "tool_calls", "tool_call_id")
"""The message fields a provider sends to the model: the only ones in a key."""


@dataclass(frozen=True)
class Decoding:
    """The decoding a call is sent with (the effective values, after any override)."""

    seed: int | None
    """The seed sent, or None when none is."""
    temperature: float
    """The sampling temperature sent."""
    num_ctx: int | None
    """The context window requested (Ollama ``num_ctx``), when the provider takes one."""
    max_tokens: int | None
    """The output cap, when one is set."""
    thinking: bool
    """Whether thinking was enabled for the call."""
    repeat_penalty: float | None
    """The repeat penalty sent, when one is."""


def _nfc(value: Any) -> Any:
    """Every string inside ``value`` in NFC (structure and whitespace kept)."""
    if isinstance(value, str):
        return normalize_query(value)
    if isinstance(value, list):
        return [_nfc(v) for v in value]
    if isinstance(value, dict):
        return {k: _nfc(v) for k, v in value.items()}
    return value


def _content(value: Any) -> Any:
    """Message content as keyed: text NFC with whitespace runs collapsed; parts NFC only."""
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(normalize_query(value).split())
    return _nfc(value)


def normalise_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Role + content (NFC, whitespace runs to one space) + name/tool_calls/tool_call_id when present.

    Everything else, :data:`VOLATILE_MESSAGE_FIELDS` included, is dropped.
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        kept: dict[str, Any] = {
            "role": message.get("role"),
            "content": _content(message.get("content")),
        }
        for name in KEPT_MESSAGE_FIELDS[2:]:
            if message.get(name) is not None:
                kept[name] = _nfc(message[name])
        out.append(kept)
    return out


def _digest(obj: Any) -> str:
    """sha256 hex of ``obj`` as canonical JSON."""
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def completion_key(
    model_build: str,
    messages: list[dict[str, Any]],
    decoding: Decoding,
    tools: list[dict[str, Any]] | None,
) -> str:
    """sha256 of canonical JSON ``{model_build, messages (normalised), decoding, tools}``."""
    return _digest(
        {
            "kind": "completion",
            "model_build": model_build,
            "messages": normalise_messages(messages),
            "decoding": dataclasses.asdict(decoding),
            "tools": _nfc(tools) if tools else None,
        }
    )


def embedding_key(
    model_build: str, text: str, *, dimensions: int | None = None, query_prefix: str = ""
) -> str:
    """sha256 of canonical JSON ``{model_build, text (NFC), dimensions, query_prefix}``.

    Whitespace is significant (the embedder sees it). ``dimensions`` (the
    provider's output size after its MRL cut) and ``query_prefix`` decide
    the vector besides the text, so two providers of one model that differ
    in either never share an entry.
    """
    return _digest(
        {
            "kind": "embedding",
            "model_build": model_build,
            "text": normalize_query(text),
            "dimensions": dimensions,
            "query_prefix": query_prefix,
        }
    )


def model_build_id(
    provider: str, model: str, *, digest: str | None, server_version: str | None
) -> str:
    """The model build a key names.

    ``provider:model:digest@server`` when the weights' digest is known (an
    ``ollama pull`` changes it, so a stale answer is never served as fresh),
    else ``provider:model`` (a hosted model id, which carries its own date),
    with ``@server`` whenever a server or API version is known.
    """
    build = f"{provider}:{model}:{digest}" if digest else f"{provider}:{model}"
    return f"{build}@{server_version}" if server_version else build


def build_names_model(build: str, provider: str, model: str) -> bool:
    """Whether ``build`` (a :func:`model_build_id`) is a build of ``provider``/``model``.

    The inverse of :func:`model_build_id`: ``provider:model``, optionally
    ``:digest`` (a server's digest, which has no colon) and ``@server``. So
    ``ollama:gemma4`` does not claim a build of ``ollama:gemma4:31b``.
    """
    head = build.split("@", 1)[0]
    prefix = f"{provider}:{model}"
    if head == prefix:
        return True
    return head.startswith(f"{prefix}:") and ":" not in head[len(prefix) + 1 :]


__all__ = [
    "KEPT_MESSAGE_FIELDS",
    "VOLATILE_MESSAGE_FIELDS",
    "Decoding",
    "build_names_model",
    "completion_key",
    "embedding_key",
    "model_build_id",
    "normalise_messages",
]
