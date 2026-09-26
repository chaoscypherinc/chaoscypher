# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ChatProbeScorer re-export: the grounded-chat probe checks live in Core.

Kept so existing ``chaoscypher_cli.benchmark.scorers.chat_probes`` imports keep
working; new code should import from ``chaoscypher_core.benchmark.scorers.chat_probes``.
"""

from __future__ import annotations

from chaoscypher_core.benchmark.scorers.chat_probes import (
    _MINOR,
    _NOT_A_NAME,
    BAND_TIER,
    CHAT_PROBE_SCORER_VERSION,
    DECLINE_PHRASES,
    ChatProbeScorer,
    _candidate_names,
    _first_position,
    _initialism,
    _mentions,
    _significant_words,
    answers_from_graph,
    declines_when_unsupported,
    finish_stop,
    leads_with_gold,
    must_not_contain,
    no_unsupported_names,
    no_unsupported_numbers,
)


__all__ = [
    "BAND_TIER",
    "CHAT_PROBE_SCORER_VERSION",
    "DECLINE_PHRASES",
    "_MINOR",
    "_NOT_A_NAME",
    "ChatProbeScorer",
    "_candidate_names",
    "_first_position",
    "_initialism",
    "_mentions",
    "_significant_words",
    "answers_from_graph",
    "declines_when_unsupported",
    "finish_stop",
    "leads_with_gold",
    "must_not_contain",
    "no_unsupported_names",
    "no_unsupported_numbers",
]
