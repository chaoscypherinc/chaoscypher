# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for RetryPolicyPort and DbLockRetryPolicy.

RetryPolicyPort is the service-facing contract for retry behavior. Phase 2
migrates ~8 services off direct ``retry_on_db_lock_*`` imports onto this port.
DbLockRetryPolicy is the default implementation, wrapping the existing
SQLite-lock retry helpers.
"""

import pytest

from chaoscypher_core.ports.retry import RetryPolicyPort
from chaoscypher_core.utils.retry import DbLockRetryPolicy


def test_retry_policy_port_is_runtime_checkable() -> None:
    """Duck-typed class with run_sync + run_async satisfies the Protocol."""

    class DuckPolicy:
        def run_sync(self, fn, /, *args, **kwargs):  # type: ignore[override]
            return fn(*args, **kwargs)

        async def run_async(self, fn, /, *args, **kwargs):  # type: ignore[override]
            return await fn(*args, **kwargs)

    assert isinstance(DuckPolicy(), RetryPolicyPort)


def test_object_missing_run_sync_does_not_satisfy_port() -> None:
    """Negative: a class without run_sync fails isinstance."""

    class IncompletePolicy:
        async def run_async(self, fn, /, *args, **kwargs):  # type: ignore[override]
            return await fn(*args, **kwargs)

    assert not isinstance(IncompletePolicy(), RetryPolicyPort)


def test_db_lock_retry_policy_satisfies_port() -> None:
    """The default implementation must satisfy its own Protocol."""
    policy = DbLockRetryPolicy()
    assert isinstance(policy, RetryPolicyPort)


def test_run_sync_invokes_callable_and_returns_value() -> None:
    """Happy path: no lock errors — the policy just runs the callable."""
    policy = DbLockRetryPolicy()
    result = policy.run_sync(lambda x, y: x + y, 2, 3)
    assert result == 5


def test_run_sync_forwards_keyword_args() -> None:
    """Keyword args forward through the policy to the wrapped callable."""
    policy = DbLockRetryPolicy()

    def add(x, y, *, scale=1):  # type: ignore[misc]
        return (x + y) * scale

    result = policy.run_sync(add, 2, 3, scale=10)
    assert result == 50


@pytest.mark.asyncio
async def test_run_async_invokes_coroutine_and_returns_value() -> None:
    """Happy path for async version."""
    policy = DbLockRetryPolicy()

    async def add_async(x: int, y: int) -> int:
        return x + y

    result = await policy.run_async(add_async, 2, 3)
    assert result == 5
