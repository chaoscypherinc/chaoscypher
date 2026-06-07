# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract: every SQLModel schema change must be captured in a migration.

Asks Alembic's autogenerate to diff HEAD against live metadata. If it
finds anything, a developer added to a model without writing a
migration — CI red, dev fixes it by running
``alembic revision --autogenerate`` and committing the generated file.
"""

from __future__ import annotations

from pathlib import Path

from alembic.autogenerate import produce_migrations
from alembic.runtime.migration import MigrationContext
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import models as _models  # noqa: F401
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.database.migrations.runner import upgrade_to_head


def test_no_schema_changes_missing_from_migrations(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    upgrade_to_head(db)

    engine = get_engine(db)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn, opts={"compare_type": True, "compare_server_default": True}
        )
        diff = produce_migrations(ctx, SQLModel.metadata)

    ops = diff.upgrade_ops.as_diffs() if diff.upgrade_ops is not None else []
    assert ops == [], (
        "SQLModel metadata has changes not captured by any migration.\n"
        "Run `alembic revision --autogenerate -m '<description>'` to generate one,\n"
        "annotate with CC_TIER and CC_DESCRIPTION, and commit the result.\n"
        f"Diff: {ops}"
    )
