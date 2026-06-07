# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-database bootstrap + management.

Module layout:

- ``engine`` — startup initialization (create_all + reflective migrator
  + seed) plus per-database engine accessors.
- ``adapter_factory`` — fresh-per-call ``SqliteAdapter`` factory tracked
  by request ContextVar (used by the cortex middleware for cleanup).
- ``seed`` — idempotent seeding of system tools, default workflows, and
  default triggers.
- ``repository`` — list/switch/delete operations for the multi-database
  management feature (previously ``chaoscypher_core.database``).

This subpackage is distinct from ``chaoscypher_core.adapters.sqlite.engine``,
which is the lower-level SQLModel engine configuration. ``database.engine``
is the app-level bootstrap that ``adapter_factory`` calls.
"""

from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
from chaoscypher_core.database.engine import (
    database_exists,
    get_db_path,
    get_engine,
    init_database,
)
from chaoscypher_core.database.seed import seed_default_data


__all__ = [
    "database_exists",
    "get_db_path",
    "get_engine",
    "get_sqlite_adapter",
    "init_database",
    "seed_default_data",
]
