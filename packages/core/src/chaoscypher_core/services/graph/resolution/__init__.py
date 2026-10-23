# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Identity resolution over a committed graph: one person, one node; edges that say what the text says.

Extraction-time deduplication compares entity texts by embedding and merges
the clear cases; what it leaves are the splits an embedding cannot see (the
2026-10-04 graph scan of Book One: "Anna Pávlovna Schérer" and "Mlle
Schérer", "Count Bezúkhov" and "Cyril Vladímirovich Bezúkhov", two
Drubetskáyas, two vicomtes), aliases landing on the wrong node (the
hostess carrying "Anna Mikháylovna"), and kinship edges the LLM inferred
rather than read. This pass plans merges, alias moves and edge flags from
the names, the aliases, the edges and the justifications the edges carry,
applies the strong ones, and reports the rest for a reader to judge.
"""

from chaoscypher_core.services.graph.resolution.apply import (
    ResolutionReport,
    apply_resolution,
    resolve_graph,
)
from chaoscypher_core.services.graph.resolution.plan import (
    AliasDrop,
    EdgeFlag,
    Merge,
    ResolutionPlan,
    plan_resolution,
)
from chaoscypher_core.services.graph.resolution.recipe import resolution_recipe


__all__ = [
    "AliasDrop",
    "EdgeFlag",
    "Merge",
    "ResolutionPlan",
    "ResolutionReport",
    "apply_resolution",
    "plan_resolution",
    "resolution_recipe",
    "resolve_graph",
]
