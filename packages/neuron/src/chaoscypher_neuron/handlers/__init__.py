# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Queue handler registration modules for the Neuron worker.

Provides individual handler registration functions for specialized
queue tasks that are not part of a larger operations service.
"""

import re

from .chat_completion import register_chat_completion_handler
from .quality_scores import register_quality_score_handler
from .template_embedding import register_template_embedding_handler


_SAFE_DB_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_database_name(name: str | None, fallback: str) -> str:
    """Validate a database name from a queue payload.

    Args:
        name: The database name from the queue task data.
        fallback: Default database name to use if validation fails.

    Returns:
        The validated database name, or the fallback if invalid.
    """
    if name and _SAFE_DB_NAME.match(name):
        return name
    return fallback


__all__ = [
    "register_chat_completion_handler",
    "register_quality_score_handler",
    "register_template_embedding_handler",
    "validate_database_name",
]
