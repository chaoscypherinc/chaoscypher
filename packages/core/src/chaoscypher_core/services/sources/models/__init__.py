# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source processing Domain Models.

Data structures and models for the source processing pipeline.

Structure:
- entities.py: Internal domain models (Entity, Relationship, SuggestedTemplate)

Example:
    from chaoscypher_core.services.sources.models import Entity, Relationship

"""

from chaoscypher_core.services.sources.models.entities import (
    Entity,
    Relationship,
    SuggestedTemplate,
)


__all__ = [
    "Entity",
    "Relationship",
    "SuggestedTemplate",
]
