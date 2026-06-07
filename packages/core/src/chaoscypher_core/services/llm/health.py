# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM health snapshot — shared by the settings health endpoint and action gates.

Both ``GET /settings/llm/health`` and the action gates (import + chat
send) compute the same predicate: "is the currently-selected LLM chat
provider configured and verified, AND are the configured models actually
pulled, so we should let a user-initiated action through?" Centralizing
the rule here keeps the gate and the banner state in lockstep — the
frontend can't show a green banner while the import endpoint 409s.

Verification is real-time, not gesture-based (2026-05-22 reshape):

- **Ollama:** ``verified`` reflects whether ``/api/tags`` is reachable on
  any configured instance at the moment of the health-check call. Users
  do not click "Verify" — bringing Ollama up automatically clears the
  banner on the next refetch.

- **Cloud (OpenAI / Anthropic / Gemini):** ``verified`` reflects an
  inexpensive format check on the configured API key (presence + prefix
  + length). No network call. The first real LLM call surfaces a key
  error if the key is wrong.

The legacy ``LLMVerifyStateTracker`` is still updated by the manual
"Test Connection" endpoints for audit purposes but no longer drives
this predicate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import structlog


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings


logger = structlog.get_logger(__name__)

# Sentinel returned by _ollama_pulled_models when /api/tags is unreachable.
# Distinguished from an empty set so callers can tell "we know there are
# zero models" (set()) from "we don't know what's pulled" (None).
_OllamaTagsUnreachable = None


@dataclass(frozen=True, slots=True)
class LLMHealth:
    """Snapshot of LLM chat provider configuration + verification state."""

    provider: str
    configured: bool
    verified: bool
    last_verified_at_iso: str | None
    missing_models: tuple[str, ...] = field(default_factory=tuple)


def _provider_is_configured(settings: Settings) -> bool:
    """Return True if the selected provider has its minimum config fields set."""
    provider = settings.llm.chat_provider
    if provider == "openai":
        return settings.llm.openai_api_key is not None
    if provider == "anthropic":
        return settings.llm.anthropic_api_key is not None
    if provider == "gemini":
        return settings.llm.gemini_api_key is not None
    if provider == "ollama":
        return bool(settings.llm.ollama_instances)
    return False


def _configured_ollama_models(settings: Settings) -> list[str]:
    """Return distinct configured Ollama model names (chat / extraction / vision)."""
    llm = settings.llm
    names = [
        llm.ollama_chat_model,
        llm.ollama_extraction_model,
        llm.ollama_vision_model,
    ]
    # Drop None and empty strings; preserve order for stable UI output.
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


async def _ollama_pulled_models(settings: Settings) -> set[str] | None:
    """Return the set of model names pulled across configured Ollama instances.

    Returns ``None`` when no instance was reachable — distinguished from
    an empty set so callers can tell "we know there are zero models" from
    "we don't know what's pulled". On the unreachable branch, callers
    should NOT add to ``missing_models`` (the upstream connection error
    is the real signal).

    Async because the surrounding endpoints are async; sync httpx would
    block the event loop for up to ``ollama_verify_timeout`` seconds.
    """
    instances = settings.llm.ollama_instances or []
    # Reuse the existing verify timeout — list_models is the same
    # /api/tags call verify_ollama_url makes, so the timeout budget is
    # the same. Avoids inventing a new settings field.
    timeout = settings.timeouts.ollama_verify_timeout
    pulled: set[str] = set()
    any_reachable = False

    async with httpx.AsyncClient(timeout=timeout) as client:
        for inst in instances:
            if not inst.enabled:
                continue
            base_url = inst.base_url
            if not base_url:
                continue
            try:
                resp = await client.get(f"{base_url}/api/tags")
                if resp.status_code != 200:
                    continue
                any_reachable = True
                for m in resp.json().get("models", []):
                    name = m.get("name")
                    if name:
                        pulled.add(name)
            except httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError:
                continue
            except Exception:
                logger.warning(
                    "ollama_health_list_models_unexpected",
                    base_url=base_url,
                    exc_info=True,
                )
                continue

    return pulled if any_reachable else _OllamaTagsUnreachable


def _missing_models_from_pulled(pulled: set[str] | None, settings: Settings) -> tuple[str, ...]:
    """Return configured Ollama models NOT present on any reachable instance.

    Returns () when Ollama is unreachable (``pulled is None``) — the
    connection error surfaces via ``verified=False`` instead.
    """
    if pulled is None:
        return ()
    configured = _configured_ollama_models(settings)
    if not configured:
        return ()
    return tuple(name for name in configured if name not in pulled)


# Minimum lengths for cloud-provider API-key format checks. Real keys are
# longer than these floors; the floors exist to reject obvious placeholders
# like "x" or "fake-key" without claiming we know the key works.
_OPENAI_KEY_PREFIX = "sk-"
_OPENAI_KEY_MIN_LEN = 20
_ANTHROPIC_KEY_PREFIX = "sk-ant-"
_ANTHROPIC_KEY_MIN_LEN = 20
_GEMINI_KEY_MIN_LEN = 30


def _cloud_key_format_valid(provider: str, settings: Settings) -> bool:
    """Return True if the cloud provider's API key is set and format-valid.

    Inexpensive format check — no network call. The first real LLM call
    surfaces a key error if the key is wrong. Matches the operator
    mental model "if I filled it in, the banner should clear; if it's
    actually bad, the first import will tell me."
    """
    llm = settings.llm
    if provider == "openai":
        key, prefix, min_len, strict_gt = (
            llm.openai_api_key,
            _OPENAI_KEY_PREFIX,
            _OPENAI_KEY_MIN_LEN,
            True,
        )
    elif provider == "anthropic":
        key, prefix, min_len, strict_gt = (
            llm.anthropic_api_key,
            _ANTHROPIC_KEY_PREFIX,
            _ANTHROPIC_KEY_MIN_LEN,
            True,
        )
    elif provider == "gemini":
        key, prefix, min_len, strict_gt = (
            llm.gemini_api_key,
            "",
            _GEMINI_KEY_MIN_LEN,
            False,
        )
    else:
        return False
    if key is None:
        return False
    raw = key.get_secret_value()
    if prefix and not raw.startswith(prefix):
        return False
    return len(raw) > min_len if strict_gt else len(raw) >= min_len


async def get_llm_health(settings: Settings) -> LLMHealth:
    """Compute the health snapshot for the currently-selected provider.

    Real-time verification (2026-05-22 reshape):
    - Ollama: ``verified`` reflects current ``/api/tags`` reachability;
      ``missing_models`` lists configured models not pulled on any
      reachable instance.
    - Cloud: ``verified`` reflects an API-key format check; no network
      call.

    Async because the Ollama path requires an ``/api/tags`` HTTP
    roundtrip. Cloud providers resolve immediately.
    """
    provider = settings.llm.chat_provider
    configured = _provider_is_configured(settings)

    if provider == "ollama":
        pulled = await _ollama_pulled_models(settings)
        verified = pulled is not None
        missing = _missing_models_from_pulled(pulled, settings)
    else:
        verified = _cloud_key_format_valid(provider, settings)
        missing = ()

    last_verified_iso = datetime.now(UTC).isoformat() if verified else None
    return LLMHealth(
        provider=provider,
        configured=configured,
        verified=verified,
        last_verified_at_iso=last_verified_iso,
        missing_models=missing,
    )


async def require_llm_verified(settings: Settings) -> None:
    """Raise :class:`LLMNotVerifiedError` when the selected provider is unverified."""
    from chaoscypher_core.exceptions import LLMNotVerifiedError

    health = await get_llm_health(settings)
    if not health.verified:
        raise LLMNotVerifiedError(provider=health.provider)


async def require_extraction_ready(settings: Settings) -> None:
    """Raise the appropriate error when the LLM cannot serve an extraction.

    Two-step gate: provider must be verified AND configured models must
    be pulled. Order matters — an unverified provider is a different
    operator problem (no Ollama URL or no API key) than a missing model
    (Ollama up, model not pulled), and the two messages map to different
    Settings panes.
    """
    from chaoscypher_core.exceptions import (
        ExtractionModelMissingError,
        LLMNotVerifiedError,
    )

    health = await get_llm_health(settings)
    if not health.verified:
        raise LLMNotVerifiedError(provider=health.provider)
    if health.missing_models:
        raise ExtractionModelMissingError(
            provider=health.provider,
            missing_models=health.missing_models,
        )


__all__ = [
    "LLMHealth",
    "get_llm_health",
    "require_extraction_ready",
    "require_llm_verified",
]
