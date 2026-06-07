# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Database engine management for ChaosCypher Knowledge Engine SQLite Adapter.

Provides SQLite database engine creation and management.

NOTE: This is the engine version (framework-agnostic).
For backend-specific initialization and migrations, see backend/shared/database/init.py
"""

import threading
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import create_engine, event
from sqlmodel import SQLModel


if TYPE_CHECKING:
    from sqlite3 import Connection as SQLite3Connection

    from sqlalchemy.engine import Connection, Engine

logger = structlog.get_logger(__name__)

# Engine cache (one database path → one engine)
_engines: dict[str, Engine] = {}
_engines_lock = threading.Lock()


def get_db_path(
    database_name: str,
    data_dir: str = "/data",
    databases_subdir: str = "databases",
    app_db_filename: str = "app.db",
) -> Path:
    """Get the path to the database file for a given database name.

    The parameter defaults match the Docker container layout. Callers should
    prefer passing values from their settings (e.g., ``PathSettings``) rather
    than relying on these defaults.

    Args:
        database_name: Name of the database
        data_dir: Root data directory (default: /data for Docker compatibility)
        databases_subdir: Subdirectory for databases (default: databases)
        app_db_filename: Database filename (default: app.db)

    Returns:
        Path to database file

    """
    return Path(data_dir) / databases_subdir / database_name / app_db_filename


def database_exists(db_path: Path) -> bool:
    """Check if database file exists.

    Args:
        db_path: Path to database file

    Returns:
        True if database file exists

    """
    return db_path.exists()


def get_engine(
    db_path: Path,
    echo: bool = False,
    connection_timeout: int | None = None,
    busy_timeout_ms: int | None = None,
    cache_size_kb: int | None = None,
) -> Engine:
    """Get or create SQLAlchemy engine for the specified database path.

    Engines are cached per database path for connection pooling.
    Settings parameters are only used on first call for a given path.

    Args:
        db_path: Path to SQLite database file
        echo: Enable SQL logging (default: False)
        connection_timeout: Seconds to wait for locks (default from DatabaseSettings)
        busy_timeout_ms: Milliseconds for busy_timeout PRAGMA (default from DatabaseSettings)
        cache_size_kb: Cache size in KB (default from DatabaseSettings)

    Returns:
        SQLAlchemy Engine instance

    """
    from chaoscypher_core.settings import DatabaseSettings

    defaults = DatabaseSettings()
    effective_timeout = (
        connection_timeout if connection_timeout is not None else defaults.connection_timeout_secs
    )
    effective_busy_ms = busy_timeout_ms if busy_timeout_ms is not None else defaults.busy_timeout_ms
    effective_cache_kb = cache_size_kb if cache_size_kb is not None else defaults.cache_size_kb

    # Normalize path for consistent caching
    db_path = Path(db_path).resolve()
    db_path_str = str(db_path)

    with _engines_lock:
        if db_path_str not in _engines:
            # Ensure parent directory exists
            db_path.parent.mkdir(parents=True, exist_ok=True)

            # Create engine with SQLite-specific settings for concurrency
            # - check_same_thread=False: Allow connection sharing across threads
            # - timeout: Wait for locks before failing
            # - pool_size/max_overflow: High limits to avoid QueuePool exhaustion
            #   under concurrent requests (dashboard fires 4+ API calls at once,
            #   each creating multiple adapters)
            engine = create_engine(
                f"sqlite:///{db_path}",
                connect_args={
                    "check_same_thread": False,
                    "timeout": effective_timeout,
                },
                pool_size=defaults.pool_size,
                max_overflow=defaults.max_overflow,
                pool_timeout=defaults.pool_timeout,
                echo=echo,
            )

            # Enable WAL mode and other optimizations on every connection
            # WAL allows concurrent reads during writes (critical for multi-process)
            @event.listens_for(engine, "connect")
            def set_sqlite_pragma(
                dbapi_connection: SQLite3Connection, _connection_record: object
            ) -> None:
                # Load sqlite-vec extension for vector search
                import sqlite_vec  # type: ignore[import-untyped]

                dbapi_connection.enable_load_extension(True)
                sqlite_vec.load(dbapi_connection)
                dbapi_connection.enable_load_extension(False)

                cursor = dbapi_connection.cursor()
                try:
                    # WAL mode: Allows concurrent reads during writes
                    cursor.execute("PRAGMA journal_mode=WAL")
                    # Synchronous NORMAL: Good balance of safety and speed
                    cursor.execute("PRAGMA synchronous=NORMAL")
                    # Increase cache size (negative = KB)
                    cursor.execute(f"PRAGMA cache_size=-{effective_cache_kb}")
                    # Enable foreign keys
                    cursor.execute("PRAGMA foreign_keys=ON")
                    # Busy timeout: Wait for locks instead of failing immediately
                    # This is critical for multi-process writes (Cortex + Neuron workers)
                    cursor.execute(f"PRAGMA busy_timeout={effective_busy_ms}")
                finally:
                    cursor.close()

            _engines[db_path_str] = engine
            logger.info("database_engine_created", db_path=db_path_str, wal_mode=True)

        return _engines[db_path_str]


def evict_engine(db_path: Path) -> None:
    """Dispose and remove a cached engine for the given database path.

    Args:
        db_path: Path to the SQLite database file.

    """
    db_path_str = str(Path(db_path).resolve())
    with _engines_lock:
        engine = _engines.pop(db_path_str, None)
    if engine is not None:
        engine.dispose()
        logger.info("database_engine_evicted", db_path=db_path_str)


def dispose_all_engines() -> None:
    """Dispose and remove all cached database engines.

    Iterates over every cached engine, disposes its connection pool,
    and clears the cache. Useful when restoring a backup or resetting
    databases so that subsequent operations create fresh connections.
    """
    with _engines_lock:
        for db_path_str, engine in list(_engines.items()):
            engine.dispose()
            logger.info("database_engine_disposed", db_path=db_path_str)
        _engines.clear()


def initialize_database(
    database_name: str,
    data_dir: str = "/data",
    databases_subdir: str = "databases",
    app_db_filename: str = "app.db",
    *,
    run_migrations: bool = True,
) -> None:
    """Initialize SQLite database: create tables from SQLModel, then apply schema updates.

    Args:
        database_name: Name of the database to initialize.
        data_dir: Root data directory (default: /data for Docker compatibility).
        databases_subdir: Subdirectory for databases.
        app_db_filename: Database filename.
        run_migrations: Run reflective schema updates after create_all (default: True).
    """
    logger.info("database_initialization_started", database_name=database_name, data_dir=data_dir)

    db_path = get_db_path(database_name, data_dir, databases_subdir, app_db_filename)
    engine = get_engine(db_path)

    SQLModel.metadata.create_all(engine, checkfirst=True)
    logger.info("database_tables_created", database_name=database_name, db_path=str(db_path))

    if run_migrations:
        apply_schema_updates(engine)

    logger.info(
        "database_initialization_completed", database_name=database_name, db_path=str(db_path)
    )


def apply_schema_updates(engine: Engine) -> None:
    """Post-create-all housekeeping: idempotent data backfills + drift logger.

    The column-adding reflective migrator that used to live here was
    retired in Phase 3 of the Alembic migration framework. Every
    column/table/constraint change now ships as an Alembic migration
    file — see ``packages/core/src/chaoscypher_core/database/migrations/``.

    What survives from the reflective era:

    - Idempotent data backfills for legacy DBs that pre-date certain
      columns being populated (group_index, template visuals).
    - CREATE INDEX IF NOT EXISTS statements for indexes that predate the
      baseline migration. On fresh DBs built by Alembic these are no-ops
      because the index is already in the baseline.
    - The constraint-drift logger, which now doubles as a CI signal via
      the autogenerate-diff test in
      ``tests/unit/database/migrations/test_no_undeclared_changes.py``.

    File-lock scope stays the same as before so concurrent workers
    don't race into overlapping backfills.
    """
    from sqlalchemy import inspect, text

    db_url = str(engine.url)
    lock_handle = None
    db_path_str = db_url.removeprefix("sqlite:///")
    if db_url.startswith("sqlite:///") and db_path_str and db_path_str != ":memory:":
        db_file = Path(db_path_str)
        lock_path = db_file.with_suffix(db_file.suffix + ".schema.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = open(lock_path, "w")  # noqa: SIM115
        try:
            from chaoscypher_core.utils.filelock import lock_file

            lock_file(lock_handle, blocking=True)
        except ImportError:
            lock_handle.close()
            lock_handle = None

    try:
        inspector = inspect(engine)

        with engine.connect() as conn:
            # Backfill group_index from chunk_metadata JSON for existing chunks.
            # Pre-dates the explicit column; kept as a one-off idempotent fix for
            # any DB that was populated before the column was added to the model.
            if "document_chunks" in inspector.get_table_names():
                try:
                    result = conn.execute(
                        text("""
                            UPDATE document_chunks
                            SET group_index = json_extract(chunk_metadata, '$.hierarchical_group.group_index')
                            WHERE chunk_metadata IS NOT NULL
                            AND group_index IS NULL
                            AND json_extract(chunk_metadata, '$.hierarchical_group.group_index') IS NOT NULL
                        """)
                    )
                    if result.rowcount:
                        conn.commit()
                        logger.info("group_index_backfilled", rows=result.rowcount)
                except Exception as e:
                    logger.debug("group_index_backfill_skipped", reason=str(e))

            # Backfill icon/color for existing templates. Idempotent —
            # skips templates that already have visuals set.
            if "graph_templates" in inspector.get_table_names():
                _backfill_template_visuals(conn)

        log_schema_constraint_drift(engine)
    finally:
        if lock_handle is not None:
            try:
                from chaoscypher_core.utils.filelock import unlock_file

                unlock_file(lock_handle)
            finally:
                lock_handle.close()


def log_schema_constraint_drift(engine: Engine) -> None:
    """Warn about FK/UNIQUE/CHECK constraints declared in models but absent from live DB.

    The reflective migrator cannot add these to existing SQLite tables. This
    surfaces the gap so operators know which guardrails are missing on which
    tables and can plan a reset or manual rebuild.

    Args:
        engine: The SQLAlchemy engine bound to the live database to inspect.

    """
    from sqlalchemy import inspect

    inspector = inspect(engine)
    db_tables = set(inspector.get_table_names())

    for table_name, table in SQLModel.metadata.tables.items():
        if table_name not in db_tables:
            continue

        declared_fks = {
            (fk.parent.name, fk.column.table.name, fk.column.name) for fk in table.foreign_keys
        }
        live_fks = {
            (fk["constrained_columns"][0], fk["referred_table"], fk["referred_columns"][0])
            for fk in inspector.get_foreign_keys(table_name)
            if fk.get("constrained_columns") and fk.get("referred_columns")
        }
        for src_col, ref_table, ref_col in declared_fks - live_fks:
            logger.warning(
                "schema_constraint_drift",
                table=table_name,
                kind="foreign_key",
                column=src_col,
                references=f"{ref_table}.{ref_col}",
            )

        from sqlalchemy import UniqueConstraint

        declared_uniques = {
            tuple(sorted(col.name for col in c.columns))
            for c in table.constraints
            if isinstance(c, UniqueConstraint)
        }
        live_uniques = {
            tuple(sorted(u["column_names"])) for u in inspector.get_unique_constraints(table_name)
        }
        for cols in declared_uniques - live_uniques:
            logger.warning(
                "schema_constraint_drift",
                table=table_name,
                kind="unique",
                columns=list(cols),
            )

        declared_checks = {
            c.name
            for c in table.constraints
            if c.__class__.__name__ == "CheckConstraint" and c.name
        }
        with engine.connect() as conn:
            row = conn.exec_driver_sql(
                f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table_name}'"
            ).fetchone()
            live_sql = (row[0] if row else "") or ""
            for name in declared_checks:
                if f"CONSTRAINT {name}" not in live_sql:
                    logger.warning(
                        "schema_constraint_drift",
                        table=table_name,
                        kind="check",
                        constraint_name=name,
                    )


def _backfill_template_visuals(conn: Connection) -> None:
    """Backfill icon/color for templates missing them.

    Uses mapping table first, then falls back to domain JSONLD configs.
    """
    from sqlalchemy import text

    from chaoscypher_core.templates.visuals import (
        resolve_edge_visuals,
        resolve_node_visuals,
    )

    # Build domain template lookup from all JSONLD files (secondary source)
    domain_lookup = _build_domain_visual_lookup()

    rows = conn.execute(
        text(
            "SELECT id, name, template_type FROM graph_templates "
            "WHERE icon IS NULL OR color IS NULL"
        )
    ).fetchall()

    updated = 0
    for row in rows:
        tid, tname, ttype = row[0], row[1], row[2]

        # Try mapping table first
        visuals = resolve_edge_visuals(tname) if ttype == "edge" else resolve_node_visuals(tname)

        # Fall back to domain JSONLD configs
        if not visuals["icon"] and not visuals["color"]:
            domain_match = domain_lookup.get(tname.lower())
            if domain_match:
                visuals = {"icon": domain_match.get("icon"), "color": domain_match.get("color")}

        if visuals["icon"] or visuals["color"]:
            conn.execute(
                text(
                    "UPDATE graph_templates SET "
                    "icon = COALESCE(icon, :icon), "
                    "color = COALESCE(color, :color) "
                    "WHERE id = :id"
                ),
                {"icon": visuals["icon"], "color": visuals["color"], "id": tid},
            )
            updated += 1

    if updated:
        conn.commit()
        logger.info("template_visuals_backfilled", rows=updated)


def _build_domain_visual_lookup() -> dict[str, dict[str, str | None]]:
    """Build a name->visuals lookup from all domain JSONLD files."""
    import json
    from pathlib import Path

    lookup: dict[str, dict[str, str | None]] = {}
    # __file__ is adapters/sqlite/engine.py -> go up to chaoscypher_core/
    domains_dir = (
        Path(__file__).parent.parent.parent
        / "services"
        / "sources"
        / "engine"
        / "extraction"
        / "domains"
        / "plugins"
    )
    if not domains_dir.exists():
        return lookup

    for f in domains_dir.glob("*.jsonld"):
        try:
            cfg = json.loads(f.read_text())
            for ttype_key in ("node_templates", "edge_templates"):
                for t in cfg.get("templates", {}).get(ttype_key, []):
                    name = t.get("name", "")
                    if name and (t.get("icon") or t.get("color")):
                        lookup[name.lower()] = {"icon": t.get("icon"), "color": t.get("color")}
        except Exception:  # noqa: S112
            continue

    return lookup
