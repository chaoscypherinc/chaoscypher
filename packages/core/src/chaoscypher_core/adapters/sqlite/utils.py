# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Utility functions for SqliteAdapter.

Provides common conversion and utility functions for SQLite adapter operations.
"""

from typing import Any, cast


def entity_to_dict(entity: Any) -> dict[str, Any] | None:
    """Convert SQLModel entity to dictionary with JSON-serializable values.

    Args:
        entity: SQLModel entity

    Returns:
        Dictionary representation with datetime objects converted to ISO strings

    """
    if entity is None:
        return None

    # Use model_dump for Pydantic v2 / SQLModel.
    # mode='json' ensures datetime objects are serialized as ISO strings.
    if hasattr(entity, "model_dump"):
        result = cast("dict[str, Any]", entity.model_dump(mode="json"))
        if result:
            return result
        # SQLAlchemy expires instance __dict__ on commit; Pydantic's
        # model_dump then returns ``{}`` because it doesn't trigger the
        # lazy-load refresh. Re-hydrate via getattr per declared field
        # (each access triggers the SA refresh) and round-trip through
        # Pydantic for mode='json' conversion semantics.
        fields = getattr(type(entity), "model_fields", None)
        if fields:
            hydrated = {name: getattr(entity, name, None) for name in fields}
            return cast("dict[str, Any]", type(entity)(**hydrated).model_dump(mode="json"))
        return result
    # Manual conversion for non-Pydantic entities
    return {key: getattr(entity, key) for key in entity.__dict__ if not key.startswith("_")}


def entities_to_dicts(entities: list[Any]) -> list[dict[str, Any]]:
    """Convert list of SQLModel entities to dictionaries.

    Args:
        entities: List of SQLModel entities

    Returns:
        List of dictionary representations

    """
    return [cast("dict[str, Any]", entity_to_dict(entity)) for entity in entities]
