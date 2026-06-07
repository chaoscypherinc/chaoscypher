# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Async cloud-LLM connectivity probes.

Lifted from `chaoscypher_cli.commands.setup` (urllib-based, sync) into
core async helpers so the wizard's verify endpoint and the CLI can
share validation logic. The cortex-side endpoint
(/settings/llm/verify) wraps these for the settings page's "Test
Connection" button. The verify gate itself is real-time as of
2026-05-22 — see :func:`chaoscypher_core.services.llm.health.get_llm_health`.

Returns ``(success: bool, message: str)`` per call so callers can
surface a user-actionable error without needing to interpret
provider-specific HTTP responses.
"""

from __future__ import annotations

from typing import Final

import httpx
import structlog


logger = structlog.get_logger(__name__)


# Timeouts are intentionally tight — a verify call should fail fast
# rather than dragging the user through a 30s wait for an obviously
# wrong key. Network blips can fail false-negative; the user can
# click Test again.
_VERIFY_TIMEOUT_SECONDS: Final[float] = 10.0


async def verify_openai_key(api_key: str) -> tuple[bool, str]:
    """Probe ``api.openai.com/v1/models`` with the provided API key."""
    try:
        async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT_SECONDS) as client:
            response = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.TimeoutException:
        return False, "Connection timed out"
    except httpx.HTTPError as exc:
        return False, f"Network error: {exc}"

    if response.status_code == 200:
        return True, "API key valid"
    if response.status_code == 401:
        return False, "Invalid API key"
    return False, f"Unexpected status: {response.status_code}"


async def verify_anthropic_key(api_key: str) -> tuple[bool, str]:
    """Probe ``api.anthropic.com`` with a minimal messages call."""
    payload = {
        "model": "claude-3-haiku-20240307",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "Hi"}],
    }
    try:
        async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT_SECONDS) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
    except httpx.TimeoutException:
        return False, "Connection timed out"
    except httpx.HTTPError as exc:
        return False, f"Network error: {exc}"

    if response.status_code == 200:
        return True, "API key valid"
    if response.status_code == 401:
        return False, "Invalid API key"
    return False, f"Unexpected status: {response.status_code}"


async def verify_gemini_key(api_key: str) -> tuple[bool, str]:
    """Probe ``generativelanguage.googleapis.com`` with the provided key."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
    except httpx.TimeoutException:
        return False, "Connection timed out"
    except httpx.HTTPError as exc:
        return False, f"Network error: {exc}"

    if response.status_code == 200:
        return True, "API key valid"
    if response.status_code in (400, 401, 403):
        return False, "Invalid API key"
    return False, f"Unexpected status: {response.status_code}"


CLOUD_PROVIDERS: Final[frozenset[str]] = frozenset({"openai", "anthropic", "gemini"})


async def verify_cloud_key(provider: str, api_key: str) -> tuple[bool, str]:
    """Dispatch to the per-provider verify function for cloud providers.

    Args:
        provider: ``"openai"`` | ``"anthropic"`` | ``"gemini"``.
        api_key: Cloud API key to probe with.

    Returns:
        ``(success, message)`` from the underlying provider call.

    Raises:
        ValidationError: If ``provider`` is not a recognised cloud provider.
    """
    from chaoscypher_core.exceptions import ValidationError

    if provider == "openai":
        return await verify_openai_key(api_key)
    if provider == "anthropic":
        return await verify_anthropic_key(api_key)
    if provider == "gemini":
        return await verify_gemini_key(api_key)
    msg = f"Unknown cloud provider: {provider!r} (expected one of {sorted(CLOUD_PROVIDERS)})"
    raise ValidationError(msg, field="provider")


__all__ = [
    "CLOUD_PROVIDERS",
    "verify_anthropic_key",
    "verify_cloud_key",
    "verify_gemini_key",
    "verify_openai_key",
]
