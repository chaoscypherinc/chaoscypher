# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Alembic environment bootstrap.

Binds target_metadata to SQLModel.metadata so ``alembic revision --autogenerate``
sees every model declared under chaoscypher_core. The engine URL is injected
by the caller via ``config.attributes['connection']`` (online mode) or
``config.set_main_option('sqlalchemy.url', ...)`` (offline mode).

Uses batch mode (``render_as_batch=True``) so operations like ADD CONSTRAINT
on SQLite get translated into rebuild-table transparently — the whole reason
we picked Alembic for this layer.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# Side-effect import: registers every model with SQLModel.metadata.
from chaoscypher_core.adapters.sqlite import models as _models  # noqa: F401


config = context.config

# Alembic's own CLI takes its logging from alembic.ini. The app's runs pass their connection
# (runner._migration_connection) and the app owns logging: fileConfig would swap its root
# handlers for a bare stderr one and, by its default disable_existing_loggers=True, silence
# every logger the app had already created, until a restart.
if config.config_file_name is not None and "connection" not in config.attributes:
    fileConfig(config.config_file_name)


target_metadata = SQLModel.metadata


def _configure_and_run(connection: object) -> None:
    # SQLite's batch mode rebuilds tables via DROP + CREATE. When the
    # target table is FK-referenced by others (workflows ← triggers,
    # workflow_executions, ...), the DROP fails with IntegrityError even
    # though the final state is consistent. Disabling FK enforcement for
    # the migration connection lets batch rebuilds run. The ON below lands
    # only when the run wrote nothing (the pragma is a no-op inside a
    # transaction); the runner (``runner._migration_connection``) turns them
    # on again once the transaction has ended, before the connection goes
    # back to the app's pool.
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    finally:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def run_migrations_offline() -> None:
    """Run migrations in offline (SQL-script-generation) mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in online mode with a live engine.

    The caller (:mod:`chaoscypher_core.database.migrations.runner`) passes
    a pre-built SQLAlchemy connection via ``config.attributes['connection']``
    so we reuse the same engine + PRAGMAs the app uses.
    """
    connectable = config.attributes.get("connection")
    if connectable is None:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
            hide_parameters=True,  # a failed statement never quotes the values it was given
        )
        with connectable.connect() as connection:
            _configure_and_run(connection)
    else:
        _configure_and_run(connectable)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
