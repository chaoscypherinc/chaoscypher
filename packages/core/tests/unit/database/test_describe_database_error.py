# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``describe_database_error`` turns a real database failure into one plain line.

Every error here is raised by SQLite itself against a database in ``tmp_path``
(through ``sqlite3`` or SQLAlchemy), never typed by hand: the point is that what
the driver really says comes out as a sentence naming the database and the step,
with no SQL statement, no ``[SQL: ...]`` block and no SQLAlchemy link.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import create_engine, text

from chaoscypher_core.database.errors import describe_database_error


_FORBIDDEN = ("SELECT", "INSERT", "[SQL:", "sqlalche.me")


def _assert_plain(message: str | None) -> str:
    assert message is not None
    assert "\n" not in message
    for token in _FORBIDDEN:
        assert token not in message, (token, message)
    return message


def _capture(action: Callable[[], object]) -> BaseException:
    with pytest.raises(Exception) as info:
        action()
    return info.value


def _empty_db(tmp_path: Path) -> Path:
    db = tmp_path / "pack-a.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        conn.commit()
    return db


def _no_such_table_via_sqlalchemy(db: Path) -> BaseException:
    engine = create_engine(f"sqlite:///{db}")
    try:

        def run() -> None:
            with engine.connect() as conn:
                conn.execute(text("SELECT id FROM sources"))

        return _capture(run)
    finally:
        engine.dispose()


def test_missing_table_names_the_table_and_the_database(tmp_path: Path) -> None:
    db = _empty_db(tmp_path)
    exc = _no_such_table_via_sqlalchemy(db)
    # The raw SQLAlchemy message is exactly what Tsukumo used to see the tail of.
    assert "sqlalche.me" in str(exc)

    message = _assert_plain(describe_database_error(exc, db_path=db))

    assert message == (
        f'the database at {db} has no "sources" table '
        "(it may not be set up, or was replaced while open)"
    )


def test_step_is_named_when_given(tmp_path: Path) -> None:
    db = _empty_db(tmp_path)
    exc = _no_such_table_via_sqlalchemy(db)

    message = _assert_plain(
        describe_database_error(exc, db_path=db, step="importing package pack-a")
    )

    assert message.startswith(f'the database at {db} has no "sources" table')
    assert "while importing package pack-a" in message
    assert "(it may not be set up, or was replaced while open)" in message


def test_raw_sqlite3_error_is_described_too(tmp_path: Path) -> None:
    db = _empty_db(tmp_path)
    with closing(sqlite3.connect(db)) as conn:
        exc = _capture(lambda: conn.execute("SELECT id FROM sources"))

    message = _assert_plain(describe_database_error(exc, db_path=db))
    assert 'has no "sources" table' in message


def test_without_a_path_it_says_the_database(tmp_path: Path) -> None:
    db = _empty_db(tmp_path)
    exc = _no_such_table_via_sqlalchemy(db)

    message = _assert_plain(describe_database_error(exc))
    assert message.startswith('the database has no "sources" table')


def test_locked_database_is_busy(tmp_path: Path) -> None:
    db = _empty_db(tmp_path)
    with (
        closing(sqlite3.connect(db, isolation_level=None)) as holder,
        closing(sqlite3.connect(db, timeout=0)) as writer,
    ):
        holder.execute("BEGIN EXCLUSIVE")
        exc = _capture(lambda: writer.execute("INSERT INTO notes (body) VALUES ('x')"))
        holder.execute("ROLLBACK")

    message = _assert_plain(describe_database_error(exc, db_path=db))
    assert "is busy: another process is writing to it" in message
    assert str(db) in message


def test_table_locked_inside_one_connection_is_not_called_busy(tmp_path: Path) -> None:
    """SQLITE_LOCKED is a conflict inside one connection, not another process writing."""
    db = _empty_db(tmp_path)
    with closing(sqlite3.connect(db, isolation_level=None)) as conn:
        conn.executemany("INSERT INTO notes (body) VALUES (?)", [("x",)] * 10)
        reading = conn.execute("SELECT id FROM notes")
        reading.fetchone()
        exc = _capture(lambda: conn.execute("DROP TABLE notes"))

    assert "database table is locked" in str(exc)
    message = _assert_plain(describe_database_error(exc, db_path=db))
    assert "another process" not in message
    assert message == f"a database error at {db}: database table is locked"


def test_empty_driver_error_does_not_read_as_none(tmp_path: Path) -> None:
    """A DBAPIError whose driver error says nothing names its type, never "None"."""
    from sqlalchemy.exc import OperationalError

    db = _empty_db(tmp_path)
    exc = OperationalError("SELECT id FROM notes", {}, sqlite3.OperationalError(""))

    message = _assert_plain(describe_database_error(exc, db_path=db))
    assert "None" not in message
    assert message == f"a database error at {db}: OperationalError"


def test_missing_driver_error_does_not_read_as_none(tmp_path: Path) -> None:
    """SQLAlchemy's stubs say ``orig`` is an exception; at run time it can be None."""
    from sqlalchemy.exc import OperationalError

    db = _empty_db(tmp_path)
    exc = OperationalError("SELECT id FROM notes", {}, cast("BaseException", None))
    assert str(exc).startswith("(builtins.NoneType) None")

    message = _assert_plain(describe_database_error(exc, db_path=db))
    assert "None" not in message
    assert message == f"a database error at {db}: OperationalError"


def test_read_only_database(tmp_path: Path) -> None:
    db = _empty_db(tmp_path)
    with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
        exc = _capture(lambda: conn.execute("INSERT INTO notes (body) VALUES ('x')"))

    message = _assert_plain(describe_database_error(exc, db_path=db))
    assert "is read-only" in message


def test_full_database(tmp_path: Path) -> None:
    db = _empty_db(tmp_path)
    with closing(sqlite3.connect(db)) as conn:
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        conn.execute(f"PRAGMA max_page_count = {page_count}")

        def fill() -> None:
            for _ in range(1000):
                conn.execute("INSERT INTO notes (body) VALUES (?)", ("x" * 4096,))

        exc = _capture(fill)

    message = _assert_plain(describe_database_error(exc, db_path=db))
    assert "disk is full" in message


def test_file_that_is_not_a_database(tmp_path: Path) -> None:
    db = tmp_path / "pack-a.db"
    db.write_bytes(b"this is plainly not a sqlite file " * 64)
    with closing(sqlite3.connect(db)) as conn:
        exc = _capture(lambda: conn.execute("SELECT * FROM sqlite_master").fetchall())

    message = _assert_plain(describe_database_error(exc, db_path=db))
    assert "is not a readable database (it may be damaged)" in message


def test_malformed_database(tmp_path: Path) -> None:
    db = tmp_path / "pack-a.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA page_size = 1024")
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        conn.executemany("INSERT INTO notes (body) VALUES (?)", [("x" * 500,)] * 200)
        conn.commit()
    raw = bytearray(db.read_bytes())
    # Keep the header page; scribble over the b-tree pages after it.
    raw[1024:] = b"\xa5" * (len(raw) - 1024)
    db.write_bytes(bytes(raw))

    with closing(sqlite3.connect(db)) as conn:
        exc = _capture(lambda: conn.execute("SELECT count(*), max(body) FROM notes").fetchall())

    assert "malformed" in str(exc)
    message = _assert_plain(describe_database_error(exc, db_path=db))
    assert "is not a readable database (it may be damaged)" in message


def test_any_other_operational_error_keeps_sqlites_short_message(tmp_path: Path) -> None:
    db = _empty_db(tmp_path)
    engine = create_engine(f"sqlite:///{db}")
    try:

        def run() -> None:
            with engine.connect() as conn:
                conn.execute(text("SELECT nope FROM notes"))

        exc = _capture(run)
    finally:
        engine.dispose()

    message = _assert_plain(describe_database_error(exc, db_path=db))
    assert message == f"a database error at {db}: no such column: nope"


def test_error_wrapped_by_another_exception_is_found_through_its_cause(
    tmp_path: Path,
) -> None:
    db = _empty_db(tmp_path)
    inner = _no_such_table_via_sqlalchemy(db)
    try:
        raise RuntimeError("import failed") from inner
    except RuntimeError as outer:
        wrapped = outer

    message = _assert_plain(describe_database_error(wrapped, db_path=db))
    assert 'has no "sources" table' in message


def test_non_database_error_is_none() -> None:
    assert describe_database_error(ValueError("x")) is None
    assert describe_database_error(ValueError("x"), db_path="pack-a.db", step="s") is None
