# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Startup schema-drift gate.

After ``run_startup_migrations()`` brings the database up to Alembic
HEAD, this module diffs the live schema against ``SQLModel.metadata``
using Alembic's autogenerate machinery. CI already runs
``test_no_undeclared_changes`` to catch this in development, but a
release shipping without CI would silently boot into a state where the
next feature query crashes on a missing column. This is the production
gate for that failure mode.

Two modes:

* **non-strict (default)** — emit a structured ``schema_drift_detected``
  error event and continue booting. Operators see a loud red line they
  can grep for; production stays up so benign textual drift on legacy
  migrations doesn't brick the system.
* **strict** — raise :class:`SchemaIntegrityError` to refuse boot.
  Opt-in via ``settings.database.strict_schema_drift``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from alembic.autogenerate import produce_migrations
from alembic.runtime.migration import MigrationContext
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import models as _models  # noqa: F401
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.exceptions import SchemaIntegrityError


if TYPE_CHECKING:
    from pathlib import Path


logger = structlog.get_logger(__name__)


# Tables that exist on disk but are intentionally NOT declared in
# SQLModel.metadata. The drift detector compares the live DB against the
# SQLModel declarative layer; without this ignore-list it floods the
# startup log with ~26 false-positive "remove_table" diffs for sqlite-vec
# and FTS5 internal shadow tables plus a couple of raw-SQL auxiliary
# tables.
_DRIFT_IGNORE_TABLES: frozenset[str] = frozenset(
    {
        # Real auxiliary tables created via raw SQL outside SQLModel:
        "fulltext_content",  # FTS5 content table (adapters/sqlite/repos/search.py)
        "search_metadata",  # Search-engine state (adapters/sqlite/repos/search.py)
        "chaoscypher_upgrade_state",  # Migration upgrade tracker (database/migrations/state.py)
    }
)

# Prefixes that match virtual tables + their automatic shadow tables.
_DRIFT_IGNORE_TABLE_PREFIXES: tuple[str, ...] = (
    # sqlite-vec virtual tables (`vec_search_chunks` / `vec_search_nodes` /
    # `vec_search_templates`, created at runtime by the search repo) plus their
    # auto-created shadows (`*_chunks`, `*_rowids`, `*_vector_chunks00`,
    # `*_auxiliary`, `*_info`).
    "vec_search_",
    # FTS5 virtual table (`fulltext_index`) plus its automatic shadows
    # (`fulltext_index_data`, `fulltext_index_idx`, `fulltext_index_docsize`,
    # `fulltext_index_config`).
    "fulltext_index",
)


def _is_ignored_table(name: str | None) -> bool:
    """Return True for tables the drift detector intentionally skips."""
    if not name:
        return False
    if name in _DRIFT_IGNORE_TABLES:
        return True
    return any(name.startswith(prefix) for prefix in _DRIFT_IGNORE_TABLE_PREFIXES)


def _diff_table_name(entry: object) -> str | None:
    """Return the table name an Alembic diff entry refers to, or None.

    Alembic's diff shapes vary by op:
        ``("add_table", Table)`` / ``("remove_table", Table)``
        ``("add_column", schema, table, Column(...))``
        ``("modify_nullable", schema, table, col, {...}, old, new)``
        ``[(...modifs for one table...)]`` (nested list)

    The first string-shaped element (or ``.name`` of a Table) past
    position 0 reliably names the table. SQLite schemas are always
    ``None`` so the schema slot never spoofs a table name.
    """
    if isinstance(entry, list) and entry:
        return _diff_table_name(entry[0])
    if isinstance(entry, tuple) and entry:
        for part in entry[1:]:
            name = getattr(part, "name", None)
            if isinstance(name, str):
                return name
            if isinstance(part, str) and part:
                return part
    return None


def _filter_known_runtime_tables(diffs: list[object]) -> list[object]:
    """Drop diff entries whose subject is in the runtime-tables ignore-list."""
    return [d for d in diffs if not _is_ignored_table(_diff_table_name(d))]


def _collect_schema_diff(db_path: Path) -> list[object]:
    """Return Alembic's autogenerate diff of the live DB vs SQLModel.metadata.

    Empty list means no drift. Each entry is whatever
    ``MigrationContext.as_diffs()`` produces — typically a tuple whose
    first element is the operation name (``add_column``, ``remove_table``,
    etc.) and whose remaining elements are the operation's arguments.
    """
    engine = get_engine(db_path)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn, opts={"compare_type": True, "compare_server_default": True}
        )
        migrations = produce_migrations(ctx, SQLModel.metadata)
    if migrations.upgrade_ops is None:
        return []
    return list(migrations.upgrade_ops.as_diffs())


def _summarize_diff(diffs: list[object]) -> list[dict[str, object]]:
    """Reduce raw Alembic diff entries to a structured summary for logging.

    Alembic emits tuples whose first element is the op name and whose
    remaining shape varies by op. We coerce each into a small dict with
    ``op`` plus a best-effort ``target`` so the log line stays grep-able
    even when the full diff has dozens of entries.
    """
    summary: list[dict[str, object]] = []
    for entry in diffs:
        if isinstance(entry, tuple) and entry:
            op = entry[0]
            # Common shapes:
            #   ("add_column", schema, table, Column(...))
            #   ("remove_column", schema, table, Column(...))
            #   ("add_table", Table(...))
            #   ("remove_table", Table(...))
            #   ("modify_nullable", schema, table, col, {...}, old, new)
            target_parts: list[str] = []
            for part in entry[1:]:
                name = getattr(part, "name", None)
                if isinstance(name, str):
                    target_parts.append(name)
                elif isinstance(part, str) and part:
                    target_parts.append(part)
                if len(target_parts) >= 3:  # noqa: PLR2004 — table.column is enough context
                    break
            summary.append(
                {
                    "op": str(op),
                    "target": ".".join(target_parts) if target_parts else "",
                }
            )
        else:
            # Nested list of column-level ops on a single table — log a
            # single rolled-up entry rather than recursing further.
            summary.append({"op": "nested", "target": repr(entry)[:120]})
    return summary


def check_schema_drift(db_path: Path, *, strict: bool) -> None:
    """Post-migration gate: detect drift between live DB and SQLModel.metadata.

    Args:
        db_path: Path to the SQLite database file that
            ``run_startup_migrations()`` just upgraded.
        strict: When true, raise :class:`SchemaIntegrityError` on any
            diff. When false, log a structured ``schema_drift_detected``
            error event and return normally.

    Raises:
        SchemaIntegrityError: when ``strict`` is true and at least one
            diff entry is reported.

    """
    diffs = _filter_known_runtime_tables(_collect_schema_diff(db_path))
    if not diffs:
        logger.debug("schema_drift_check_clean", db_path=str(db_path))
        return

    summary = _summarize_diff(diffs)
    logger.error(
        "schema_drift_detected",
        db_path=str(db_path),
        diff_count=len(diffs),
        diffs=summary,
        strict=strict,
    )

    if strict:
        raise SchemaIntegrityError(
            f"Schema drift detected after migrations: {len(diffs)} diff(s) "
            f"between live DB and SQLModel.metadata.",
            details={"db_path": str(db_path), "diffs": summary},
        )
