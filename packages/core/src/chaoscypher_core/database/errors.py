# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""One plain line for a database failure.

SQLAlchemy's message for a failed statement runs to several lines: the driver's
message, then ``[SQL: <the statement>]``, ``[parameters: ...]`` and finally
``(Background on this error at: https://sqlalche.me/...)``. A caller that keeps
only the last line of a failed command's output (a desktop app running
``chaoscypher compose build``) saw nothing but that link. ``describe_database_error``
is the one place that turns such an error into a sentence saying what failed, on
which database, at which step; every surface that prints a database error calls it.
The full exception still belongs in the log.
"""

from __future__ import annotations

import re
import sqlite3
from typing import TYPE_CHECKING

from sqlalchemy.exc import DBAPIError


if TYPE_CHECKING:
    from pathlib import Path


_MISSING_TABLE = re.compile(r"no such table:\s*(\S+)")

# How far to follow ``__cause__`` / ``.orig`` links before giving up: a cycle
# guard, far deeper than any real wrapping.
_MAX_CHAIN = 16


def _find_database_error(exc: BaseException) -> BaseException | None:
    """Return the most specific database error in ``exc``'s chain, or None.

    Walks ``exc``, a SQLAlchemy ``DBAPIError``'s ``.orig`` and ``__cause__``.
    The first ``sqlite3.Error`` wins; a ``DBAPIError`` whose driver error is not
    sqlite3 (another driver) is returned itself so it still reads as a database
    error.
    """
    fallback: BaseException | None = None
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(seen) < _MAX_CHAIN:
        seen.add(id(current))
        if isinstance(current, sqlite3.Error):
            return current
        if isinstance(current, DBAPIError):
            if fallback is None:
                fallback = current
            orig = current.orig
            if isinstance(orig, BaseException) and id(orig) not in seen:
                current = orig
                continue
        current = current.__cause__
    return fallback


# SQLAlchemy starts its message with the driver error's class, "(sqlite3.OperationalError) ".
_SQLALCHEMY_PREFIX = re.compile(r"^\([\w.]+\)\s*")


def _first_line(value: object) -> str:
    """The first non-empty line of ``value`` as text, or ``""``."""
    lines = str(value).strip().splitlines()
    return lines[0].strip() if lines else ""


def _driver_message(error: BaseException) -> str:
    """The driver's own short message: never the SQL text or the SQLAlchemy link.

    A ``DBAPIError`` with no driver error (``orig`` is None) or an empty one falls
    back to the first line of its own message, without SQLAlchemy's class prefix,
    and to the error's type name when that says nothing either: never "None".
    """
    if isinstance(error, DBAPIError):
        if error.orig is not None:
            message = _first_line(error.orig)
            if message:
                return message
        message = _SQLALCHEMY_PREFIX.sub("", _first_line(error))
    else:
        message = _first_line(error)
    return message if message and message != "None" else type(error).__name__


def describe_database_error(
    exc: BaseException,
    *,
    db_path: Path | str | None = None,
    step: str | None = None,
) -> str | None:
    """Turn a database error into one plain line, or None if it is not one.

    Args:
        exc: Any exception; a SQLAlchemy or sqlite3 error is found through
            ``.orig`` and ``__cause__``.
        db_path: The database file the failure happened on, when known.
        step: What was being done, phrased to follow "while" (e.g.
            ``"importing package pack-a"``).

    Returns:
        The sentence, or None when ``exc`` carries no database error, so the
        caller keeps its own message. The sentence never contains SQL text,
        parameters or the SQLAlchemy link.
    """
    error = _find_database_error(exc)
    if error is None:
        return None

    message = _driver_message(error)
    lowered = message.lower()
    where = f"the database at {db_path}" if db_path is not None else "the database"
    hint = ""

    missing = _MISSING_TABLE.search(message)
    if missing is not None:
        what = f'{where} has no "{missing.group(1)}" table'
        hint = "it may not be set up, or was replaced while open"
    elif "database is locked" in lowered:
        # SQLITE_BUSY: another connection holds the write lock. "database table is
        # locked" (SQLITE_LOCKED) is a conflict inside one connection, not another
        # process, so it falls to the generic branch below.
        what = f"{where} is busy: another process is writing to it"
    elif "readonly database" in lowered or "read-only" in lowered:
        what = f"{where} is read-only"
    elif "disk is full" in lowered:
        what = f"{where} could not be written: the disk is full"
    elif "file is not a database" in lowered or "disk image is malformed" in lowered:
        file = f"the file at {db_path}" if db_path is not None else "the database file"
        what = f"{file} is not a readable database"
        hint = "it may be damaged"
    else:
        at = f" at {db_path}" if db_path is not None else ""
        what = f"a database error{at}: {message}"

    if step:
        what = f"{what} while {step}"
    if hint:
        what = f"{what} ({hint})"
    return what


__all__ = ["describe_database_error"]
