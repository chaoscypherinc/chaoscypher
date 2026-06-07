# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SQLite Storage Adapter for ChaosCypher Knowledge Engine.

Provides SQLite database storage implementation using SQLModel.
This is the default and only storage adapter.

Components:
    - SqliteAdapter: Main adapter class implementing all storage protocols
    - models: SQLModel table definitions
    - engine: Database engine creation and initialization

``SafeSession`` (our ``sqlmodel.Session`` subclass that wraps ``commit``
in exponential-backoff retry for SQLite write-lock contention) lives at
``chaoscypher_core.adapters.sqlite.safe_session`` but is deliberately
NOT re-exported here — it is adapter-internal infrastructure. External
callers should consume the adapter via ``SqliteAdapter.transaction()``
and should never construct or type-hint against ``SafeSession``
directly.

Session helpers (``get_session`` / ``get_db_session``) live at
``chaoscypher_core.adapters.sqlite.session`` and are similarly not
re-exported from this barrel for the same reason.

Usage:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

    adapter = SqliteAdapter(db_path="data/databases/default/app.db")
    adapter.connect()

    # Create workflow
    workflow = adapter.create_workflow({
        "id": "workflow_001",
        "database_name": "default",
        "name": "Research Pipeline",
        ...
    })

    adapter.disconnect()

    # Or use as context manager
    with SqliteAdapter(db_path="data/app.db") as adapter:
        workflow = adapter.get_workflow("workflow_001")
"""

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import (
    dispose_all_engines,
    evict_engine,
    initialize_database,
)


__all__ = [
    "SqliteAdapter",
    "dispose_all_engines",
    "evict_engine",
    "initialize_database",
]
