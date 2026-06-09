# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Render the public model-cards documentation page from the benchmark registry.

This module is the importable core used by ``scripts/generate_model_cards.py``.
Run via: ``uv run python scripts/generate_model_cards.py``
Writes:  ``packages/docs/docs/reference/model-cards.md``
"""

from __future__ import annotations

from chaoscypher_cli.benchmark.models_registry import load_registry


def _md(s: str) -> str:
    """Escape a string for safe inclusion in a Markdown table cell."""
    return (s or "").replace("|", "&#124;")


def render_cards() -> str:
    """Render the registry as a Markdown catalog (deterministic order)."""
    reg = load_registry()
    rows = sorted(reg.values(), key=lambda e: (not e.open_weight, e.provider, e.model))
    lines = [
        "---",
        "id: model-cards",
        "title: Benchmark Model Cards",
        "description: Metadata and pricing for every model in the ChaosCypher benchmark suite.",
        "---",
        "",
        "# Benchmark Model Cards",
        "",
        "> Generated from `models_registry.yaml`. Do not edit by hand —",
        "> run `uv run python scripts/generate_model_cards.py`.",
        "",
        "| Model | Provider | Open weight | Context | Price in/out ($/1M) | Why included |",
        "|---|---|---|---|---|---|",
    ]
    for e in rows:
        price = (
            f"${e.price['input']:.2f} / ${e.price['output']:.2f}"
            if e.price is not None
            else "free (local)"
        )
        ctx = f"{e.context:,}" if e.context else "-"
        lines.append(
            f"| {_md(e.label)} | {_md(e.provider)} | {'yes' if e.open_weight else 'no'} | "
            f"{ctx} | {price} | {_md(e.why or '')} |"
        )
    lines.append("")
    return "\n".join(lines)


__all__ = ["render_cards"]
