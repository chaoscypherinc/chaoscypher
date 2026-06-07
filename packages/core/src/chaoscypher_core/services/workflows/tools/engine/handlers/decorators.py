# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared decorators for tool handler methods.

Provides reusable error-handling patterns to reduce boilerplate
across handler classes.
"""

import functools
from collections.abc import Callable
from typing import Any, cast

import structlog


logger = structlog.get_logger(__name__)


def tool_handler(operation_name: str) -> Callable:
    """Wrap an async tool handler with standard error handling.

    Catches any ``Exception``, logs it with :func:`logger.exception`,
    and returns ``{"success": False, "error": "Operation failed"}``.

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
            """Run the wrapped handler and map exceptions to a generic failure dict."""
            try:
                return cast("dict[str, Any]", await func(*args, **kwargs))
            except Exception as e:
                logger.exception(operation_name, error=str(e))
                return {"success": False, "error": "Operation failed"}

        return wrapper

    return decorator
