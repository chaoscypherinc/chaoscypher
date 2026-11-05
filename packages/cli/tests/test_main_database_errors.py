# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A database error escaping a command prints its one plain line on stderr.

``chaoscypher mcp`` and ``mount`` reserve stdout for JSON-RPC, and ``mcp`` builds
the Engine (``get_context``) before the stdio server starts: a locked, damaged or
read-only database there must not put ``Error: ...`` on the protocol stream.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import structlog
from click.testing import CliRunner


def _real_locked_error(tmp_path: Path) -> Exception:
    """A real SQLAlchemy OperationalError: ``database is locked``."""
    import sqlite3
    from contextlib import closing

    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import OperationalError

    db = tmp_path / "pack-a.db"
    engine = create_engine(f"sqlite:///{db}", connect_args={"timeout": 0})
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE notes (id INTEGER PRIMARY KEY)"))
        with closing(sqlite3.connect(db, isolation_level=None)) as holder:
            holder.execute("BEGIN EXCLUSIVE")
            try:
                with engine.begin() as conn:
                    conn.execute(text("INSERT INTO notes (id) VALUES (1)"))
            except OperationalError as exc:
                return exc
            finally:
                holder.execute("ROLLBACK")
    finally:
        engine.dispose()
    raise AssertionError("the write against a locked database should have failed")


def test_mcp_database_error_leaves_stdout_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chaoscypher_cli.__main__ import main

    error = _real_locked_error(tmp_path)
    monkeypatch.setattr(sys, "argv", ["chaoscypher", "mcp"])

    def _locked(*_args: Any, **_kwargs: Any) -> None:
        raise error

    with (
        patch("chaoscypher_core.database.migrations.startup.run_startup_migrations"),
        patch(
            "chaoscypher_core.database.migrations.state.get_upgrade_state",
            return_value=SimpleNamespace(ready=True),
        ),
        patch("chaoscypher_cli.mcp.command.get_context", side_effect=_locked),
        # Under the test's default structlog config every level prints to stdout;
        # the real `mcp` routes logs to stderr. Capture them to check the debug record.
        structlog.testing.capture_logs() as logs,
    ):
        result = CliRunner().invoke(main, ["mcp"])

    assert result.exit_code == 1, (result.stdout, result.stderr)
    assert isinstance(result.exception, SystemExit)
    assert result.stdout == ""
    last = [line for line in result.stderr.splitlines() if line.strip()][-1]
    assert last == (
        "Error: the database is busy: another process is writing to it while running chaoscypher mcp"
    )
    assert "Traceback" not in result.stderr
    assert "[SQL:" not in result.stderr
    assert "sqlalche.me" not in result.stderr
    # The full exception (with its SQL) goes to the debug log instead.
    records = [r for r in logs if r["event"] == "cli_database_error"]
    assert len(records) == 1, logs
    assert records[0]["log_level"] == "debug"
    assert records[0]["exc_info"] is True
