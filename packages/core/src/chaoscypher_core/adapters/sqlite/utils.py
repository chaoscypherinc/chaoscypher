# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Utility functions for SqliteAdapter.

Provides common conversion and utility functions for SQLite adapter operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func


if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from sqlalchemy.sql.elements import ColumnElement


#: Ids per ``IN (...)`` list: inside SQLite's bound-parameter limit on every
#: build (999 before 3.32), whatever else the statement binds.
SQL_IN_BATCH = 500


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


def json_property_path(key: str) -> str:
    """The SQLite JSON path of the top-level property ``key`` (quoted, so any key is one step)."""
    return '$."' + key.replace('"', '""') + '"'


def json_property_is_true(column: Any, key: str) -> ColumnElement[bool]:
    """SQL: the JSON ``column`` holds ``true`` under the top-level ``key`` (``true`` exactly).

    The one SQL reading of a pipeline flag (``name_identity.pipeline_flag``):
    ``json_type(...) = 'true'``, never the string ``"true"`` or the number 1.
    Read by the node and edge flag scans and the minimal edge listing.
    """
    return func.json_type(column, json_property_path(key)) == "true"


def id_batches(ids: Sequence[str] | None) -> Iterator[list[str] | None]:
    """``ids`` de-duplicated, in slices of ``SQL_IN_BATCH`` for an ``IN (...)`` filter.

    ``None`` (no id filter) yields ``None`` once, so a caller writes one loop
    for both the scoped and the whole-table read; an empty ``ids`` yields
    nothing.
    """
    if ids is None:
        yield None
        return
    unique = list(dict.fromkeys(ids))
    for start in range(0, len(unique), SQL_IN_BATCH):
        yield unique[start : start + SQL_IN_BATCH]
