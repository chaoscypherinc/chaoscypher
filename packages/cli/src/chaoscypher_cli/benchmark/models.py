# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Benchmark model config: ModelConfig dataclass, cost computation, and filtering."""

from __future__ import annotations

from dataclasses import dataclass

from chaoscypher_cli.benchmark.models_registry import load_registry


# Local providers cost zero. Anything not listed here is "commercial" and
# requires a price registry entry.
_LOCAL_PROVIDERS: frozenset[str] = frozenset({"ollama"})


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
    """Return USD cost for a run, or None if the model has no registry price.

    Local providers always cost zero. Commercial models look up
    ``<provider>/<model>`` in the registry; a missing price returns None.
    """
    if model.provider in _LOCAL_PROVIDERS:
        return 0.0
    entry = load_registry().get(model.model_id)
    if entry is None or entry.price is None:
        return None
    input_usd = (input_tokens / 1_000_000.0) * entry.price["input"]
    output_usd = (output_tokens / 1_000_000.0) * entry.price["output"]
    return input_usd + output_usd


def assert_registry_coverage(models: list[ModelConfig]) -> list[str]:
    """Return ``<provider>/<model>`` ids of commercial models missing a registry price entry.

    Used by the CLI to fail fast before a run rather than silently producing
    a $0.00 cost for an unpriced commercial model.
    """
    reg = load_registry()
    missing: list[str] = []
    for m in models:
        if m.provider in _LOCAL_PROVIDERS:
            continue
        entry = reg.get(m.model_id)
        if entry is None or entry.price is None:
            missing.append(m.model_id)
    return missing


__all__ = [
    "ModelConfig",
    "assert_registry_coverage",
    "compute_cost",
    "filter_models",
]
