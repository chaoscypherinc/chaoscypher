# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Template-related utilities shared by services and adapters.

Modules in this package must NOT import from `chaoscypher_core.services.*`
or `chaoscypher_core.adapters.*`. This is a neutral location so both sides
can depend on it without breaking hexagonal architecture direction.
"""

from chaoscypher_core.templates.visuals import (
    resolve_edge_visuals,
    resolve_node_visuals,
)


__all__ = [
    "resolve_edge_visuals",
    "resolve_node_visuals",
]
