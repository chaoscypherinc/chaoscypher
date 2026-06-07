# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Retry-policy protocol for services.

Services needing retry behavior accept a ``RetryPolicyPort`` instead of
hard-importing ``retry_on_db_lock_*`` from ``chaoscypher_core.utils.retry``.
The Engine wires a concrete policy (e.g. ``DbLockRetryPolicy``) at
construction time.

Phase 2 migrates ~8 service files off direct ``retry_on_db_lock_*`` imports
onto this port via DI.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar, runtime_checkable


T = TypeVar("T")


@runtime_checkable
class RetryPolicyPort(Protocol):
    """Minimal service-facing contract for retry behavior.

    Services accept this type (or any structural equivalent) via DI instead
    of importing the retry helpers directly. Keep this port narrow — only
    the two operations services actually need belong here.
    """

    def run_sync(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        """Run ``fn(*args, **kwargs)`` under this retry policy.

        Args:
            fn: Sync callable to invoke.
            *args: Positional arguments forwarded to ``fn``.
            **kwargs: Keyword arguments forwarded to ``fn``.

        Returns:
            Return value of ``fn`` on success.

        """
        ...

    async def run_async(
        self,
        fn: Callable[..., Awaitable[T]],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Await ``fn(*args, **kwargs)`` under this retry policy.

        Args:
            fn: Async callable to invoke.
            *args: Positional arguments forwarded to ``fn``.
            **kwargs: Keyword arguments forwarded to ``fn``.

        Returns:
            Return value of ``fn`` on success.

        """
        ...
