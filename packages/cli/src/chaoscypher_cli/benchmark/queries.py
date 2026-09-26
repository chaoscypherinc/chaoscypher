# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LabeledQuery re-export: the queries.yaml models and parser live in Core.

Kept so existing ``chaoscypher_cli.benchmark.queries`` imports keep
working; new code should import from ``chaoscypher_core.benchmark.queries``.
"""

from __future__ import annotations

from chaoscypher_core.benchmark.queries import (
    BAND_VALUES,
    Band,
    LabeledQuery,
    LabeledQuerySet,
    load_queries,
)


__all__ = [
    "BAND_VALUES",
    "Band",
    "LabeledQuery",
    "LabeledQuerySet",
    "load_queries",
]
