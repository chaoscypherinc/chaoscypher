# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Which completions may be cached: pinned decoding only.

A completion is a function of its input only when it is pinned: temperature
0 and, on a provider that honours seeds, a seed. The policy judges the
EFFECTIVE decoding (the wrappers build :class:`~chaoscypher_core.cache.keys.Decoding`
from ``chaoscypher_core.utils.decoding``'s ``effective_*``), so the retry
ladder's pinned rungs cache under their own key and a sampled regenerate
bypasses. Embeddings are deterministic and never consult the policy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from chaoscypher_core.utils.decoding import seed_honoured


if TYPE_CHECKING:
    from chaoscypher_core.cache.keys import Decoding


BypassReason = Literal["sampled", "unseeded"]
"""Why a call was kept away from the store: sampled decoding, or no seed where seeds count."""


def cacheable(decoding: Decoding, provider: str) -> tuple[bool, BypassReason | None]:
    """``(True, None)`` when pinned, else ``(False, reason)``.

    Pinned is temperature 0 and either a seed or a provider that ignores
    seeds (a hosted model at temperature 0 is cached, its hits flagged by
    :func:`seeded`). Which providers honour a seed is
    :data:`~chaoscypher_core.utils.decoding.SEED_HONOURING_PROVIDERS`.
    """
    if decoding.temperature != 0:
        return False, "sampled"
    if decoding.seed is None and seed_honoured(provider):
        return False, "unseeded"
    return True, None


def seeded(decoding: Decoding, provider: str) -> bool:
    """Whether a hit is the deterministic answer (the seed is honoured) or the first answer seen."""
    return seed_honoured(provider) and decoding.seed is not None


__all__ = ["BypassReason", "cacheable", "seeded"]
