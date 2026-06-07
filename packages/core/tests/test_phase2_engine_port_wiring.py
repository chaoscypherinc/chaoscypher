# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 2 Task K: Engine constructs ports once and injects into services."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_engine():
    from chaoscypher_core import Engine

    # ignore_cleanup_errors tolerates Windows keeping SQLite file handles
    # alive past the Engine's disconnect — the test's only goal is to
    # verify port wiring, not file-handle lifecycle.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        data_dir = Path(tmp) / "databases" / "_phase2_wiring"
        data_dir.mkdir(parents=True, exist_ok=True)
        with Engine(data_dir=str(data_dir), initialize_db=True) as engine:
            yield engine


def test_engine_retry_policy_property_returns_port(temp_engine) -> None:
    from chaoscypher_core.ports.retry import RetryPolicyPort

    assert isinstance(temp_engine.retry_policy, RetryPolicyPort)


def test_engine_retry_policy_is_cached(temp_engine) -> None:
    assert temp_engine.retry_policy is temp_engine.retry_policy


def test_engine_retry_policy_is_db_lock_policy(temp_engine) -> None:
    from chaoscypher_core.utils.retry import DbLockRetryPolicy

    assert isinstance(temp_engine.retry_policy, DbLockRetryPolicy)


def test_engine_embedding_provider_alias(temp_engine) -> None:
    from chaoscypher_core.ports.embedding import EmbeddingProviderProtocol

    assert isinstance(temp_engine.embedding_provider, EmbeddingProviderProtocol)
    # Alias returns the same instance as embedding_service
    assert temp_engine.embedding_provider is temp_engine.embedding_service


def test_commit_service_receives_engine_retry_policy(temp_engine) -> None:
    commit_svc = temp_engine.commit_service
    assert commit_svc._retry_policy is temp_engine.retry_policy


def test_commit_service_receives_engine_embedding_provider(temp_engine) -> None:
    commit_svc = temp_engine.commit_service
    assert commit_svc._embedding_provider is temp_engine.embedding_service
