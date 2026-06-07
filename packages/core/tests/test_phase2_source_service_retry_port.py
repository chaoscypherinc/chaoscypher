# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 2 Task D: SourceService consumes RetryPolicyPort."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

from chaoscypher_core.ports.retry import RetryPolicyPort
from chaoscypher_core.services.graph.management.source import SourceService


SOURCE_FILE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "chaoscypher_core"
    / "services"
    / "graph"
    / "management"
    / "source.py"
)


class _FakeRetryPolicy:
    """RetryPolicyPort stand-in that records calls and runs ``fn`` directly."""

    def __init__(self) -> None:
        self.sync_calls: list[tuple[tuple, dict]] = []

    def run_sync(self, fn, /, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.sync_calls.append((args, kwargs))
        return fn(*args, **{k: v for k, v in kwargs.items() if k != "operation_name"})

    async def run_async(self, fn, /, *args, **kwargs):  # type: ignore[no-untyped-def]
        return await fn(*args, **kwargs)


def test_fake_policy_satisfies_port() -> None:
    assert isinstance(_FakeRetryPolicy(), RetryPolicyPort)


def test_source_service_default_retry_policy_is_db_lock_policy() -> None:
    repo = MagicMock()
    svc = SourceService(repository=repo, database_name="test_db")
    from chaoscypher_core.utils.retry import DbLockRetryPolicy

    assert isinstance(svc._retry_policy, DbLockRetryPolicy)


def test_source_service_delete_uses_injected_policy() -> None:
    repo = MagicMock()
    repo.get_orphaned_entity_uris.return_value = []
    repo.get_source.return_value = None
    policy = _FakeRetryPolicy()
    svc = SourceService(repository=repo, database_name="test_db", retry_policy=policy)

    svc.delete_source("source_123")

    assert len(policy.sync_calls) == 1, "retry policy must be invoked once per delete"
    _, kwargs = policy.sync_calls[0]
    assert kwargs["source_id"] == "source_123"
    assert kwargs["operation_name"] == "source_delete"


def test_source_service_no_direct_adapter_retry_import() -> None:
    tree = ast.parse(SOURCE_FILE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "chaoscypher_core.adapters.sqlite.retry", (
                f"{SOURCE_FILE} still imports retry helpers directly"
            )
