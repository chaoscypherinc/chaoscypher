# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Benchmark model config: ModelConfig dataclass, cost computation, and filtering."""

from __future__ import annotations

from dataclasses import dataclass


# Local providers cost zero. Anything not listed here is "commercial" and
# requires a price registry entry.
_LOCAL_PROVIDERS: frozenset[str] = frozenset({"ollama"})


# Prices in USD per 1,000,000 tokens, dated 2026-04-28.
# Update with provenance comments when prices change. Missing entries cause
# compute_cost() to return None - surfaced as a leaderboard warning.
_PRICE_REGISTRY: dict[tuple[str, str], dict[str, float]] = {
    ("openai", "gpt-4o"): {"input": 2.50, "output": 10.00},
    ("openai", "gpt-4o-mini"): {"input": 0.15, "output": 0.60},
    ("anthropic", "claude-sonnet-4-6"): {"input": 3.00, "output": 15.00},
    ("anthropic", "claude-opus-4-7"): {"input": 15.00, "output": 75.00},
    ("anthropic", "claude-haiku-4-5-20251001"): {"input": 0.80, "output": 4.00},
}


@dataclass(frozen=True)
class ModelConfig:
    """A single benchmark model entry (provider + model + label).

    Attributes:
        provider: e.g. "ollama", "openai", "anthropic". Must match the
            provider name expected by `chaoscypher_core` LLMProvider.
        model: Provider-specific model identifier (e.g. "llama3.1:8b",
            "gpt-4o", "claude-sonnet-4-6").
        label: Human-readable name for leaderboard display.
        kinds: Optional list restricting the model to specific pack kinds.
            None means the model runs on all kinds.
    """

    provider: str
    model: str
    label: str
    kinds: list[str] | None = None

    @property
    def model_id(self) -> str:
        """Stable identifier of form ``<provider>/<model>`` for results files."""
        return f"{self.provider}/{self.model}"


def filter_models(
    models: list[ModelConfig],
    *,
    local_only: bool = False,
    kind: str | None = None,
) -> list[ModelConfig]:
    """Filter a model list by provider class and pack kind.

    Args:
        models: Input model list.
        local_only: If True, drop commercial-provider entries.
        kind: If set, drop models whose ``kinds`` field excludes this kind.
            Models with ``kinds=None`` are unscoped and always kept.
    """
    out = list(models)
    if local_only:
        out = [m for m in out if m.provider in _LOCAL_PROVIDERS]
    if kind is not None:
        out = [m for m in out if m.kinds is None or kind in m.kinds]
    return out


def compute_cost(model: ModelConfig, *, input_tokens: int, output_tokens: int) -> float | None:
    """Return USD cost for a run, or None if the model has no price entry.

    Local providers always cost zero. Commercial models look up
    ``(provider, model)`` in the price registry; missing entries return None.
    """
    if model.provider in _LOCAL_PROVIDERS:
        return 0.0
    prices = _PRICE_REGISTRY.get((model.provider, model.model))
    if prices is None:
        return None
    input_usd = (input_tokens / 1_000_000.0) * prices["input"]
    output_usd = (output_tokens / 1_000_000.0) * prices["output"]
    return input_usd + output_usd


__all__ = [
    "ModelConfig",
    "compute_cost",
    "filter_models",
]
