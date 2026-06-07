# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Edition Models.

Pydantic DTOs for the edition/licensing endpoint.
"""

from pydantic import BaseModel


class LicenseInfo(BaseModel):
    """Enterprise license details."""

    type: str
    holder: str
    expires: str | None = None


class EditionResponse(BaseModel):
    """Edition and feature availability response."""

    edition: str
    license: LicenseInfo | None = None
    features: list[str]


# Features available in the community edition.
# This is the canonical list — enterprise extensions append to it.
COMMUNITY_FEATURES: list[str] = [
    "auth",
    "backup",
    "chats",
    "counts",
    "databases",
    "diagnostics",
    "edges",
    "export",
    "graph",
    "health",
    "lexicon",
    "llm",
    "logs",
    "mcp",
    "nodes",
    "quality",
    "queue",
    "search",
    "settings",
    "sources",
    "templates",
    "tools",
    "triggers",
    "workflows",
]
