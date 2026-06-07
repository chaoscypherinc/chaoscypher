# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Text Extraction Helper.

Extracts searchable text from graph nodes.
"""

import json
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from chaoscypher_core.models import Node


def extract_searchable_text(node: Node) -> str:
    """Extract all searchable text from a node.

    Args:
        node: Node to extract text from

    Returns:
        Combined searchable text string

    """
    text_parts = [node.label]

    # Extract text from properties
    for value in node.properties.values():
        if isinstance(value, str):
            text_parts.append(value)
        elif isinstance(value, (int, float, bool)):
            text_parts.append(str(value))
        elif isinstance(value, (list, dict)):
            text_parts.append(json.dumps(value))

    return " ".join(text_parts)
