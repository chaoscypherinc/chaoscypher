# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pin the decoding the benchmark records onto the settings a stage actually runs with.

Every result row records ``seed`` and ``temperature``. Until 2026-10-06 the
chat stage never received them: it built a bare ``LLMSettings`` from the
provider, model and credentials, so every chat row ran at the product's
``ai_temperature`` (0.3) with no seed while claiming seed 42 / temperature
0 — the source of the five-to-six-question spread between "identical"
repeat passes. Extraction had the same bug earlier and pinned by hand. Both
stages now pin through :func:`pin_llm_determinism`, which assigns by real
field name and fails loudly when the settings schema moves.
"""

from __future__ import annotations

from typing import Any


PINNED_FIELDS = ("seed", "ai_temperature", "extraction_temperature", "thinking_for_extraction")
"""The ``LLMSettings`` fields the benchmark pins; a missing one is a schema change, not a default."""


def pin_llm_determinism(
    llm: Any, *, seed: int, temperature: float, thinking: bool | None = None
) -> None:
    """Set the seed, both temperatures and (when given) extraction thinking on ``llm`` in place.

    Raises:
        AttributeError: When ``llm`` lacks one of :data:`PINNED_FIELDS`; the row
            would otherwise record a configuration the run did not use.
    """
    values: dict[str, object] = {
        "seed": seed,
        "ai_temperature": temperature,
        "extraction_temperature": temperature,
    }
    if thinking is not None:
        values["thinking_for_extraction"] = thinking
    missing = [f for f in PINNED_FIELDS if not hasattr(llm, f)]
    if missing:
        msg = (
            f"LLMSettings has no field(s) {', '.join(repr(f) for f in missing)}; the benchmark "
            "cannot pin its run configuration and its results would be unreproducible."
        )
        raise AttributeError(msg)
    for field_name, value in values.items():
        setattr(llm, field_name, value)


__all__ = ["PINNED_FIELDS", "pin_llm_determinism"]
