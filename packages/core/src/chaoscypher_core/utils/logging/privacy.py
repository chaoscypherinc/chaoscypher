# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Logs written while a memory is handled carry none of its text (spec 2026-10-24 D16).

An error from a model or a database can quote what it was given, and the extraction logs
the lines, names and labels it read: all of it may be a memory's text. Code that handles a
memory runs inside :func:`private_text`; while it does, the log processor
:func:`withhold_private_text` (part of ``configure_logging``'s chain, so it covers every
logger, structlog and stdlib alike) keeps, at INFO and above, only an allow-list of
known-safe fields (event names, ids, counts, types, stages, lengths, times) and withholds
every other text and the traceback. DEBUG keeps everything, for a person who turned it on
to debug.

:func:`log_failure` is how a failure is logged where the text may be private: the error's
type at the given level, and its message and traceback only outside :func:`private_text`
(inside, the traceback goes to DEBUG).
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator, Mapping, MutableMapping
from contextvars import ContextVar
from enum import Enum
from typing import Any


WITHHELD = "(withheld: may quote a memory)"
"""What a withheld value reads inside :func:`private_text` at INFO and above."""

SAFE_KEYS = frozenset(
    {
        # the record itself: event name, level, time, logger, call site, process
        "event",
        "level",
        "log_level",
        "logger",
        "logger_name",
        "timestamp",
        "filename",
        "func_name",
        "lineno",
        "module",
        "pathname",
        "process",
        "process_name",
        "thread",
        "thread_name",
        # where the work is: fixed names, never a memory's words
        "stage",
        "state",
        "status",
        "outcome",
        "error_type",
        "database_name",
        "operation",
        "queue",
        "provider",
        "model",
        "mode",
        "phase",
        "step",
        "attempt",
        "attempts",
    }
)
"""The keys whose text is logged inside :func:`private_text`; every other text is withheld."""

SAFE_SUFFIXES = (
    "_id",
    "_ids",
    "_count",
    "_type",
    "_ms",
    "_seconds",
    "_length",
    "_len",
    "_size",
    "_bytes",
    "_number",
    "_index",
    "_stage",
    "_state",
    "_status",
)
"""Key endings that name an id, a count, a type, a length or a time (``source_id``, ``duration_ms``)."""

_TRACEBACK_FIELDS = ("exc_info", "exception", "stack_info", "stack")

_DEBUG_METHODS = frozenset({"debug", "trace"})

_private: ContextVar[bool] = ContextVar("chaoscypher_private_text", default=False)


@contextlib.contextmanager
def private_text() -> Iterator[None]:
    """Mark the code run inside as handling private text (a memory); tasks it starts inherit it."""
    token = _private.set(True)
    try:
        yield
    finally:
        _private.reset(token)


def outside_private_text() -> contextvars.Context:
    """A copy of the current context with :func:`private_text` off, for a task to run in.

    A task copies its creator's context: one started inside :func:`private_text` would keep
    every later log of its own withheld. Code that marks its own private part (a link)
    starts its task in this context instead.
    """
    context = contextvars.copy_context()
    context.run(_private.set, False)
    return context


def text_is_private() -> bool:
    """Whether the running code is inside :func:`private_text`."""
    return _private.get()


def _safe_key(key: str) -> bool:
    return key in SAFE_KEYS or key.startswith("_") or key.endswith(SAFE_SUFFIXES)


def _holds_text(value: Any) -> bool:
    """Whether ``value`` is, or holds, a string that is no enum member (a fixed name)."""
    if isinstance(value, Enum):
        return False
    if isinstance(value, str):
        return True
    if isinstance(value, Mapping):
        return any(_holds_text(k) or _holds_text(v) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_holds_text(item) for item in value)
    return False


def withhold_private_text(
    _logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """A structlog processor: inside :func:`private_text`, above DEBUG, only known-safe text.

    The record keeps its event name, call site, ids, counts, types, lengths and times
    (:data:`SAFE_KEYS`, :data:`SAFE_SUFFIXES`), its numbers, flags and enum values; every
    other string, and every list or mapping that holds one, reads :data:`WITHHELD`, and the
    traceback is dropped. A record from the ``logging`` module keeps its logger's name and
    its message template, never the arguments formatted into it.
    """
    if not _private.get() or method_name in _DEBUG_METHODS:
        return event_dict
    for key in _TRACEBACK_FIELDS:
        event_dict.pop(key, None)
    record = event_dict.get("_record")
    if record is not None and not event_dict.get("_from_structlog", False):
        template = record.msg if isinstance(record.msg, str) else ""
        event_dict["event"] = f"{record.name}: {template}" if template else record.name
        event_dict.pop("positional_args", None)
    for key, value in event_dict.items():
        if not _safe_key(key) and _holds_text(value):
            event_dict[key] = WITHHELD
    return event_dict


def log_failure(
    logger: Any, event: str, error: BaseException, *, level: str = "error", **fields: Any
) -> None:
    """Log that ``error`` happened, saying only its type inside :func:`private_text`.

    Outside it, the record carries the error's message and traceback as ``logger.exception``
    would; inside it, the traceback goes to a ``<event>_detail`` record at DEBUG.
    """
    emit = getattr(logger, level)
    if _private.get():
        emit(event, error_type=type(error).__name__, **fields)
        detail = f"{event}_detail"
        logger.debug(detail, exc_info=error)
        return
    emit(event, error_type=type(error).__name__, error_message=str(error), exc_info=error, **fields)


__all__ = [
    "SAFE_KEYS",
    "SAFE_SUFFIXES",
    "WITHHELD",
    "log_failure",
    "outside_private_text",
    "private_text",
    "text_is_private",
    "withhold_private_text",
]
