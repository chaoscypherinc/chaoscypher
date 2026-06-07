# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Uniform ID Generation.

Generates standard 36-character UUID v4 IDs for all resources.
Optional prefix support for RDF entities and internal types.
"""

import uuid


def generate_id(prefix: str | None = None) -> str:
    """Generate a unique ID using UUID v4.

    Args:
        prefix: Optional prefix to prepend (e.g., "node", "edge", "chunk", "emb")
                If provided, format will be: {prefix}_{uuid}
                If None, returns plain UUID

    Returns:
        str: Unique identifier

    Examples:
        >>> generate_id()  # Plain UUID
        '550e8400-e29b-41d4-a716-446655440000'

        >>> generate_id("node")  # RDF node
        'node_550e8400-e29b-41d4-a716-446655440000'

        >>> generate_id("chunk")  # Document chunk
        'chunk_550e8400-e29b-41d4-a716-446655440000'

    """
    uid = str(uuid.uuid4())
    return f"{prefix}_{uid}" if prefix else uid
