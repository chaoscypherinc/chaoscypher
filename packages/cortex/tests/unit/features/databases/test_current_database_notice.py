# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``GET /databases/current`` carries the guard's notice for a database from before the core types.

The predicate and the message are core's (``database.core_types_guard``,
pinned on a real upgraded database in core's integration tests); this pins
the wiring: the current database's engine is read, the one message is
passed through as ``notice``, and a database with no file reads nothing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.database.core_types_guard import PRE_CORE_TYPES_MESSAGE
from chaoscypher_cortex.features.databases.models import CurrentDatabaseResponse
from chaoscypher_cortex.features.databases.service import DatabasesService


_SERVICE = "chaoscypher_cortex.features.databases.service"


@pytest.fixture
def service() -> DatabasesService:
    repo = MagicMock()
    repo.get_database.return_value.model_dump.return_value = {
        "name": "old_db",
        "path": "/tmp/old_db",
        "exists": True,
        "size": 1024,
        "last_modified": None,
    }
    settings = MagicMock()
    settings.current_database = "old_db"
    return DatabasesService(database_repository=repo, settings=settings)


def test_a_database_from_before_the_core_types_carries_the_notice(
    service: DatabasesService,
) -> None:
    engine = MagicMock()
    with (
        patch(f"{_SERVICE}.database_exists", return_value=True),
        patch(f"{_SERVICE}.get_engine", return_value=engine) as get_engine,
        patch(f"{_SERVICE}.pre_core_types_notice", return_value=PRE_CORE_TYPES_MESSAGE) as guard,
    ):
        result = service.get_current_database()
    get_engine.assert_called_once_with("old_db")
    guard.assert_called_once_with(engine, "old_db")
    assert result.notice == PRE_CORE_TYPES_MESSAGE


def test_a_database_without_a_file_has_no_notice(service: DatabasesService) -> None:
    with (
        patch(f"{_SERVICE}.database_exists", return_value=False),
        patch(f"{_SERVICE}.pre_core_types_notice") as guard,
    ):
        result = service.get_current_database()
    guard.assert_not_called()
    assert result.notice is None


def test_the_notice_defaults_to_none() -> None:
    assert "notice" in CurrentDatabaseResponse.model_fields
    assert CurrentDatabaseResponse.model_fields["notice"].default is None
