# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The answer a tool that failed gives: a fixed reason per class of error, never its text.

Every catch around a tool call uses it: ``tool_handler``, the tool executor, the MCP bridge
and the MCP server's own tools, and the research tools. A client gets what it can act on (a
missing template, a busy database); the error's own text, which can quote the SQL and its
parameters and so a memory's words (spec 2026-10-24 D16), stays out of the answer.
"""

from __future__ import annotations

from typing import Any

import pydantic
import structlog
from sqlalchemy.exc import IntegrityError

from chaoscypher_core.exceptions import NotFoundError, PropertyValidationError, ValidationError
from chaoscypher_core.utils.retry import is_sqlite_lock_error


_logger = structlog.get_logger(__name__)

TOOL_FAILURES: dict[str, str] = {
    "DATABASE_BUSY": "Not done: the database is busy with another process's write. Try again.",
    "MISSING_REFERENCE": "Not done: a template or node this call names does not exist.",
    "DUPLICATE": "Not done: something with the same id already exists.",
    "INVALID_INPUT": "Not done: a value given to this tool is not valid.",
    "NOT_FOUND": "Not done: something this call names was not found.",
    "OPERATION_FAILED": "Not done: the operation failed. The server's log has the error's type.",
}
"""The answer a failed tool gives, by code: a fixed sentence, never the error's own text.

The error's text can quote the SQL and its parameters, and so a memory's words (spec
2026-10-24 D16); the client gets what it can act on instead."""


_CONSTRAINT_CODES = (("FOREIGN KEY", "MISSING_REFERENCE"), ("UNIQUE", "DUPLICATE"))
"""SQLite's constraint-failure message openings, and the code each answers."""


def _constraint_code(error: IntegrityError) -> str:
    """The code of a constraint failure, from SQLite's own message (``error.orig``)."""
    reason = str(error.orig)
    return next(
        (code for kind, code in _CONSTRAINT_CODES if reason.startswith(kind)), "OPERATION_FAILED"
    )


def _failure_code(error: BaseException) -> str:
    """The :data:`TOOL_FAILURES` code of ``error``'s class.

    A constraint failure is read first, from SQLite's own message (``error.orig``, never
    the statement or its parameters, and never passed on): it is permanent, and must not
    be told to try again. Busy is SQLite's result code (``is_sqlite_lock_error``).
    """
    if isinstance(error, IntegrityError):
        return _constraint_code(error)
    if is_sqlite_lock_error(error):
        return "DATABASE_BUSY"
    if isinstance(error, (ValidationError, PropertyValidationError, pydantic.ValidationError)):
        return "INVALID_INPUT"
    if isinstance(error, NotFoundError):
        return "NOT_FOUND"
    return "OPERATION_FAILED"


def tool_failure(error: BaseException) -> dict[str, Any]:
    """The answer a tool that raised ``error`` gives: its class's fixed sentence and code."""
    code = _failure_code(error)
    return {"success": False, "error": TOOL_FAILURES[code], "code": code}


def log_tool_failure(
    logger: Any, event: str, error: BaseException, *, level: str = "error", **fields: Any
) -> dict[str, Any]:
    """Log that a tool failed and return its answer (:func:`tool_failure`).

    The record carries the event, the fields the caller names (the tool's name), the error's
    type and its code; never its message, its parameters or a traceback, at any level. A
    graph tool's values are a client's words: an agent saves a memory as a node's
    properties through ``create_node``, outside ``private_text``, and a database error's
    text quotes the statement's parameters.
    """
    answer = tool_failure(error)
    getattr(logger, level)(event, error_type=type(error).__name__, outcome=answer["code"], **fields)
    return answer


def discard_failed_work(*holders: Any) -> None:
    """Discard what a failed call left in each holder's session (``discard_failed_work()``).

    For a catch that answers a failure and goes on, so the next call does not find the
    session needing a rollback. A holder that is None is skipped. A cleanup that raises
    (a rollback on a dead connection) is logged by its type alone and goes no further: it
    must not replace the call's own failure, whose fixed answer the catch still returns.
    """
    for holder in holders:
        if holder is None:
            continue
        try:
            holder.discard_failed_work()
        except Exception as cleanup:
            _logger.error(  # noqa: TRY400 - its type only: a traceback quotes the failed statement
                "tool_cleanup_failed", error_type=type(cleanup).__name__
            )
