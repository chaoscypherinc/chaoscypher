# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A read-time guard for a database from before the core entity types (clean break, 2026-10-08).

A database whose graph was committed before the core types is not migrated
in place: its nodes keep their old per-type names (``Character``,
``Military Unit``) and their old templates. The fix is to export it with the
previous version, update, and import the export; the ccx import maps the old
types onto the core ones (``services.package.importer.core_types``).

Opening such a database is not an error and rewrites nothing: the product
logs :data:`PRE_CORE_TYPES_MESSAGE` once per database (per process) and the
UI shows it as a one-line notice (``GET /databases/current``). One predicate
(:func:`is_pre_core_types_database`), one message.

The predicate reads ``graph_nodes``: a node committed from a source (its
``source_id`` set) whose ``entity_type`` is set and is not a core type.
Since the core types every writer of ``entity_type`` (the commit, the ccx
import) places it with ``type_rescue.core_typed``, so such a row comes only
from before them. A node a user made by hand has no ``entity_type`` and no
source, and is never read. (The sources' ``extraction_packs`` cannot tell
the two apart: migration 0017 backfilled the list on every source.)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
import structlog

from chaoscypher_core.services.sources.engine.extraction.domains.core_types import CORE_TYPES


if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


logger = structlog.get_logger(__name__)


PRE_CORE_TYPES_MESSAGE: Final[str] = (
    "This database was made before the core entity types and is not migrated in place: "
    "export it with the previous version, update, then import the export "
    "(the import maps the old types onto the core ones)."
)
"""The one message of the guard: the log warning and the UI notice."""

_QUERY = sa.text(
    "SELECT 1 FROM graph_nodes"
    " WHERE database_name = :database_name"
    " AND source_id IS NOT NULL"
    " AND entity_type IS NOT NULL AND entity_type != ''"
    " AND entity_type NOT IN :core_types"
    " LIMIT 1"
).bindparams(sa.bindparam("core_types", expanding=True))

_warned: set[str] = set()
"""Databases (``<file>:<name>``) this process has warned about already."""


def is_pre_core_types_database(engine: Engine, database_name: str) -> bool:
    """Whether ``database_name`` holds a graph committed before the core entity types.

    True when a ``graph_nodes`` row of the database comes from a source and
    has an ``entity_type`` that is not one of ``CORE_TYPES``. A database
    whose schema is not there (a fresh file, a blocked upgrade) or that
    cannot be read is not one: the guard never fails an open.
    """
    try:
        with engine.connect() as connection:
            row = connection.execute(
                _QUERY, {"database_name": database_name, "core_types": list(CORE_TYPES)}
            ).first()
    except sa.exc.SQLAlchemyError:
        logger.debug("core_types_guard_unreadable", database_name=database_name)
        return False
    return row is not None


def pre_core_types_notice(engine: Engine, database_name: str) -> str | None:
    """:data:`PRE_CORE_TYPES_MESSAGE` for a database from before the core types, else None.

    Logs the message as a warning the first time this process finds the
    database to be one (``database_predates_core_types``); a later call
    returns the notice without logging again.
    """
    if not is_pre_core_types_database(engine, database_name):
        return None
    key = f"{engine.url.database}:{database_name}"
    if key not in _warned:
        _warned.add(key)
        logger.warning(
            "database_predates_core_types",
            database_name=database_name,
            message=PRE_CORE_TYPES_MESSAGE,
        )
    return PRE_CORE_TYPES_MESSAGE


__all__ = [
    "PRE_CORE_TYPES_MESSAGE",
    "is_pre_core_types_database",
    "pre_core_types_notice",
]
