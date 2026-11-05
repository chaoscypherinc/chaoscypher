# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The CLI's logging setup: one call every CLI entry point makes.

Core logs a failure with ``logger.exception`` (the merger's ``merger_package_failed``,
``merger_failed`` and many more), and at the CLI's default level (WARNING) that prints the
whole traceback. For a database error the traceback carries SQLAlchemy's
``[SQL: <the statement>]`` and its ``sqlalche.me`` link, so ``compose build``'s output still
held the SQL a few lines above its plain ``Build failed:`` line. Below DEBUG,
:func:`plain_database_errors` keeps the event and replaces such an exception with the one
plain line ``describe_database_error`` gives; at ``LOG_LEVEL=DEBUG`` everything is kept.
Cortex and Neuron configure their own logging and keep their tracebacks.
"""

from __future__ import annotations

import os
import re
import sys
from typing import IO, TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import MutableMapping


_RAW_DATABASE_TEXT = ("[SQL:", "sqlalche.me")

# SQLAlchemy appends to the driver's message: "\n[SQL: <statement>]",
# "\n[parameters: ...]" and "\n(Background on this error at: https://sqlalche.me/...)".
_SQLALCHEMY_LINK = re.compile(r"\s*\(Background on this error at: https?://sqlalche\.me[^)]*\)")


def _scrub(text: str) -> str:
    """Cut SQLAlchemy's statement block and its link out of a string, keeping the rest."""
    head = text.split("\n[SQL:", 1)[0]
    return _SQLALCHEMY_LINK.sub("", head)


def _logged_exception(exc_info: object) -> BaseException | None:
    """The exception an event's ``exc_info`` refers to, or None."""
    if exc_info is True:
        return sys.exc_info()[1]
    if isinstance(exc_info, BaseException):
        return exc_info
    if isinstance(exc_info, tuple) and len(exc_info) == 3:
        value = exc_info[1]
        return value if isinstance(value, BaseException) else None
    return None


def plain_database_errors(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Replace a logged database error's traceback with its one plain line.

    The event and its other fields are kept; ``exc_info`` is dropped and ``error``
    holds the plain line (an ``error`` field already set is kept unless it is the
    raw SQLAlchemy text). Any other exception is left as it is.

    Every string field is scrubbed too, with or without ``exc_info``: Core logs
    ``error_message=str(e)`` and ``error=str(e)`` in hundreds of places, and the
    SQL block and link are cut out of them (the driver's own message stays).
    """
    for key, value in list(event_dict.items()):
        if isinstance(value, str) and key != "exc_info":
            event_dict[key] = _scrub(value)
    exc = _logged_exception(event_dict.get("exc_info"))
    if exc is None:
        return event_dict
    from chaoscypher_core.database.errors import describe_database_error

    described = describe_database_error(exc)
    if described is None:
        return event_dict
    event_dict.pop("exc_info", None)
    current = event_dict.get("error")
    if current is None or any(raw in str(current) for raw in _RAW_DATABASE_TEXT):
        event_dict["error"] = described
    return event_dict


def configure_cli_logging(log_level: str | None = None, stream: IO[str] | None = None) -> None:
    """Configure logging for a CLI process (the first call wins, as in Core).

    Args:
        log_level: The level; ``LOG_LEVEL`` (default ``WARNING``) when None.
        stream: Where logs go; stdout when None. ``mcp`` and ``mount`` pass stderr,
            since their stdout carries JSON-RPC.
    """
    from chaoscypher_core.utils.logging import configure_logging

    level = log_level or os.getenv("LOG_LEVEL") or "WARNING"
    extra = () if level.upper() == "DEBUG" else (plain_database_errors,)
    configure_logging(log_level=level, stream=stream, extra_processors=extra)


__all__ = ["configure_cli_logging", "plain_database_errors"]
