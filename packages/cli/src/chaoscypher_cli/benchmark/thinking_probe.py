# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Probe whether an Ollama model honours the ``think`` parameter.

Models accept ``think`` and may silently ignore it — measured 2026-09-22,
``qwen3:30b-instruct`` returns no reasoning for ``think=true`` and no error, and
``gemma4:31b`` returns a byte-identical response for ``think="low"`` and
``think="high"``. A benchmark that assumes the request was honoured will
mislabel its own rows, so verify instead of assuming.
"""

from __future__ import annotations

import httpx
import structlog


logger = structlog.get_logger(__name__)

_PROMPT = "What is 17*23? Answer with just the number."
_TIMEOUT = 120.0


async def probe_thinking(
    model: str,
    *,
    base_url: str = "http://localhost:11434",
    timeout: float = _TIMEOUT,
) -> dict[str, bool | None]:
    """Return what a model actually does with the ``think`` parameter.

    Args:
        model: Ollama model tag, e.g. ``"qwen3.8:27b"``.
        base_url: Ollama server URL.
        timeout: Per-request timeout in seconds.

    Returns:
        ``thinks_when_asked``: reasoning came back for ``think=true``.
        ``obeys_think_false``: no reasoning came back for ``think=false``.
        Either is ``None`` when the probe could not reach the model, so an
        unreachable server is never reported as a capability answer.
    """
    out: dict[str, bool | None] = {"thinks_when_asked": None, "obeys_think_false": None}
    async with httpx.AsyncClient(timeout=timeout) as client:
        for think, key in ((True, "thinks_when_asked"), (False, "obeys_think_false")):
            try:
                resp = await client.post(
                    f"{base_url}/api/chat",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": _PROMPT}],
                        "think": think,
                        "stream": False,
                        "options": {"seed": 42, "temperature": 0},
                    },
                )
                resp.raise_for_status()
                thinking = (resp.json().get("message") or {}).get("thinking") or ""
            except Exception as exc:  # a probe never fails the run it informs
                logger.warning("thinking_probe_failed", model=model, think=think, error=str(exc))
                continue
            # think=true  -> honoured when reasoning is present.
            # think=false -> honoured when reasoning is absent.
            out[key] = bool(thinking) if think else not thinking
    return out


def thinking_honoured(probe: dict[str, bool | None], *, requested: bool) -> bool | None:
    """Did the model do what the benchmark asked for this run?

    Returns None when the probe could not answer, so "unknown" is never
    silently reported as "honoured".
    """
    key = "thinks_when_asked" if requested else "obeys_think_false"
    return probe.get(key)


__all__ = ["probe_thinking", "thinking_honoured"]
