# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Reseed resilience in _reset_knowledge_graph.

clear_all commits inside its own transaction, so the template delete is
durable BEFORE seed_default_templates runs — a seed failure used to leave
the database with zero node templates behind a generic failed task.
seed_default_templates is documented idempotent, so the reset retries it
once and otherwise raises a typed OperationError naming the recovery.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.exceptions import OperationError
from chaoscypher_core.services.reset.operations import _reset_knowledge_graph


def _fake_adapter() -> MagicMock:
    adapter = MagicMock()

    @contextmanager
    def _transaction() -> Any:
        yield

    adapter.transaction = _transaction
    adapter.session = MagicMock()
    return adapter


def _run(seed_mock: MagicMock) -> dict[str, object]:
    stats: dict[str, object] = {}
    graph_repo = MagicMock()
    graph_repo.clear_all.return_value = {
        "nodes_removed": 1,
        "edges_removed": 2,
        "templates_removed": 3,
    }
    with (
        patch(
            "chaoscypher_core.database.adapter_factory.get_sqlite_adapter",
            return_value=_fake_adapter(),
        ),
        patch(
            "chaoscypher_core.repo_factories.get_graph_repository",
            return_value=graph_repo,
        ),
        patch(
            "chaoscypher_core.database.seed.seed_default_templates",
            seed_mock,
        ),
    ):
        _reset_knowledge_graph("test-db", stats)
    return stats


@pytest.mark.unit
def test_transient_seed_failure_is_retried_once() -> None:
    seed = MagicMock(side_effect=[RuntimeError("db locked"), None])
    stats = _run(seed)
    assert seed.call_count == 2
    assert stats["templates_deleted"] == 3


@pytest.mark.unit
def test_persistent_seed_failure_raises_typed_actionable_error() -> None:
    seed = MagicMock(side_effect=RuntimeError("disk full"))
    with pytest.raises(OperationError, match="no node templates"):
        _run(seed)
    assert seed.call_count == 2
