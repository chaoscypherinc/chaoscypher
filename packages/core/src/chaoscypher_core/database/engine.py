# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Backend Database Engine — Initialization Layer.

Thin wrapper around chaoscypher_core.adapters.sqlite.engine that wires in
cortex settings (paths, seed data, process-level init locking).

Schema is owned by Alembic migrations. ``init_database`` routes through
``run_startup_migrations`` (tier-aware: stamps + applies, or gates on a
needs-confirmation migration). ``apply_schema_updates()`` no longer mutates
schema — it only runs idempotent data backfills and logs constraint drift.
"""

import contextlib
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.adapters.sqlite.engine import (
    apply_schema_updates as engine_apply_schema_updates,
)
from chaoscypher_core.adapters.sqlite.engine import (
    database_exists as engine_database_exists,
)
from chaoscypher_core.adapters.sqlite.engine import (
    get_db_path as engine_get_db_path,
)
from chaoscypher_core.adapters.sqlite.engine import (
    get_engine as engine_get_engine,
)
from chaoscypher_core.database.seed import seed_default_data
from chaoscypher_core.utils.filelock import lock_file, unlock_file


if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine

logger = structlog.get_logger(__name__)


def get_db_path(
    database_name: str,
    *,
    data_dir: str | None = None,
    databases_subdir: str | None = None,
    app_db_filename: str | None = None,
) -> Path:
    """Return path to app.db for the given database.

    Path components are injected by callers that hold engine settings. When
    omitted they resolve from ``PathSettings`` defaults — which read
    ``CHAOSCYPHER_DATA_DIR`` (and the static subdir/filename defaults) — so
    this no longer depends on the application settings singleton.
    """
    from chaoscypher_core.settings import PathSettings

    paths = PathSettings()
    return engine_get_db_path(
        database_name=database_name,
        data_dir=data_dir if data_dir is not None else paths.data_dir,
        databases_subdir=(
            databases_subdir if databases_subdir is not None else paths.databases_subdir
        ),
        app_db_filename=(app_db_filename if app_db_filename is not None else paths.app_db_filename),
    )


def database_exists(database_name: str) -> bool:
    """Return True if app.db exists for this database."""
    return engine_database_exists(get_db_path(database_name))


def get_engine(database_name: str) -> Engine:
    """Return cached SQLAlchemy engine for this database."""
    return engine_get_engine(get_db_path(database_name))


def init_database(
    database_name: str,
    *,
    data_dir: str | None = None,
    databases_subdir: str | None = None,
    app_db_filename: str | None = None,
    strict_schema_drift: bool | None = None,
    auto_apply_destructive: bool | None = None,
) -> None:
    """Create tables, apply reflective schema updates, seed defaults.

    Idempotent. Uses a file lock to serialize cross-process initialization
    (e.g., Web UI and Grounding API starting simultaneously).

    Settings are injected by callers that hold engine settings. When omitted
    the path components, the drift-strictness flag, and the auto-apply flag
    resolve from their core group defaults (``PathSettings`` /
    ``DatabaseSettings`` / ``MigrationsSettings``), which honour the relevant
    environment overrides — so this no longer reads the application settings
    singleton.

    Args:
        database_name: Name of the database to initialize.
        data_dir: Root data directory; defaults to ``PathSettings().data_dir``.
        databases_subdir: Databases subdirectory; defaults to PathSettings.
        app_db_filename: SQLite filename; defaults to PathSettings.
        strict_schema_drift: Refuse boot on post-migration schema drift;
            defaults to ``DatabaseSettings().strict_schema_drift`` (True).
        auto_apply_destructive: Auto-apply destructive migrations at startup;
            defaults (via the startup runner) to
            ``MigrationsSettings().auto_apply_destructive``.
    """
    logger.info("database_initialization_started", database_name=database_name)

    if strict_schema_drift is None:
        from chaoscypher_core.settings import DatabaseSettings

        strict_schema_drift = DatabaseSettings().strict_schema_drift

    db_path = get_db_path(
        database_name,
        data_dir=data_dir,
        databases_subdir=databases_subdir,
        app_db_filename=app_db_filename,
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)

    lock_file_path = db_path.parent / ".init.lock"
    with open(lock_file_path, "w") as lock_handle:
        try:
            lock_file(lock_handle, blocking=False)
            logger.info("initialization_lock_acquired", database_name=database_name)
        except BlockingIOError:
            logger.info("waiting_for_another_process_initialization", database_name=database_name)
            lock_file(lock_handle, blocking=True)
            if engine_database_exists(db_path) and db_path.stat().st_size > 1024:
                logger.info(
                    "database_already_initialized_by_another_process",
                    database_name=database_name,
                )
                unlock_file(lock_handle)
                return

        try:
            engine = engine_get_engine(db_path)

            # Import models for SQLModel.metadata registration (side-effect
            # import) — Alembic's env.py relies on this for autogenerate.
            from chaoscypher_core.adapters.sqlite import models as _core_models  # noqa: F401

            # Migrations are the single source of truth for schema. The
            # baseline migration 0001 creates every table; subsequent
            # migrations apply constraint/index/data changes. We no longer
            # call SQLModel.metadata.create_all() here because (a) the
            # baseline duplicates its work on fresh installs, and (b) on
            # pre-Alembic installs create_all would silently add missing
            # tables without the new constraints, defeating the point of
            # routing through Alembic.
            from chaoscypher_core.database.migrations.startup import (
                run_startup_migrations,
            )

            run_startup_migrations(db_path, auto_apply_destructive=auto_apply_destructive)

            # Everything past this point assumes a healthy, at-HEAD schema.
            # If the upgrade gated (a needs-confirmation migration) or
            # failed (run_startup_migrations recorded ready=False after, say,
            # an interrupted upgrade left the schema ahead of its stamp), we
            # MUST NOT run the post-migration steps: they assume schema
            # invariants the blocked DB doesn't satisfy, and would re-crash
            # the boot before the maintenance page can be served. Skip them
            # and let the upgrade-gate middleware serve maintenance instead.
            from chaoscypher_core.database.migrations.state import (
                get_upgrade_state,
            )

            if not get_upgrade_state(db_path).ready:
                logger.warning(
                    "database_initialization_blocked_pending_upgrade",
                    database_name=database_name,
                    message=get_upgrade_state(db_path).message,
                )
                return

            # Post-migration housekeeping: idempotent data backfills +
            # constraint-drift logger. No-op on fresh installs.
            engine_apply_schema_updates(engine)

            # Startup schema-drift gate (P1 operability — see
            # ``database/migrations/drift.py``). CI catches drift during
            # development, but a release shipping without CI would
            # silently boot into a state where the next feature query
            # crashes on a missing column. Strict mode refuses boot;
            # default mode emits a structured error event and continues.
            from chaoscypher_core.database.migrations.drift import (
                check_schema_drift,
            )

            check_schema_drift(
                db_path,
                strict=strict_schema_drift,
            )

            seed_default_data(database_name)
            logger.info("database_initialized_successfully", database_name=database_name)
        finally:
            unlock_file(lock_handle)

    with contextlib.suppress(Exception):
        lock_file_path.unlink(missing_ok=True)
