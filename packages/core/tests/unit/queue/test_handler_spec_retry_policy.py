# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract: ``QueueClient`` records each handler's transient-retry policy.

``HandlerSpec.retry_on_transient`` governs whether the queue worker retries
the handler on transient (non-crash) errors. Bare callables registered
without a ``HandlerSpec`` wrapper default to ``True`` (queue-owned retry),
matching historical behavior. Handlers that own their own retry counter
(e.g. extract_chunk) opt out by registering with ``retry_on_transient=False``.
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.queue.client import QueueClient
from chaoscypher_core.queue.handler_spec import HandlerSpec


async def _ok_handler(
    data: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    return {"data": data}


@pytest.fixture
def client() -> QueueClient:
    return QueueClient()


def test_handler_spec_records_transient_retry_policy(client: QueueClient) -> None:
    """``retry_on_transient`` is stored alongside the registered handler."""
    client.register_handlers(
        "operations",
        {
            "handler_owned_retry": HandlerSpec(
                handler=_ok_handler,
                retry_on_crash=True,
                retry_on_transient=False,
            ),
            "queue_owned_retry": _ok_handler,
        },
    )

    assert client.get_transient_retry_policy("operations", "handler_owned_retry") is False
    assert client.get_transient_retry_policy("operations", "queue_owned_retry") is True
    assert client.get_transient_retry_policy("operations", "missing") is True
