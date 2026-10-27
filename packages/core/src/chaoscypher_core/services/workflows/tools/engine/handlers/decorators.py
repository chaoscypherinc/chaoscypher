# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared decorators for tool handler methods.

Provides reusable error-handling patterns to reduce boilerplate
across handler classes. A failed handler answers ``utils.tool_failure``'s
fixed reason, as every catch around a tool call does.
"""

import functools
from collections.abc import Callable
from typing import Any, cast

import structlog

from chaoscypher_core.utils.tool_failure import discard_failed_work, log_tool_failure


logger = structlog.get_logger(__name__)


def tool_handler(operation_name: str) -> Callable:
    """Wrap an async tool handler with standard error handling.

    Catches any ``Exception``, discards what the call left in the handler's graph session
    (``discard_failed_work``: a failed flush would otherwise break every later call that
    shares it), logs the tool's name, the error's type and its code, never its text
    (``log_tool_failure``), and returns ``tool_failure``'s fixed answer.

    Only suitable for handlers that follow the exact pattern of
    returning a dict with ``success`` and ``error`` keys on failure.

    Args:
        operation_name: Event name used in the log message
            (e.g. ``"create_node_failed"``).

    Returns:
        Decorator that wraps an async handler method.

    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            """Run the wrapped handler and map an exception to its fixed answer."""
            try:
                return cast("dict[str, Any]", await func(*args, **kwargs))
            except Exception as e:
                if args:  # the handler's graph session; one with none holds no session
                    discard_failed_work(getattr(args[0], "graph", None))
                return log_tool_failure(logger, operation_name, e, tool=func.__name__)

        return wrapper

    return decorator
