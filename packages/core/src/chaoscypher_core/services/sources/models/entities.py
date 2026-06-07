# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Extraction Result Models.

Domain models for source processing pipeline results.

Provides canonical format for extraction workflow outputs including
entities, relationships, templates, and metadata.
"""

from typing import Any

from pydantic import BaseModel, Field


class Entity(BaseModel):
    """Represents an extracted entity."""

    name: str
    type: str
    description: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    id: str | None = None  # For graph-based workflows

    def to_dict(self) -> dict:
        """Convert to dictionary, excluding None values."""
        return self.model_dump(exclude_none=True)


class Relationship(BaseModel):
    """Represents a relationship between entities."""

    from_entity: str = Field(serialization_alias="from")  # Entity name or ID
    to_entity: str = Field(serialization_alias="to")  # Entity name or ID
    type: str
    confidence: float = 0.5
    justification: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)

    # Support both name-based and index-based relationships
    source: int | None = None  # Source entity index
    target: int | None = None  # Target entity index

    def to_dict(self) -> dict:
        """Convert to dictionary, excluding None values.

        Serializes ``from_entity`` as ``"from"`` and ``to_entity`` as
        ``"to"`` for downstream compatibility.
        """
        return self.model_dump(exclude_none=True, by_alias=True)


class SuggestedTemplate(BaseModel):
    """Represents a suggested template from extraction results."""

    name: str
    description: str
    properties: list[str] = Field(default_factory=list)
    confidence: float = 1.0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return self.model_dump()


class EdgeTemplate(BaseModel):
    """Represents a suggested edge template."""

    name: str
    description: str
    relationship_count: int = 0
    reason: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return self.model_dump()
