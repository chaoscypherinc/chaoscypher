# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests for the upgrade-time recovery dispatcher.

When a worker dequeues a task with an unsupported ``payload_version``
the worker calls ``apply_upgrade_recovery`` to transition the owning
resource (source row, chat) to a user-visible "interrupted by upgrade"
state so the user can retry. These tests pin the dispatch rules and
the resource-state contracts.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import Chat, SourceRow
from chaoscypher_core.constants import (
    OP_CHAT_BACKGROUND,
    OP_CLEANUP_ORPHANS,
    OP_EMBED_CHUNKS,
    OP_EXTRACT_CHUNK,
    OP_FETCH_URL,
    OP_FINALIZE_EXTRACTION,
    OP_IMPORT_ANALYSIS,
    OP_IMPORT_COMMIT,
    OP_IMPORT_INDEXING,
    OP_INDEX_DOCUMENT,
    OP_VISION_FINALIZE,
    OP_VISION_PAGE,
    OPERATION_QUEUE_ROUTING,
)
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.queue.upgrade_recovery import (
    OPERATION_RECOVERY_CATEGORY,
    apply_upgrade_recovery,
)


@pytest.fixture
def db_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Real file-backed SqliteAdapter scoped to tmp_path."""
    db_dir = tmp_path / "ccx-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    yield adapter
    adapter.disconnect()


@pytest.fixture
def patched_adapter_factory(db_adapter: SqliteAdapter):
    """Route ``get_sqlite_adapter`` through the in-memory adapter for the test."""

    def _factory(database_name: str) -> SqliteAdapter:
        assert database_name == "default"
        return db_adapter

    with patch(
        "chaoscypher_core.database.adapter_factory.get_sqlite_adapter",
        side_effect=_factory,
    ):
        # The recovery module imports adapter_factory lazily, so we also need
        # to patch ``disconnect`` to a no-op — disconnecting the shared adapter
        # mid-test would close the SQLite file before assertions run.
        with patch.object(db_adapter, "disconnect", lambda: None):
            yield


# ---------------------------------------------------------------------------
# Registry completeness
# ---------------------------------------------------------------------------


def test_every_routed_operation_has_a_recovery_category() -> None:
    """Every op in OPERATION_QUEUE_ROUTING must be categorized for recovery.

    Catching a missing entry here at test time is cheaper than discovering
    in production that the new OP_FOO falls through to drop_and_log and
    silently leaves users wondering why their import disappeared.
    """
    missing = sorted(set(OPERATION_QUEUE_ROUTING) - set(OPERATION_RECOVERY_CATEGORY))
    assert missing == [], (
        f"Operations registered in OPERATION_QUEUE_ROUTING but missing from "
        f"OPERATION_RECOVERY_CATEGORY: {missing}. Add each to the recovery "
        f"registry — see queue/upgrade_recovery.py contract docstring."
    )


def test_all_source_bound_ops_are_indexed_as_source_bound() -> None:
    """Spot-check: the indexing/extraction/commit/embedding/vision ops are source_bound."""
    expected_source_bound = {
        OP_IMPORT_INDEXING,
        OP_IMPORT_ANALYSIS,
        OP_EXTRACT_CHUNK,
        OP_FINALIZE_EXTRACTION,
        OP_VISION_PAGE,
        OP_VISION_FINALIZE,
        OP_IMPORT_COMMIT,
        OP_INDEX_DOCUMENT,
        OP_EMBED_CHUNKS,
    }
    for op in expected_source_bound:
        assert OPERATION_RECOVERY_CATEGORY[op] == "source_bound", op


def test_chat_background_is_chat_bound() -> None:
    assert OPERATION_RECOVERY_CATEGORY[OP_CHAT_BACKGROUND] == "chat_bound"


def test_fetch_url_and_cleanup_are_drop_and_log() -> None:
    """OP_FETCH_URL has no source yet; cleanup ops are idempotent."""
    assert OPERATION_RECOVERY_CATEGORY[OP_FETCH_URL] == "drop_and_log"
    assert OPERATION_RECOVERY_CATEGORY[OP_CLEANUP_ORPHANS] == "drop_and_log"


# ---------------------------------------------------------------------------
# Source-bound recovery
# ---------------------------------------------------------------------------


def test_source_bound_recovery_marks_source_error(
    db_adapter: SqliteAdapter, patched_adapter_factory: None
) -> None:
    """Source in EXTRACTING flips to ERROR with the upgrade-interruption message."""
    src_id = "src_upgrade_test"
    with db_adapter.transaction():
        session = db_adapter.session
        assert session is not None
        session.add(
            SourceRow(
                id=src_id,
                database_name="default",
                filename="test.pdf",
                filepath="/tmp/test.pdf",
                file_type="pdf",
                status=SourceStatus.EXTRACTING,
            )
        )

    apply_upgrade_recovery(
        operation=OP_EXTRACT_CHUNK,
        data={"source_id": src_id, "database_name": "default"},
        metadata={},
        task_id="tsk_old",
        payload_version=0,
    )

    with db_adapter.transaction():
        session = db_adapter.session
        assert session is not None
        row = session.get(SourceRow, src_id)
        assert row is not None
        assert row.status == SourceStatus.ERROR
        assert row.error_message is not None
        assert "upgrade" in row.error_message.lower()
        assert row.error_stage == "upgrade_recovery"


def test_source_bound_recovery_picks_up_database_name_from_metadata(
    db_adapter: SqliteAdapter, patched_adapter_factory: None
) -> None:
    """When data lacks database_name, metadata is the fallback."""
    src_id = "src_md_fallback"
    with db_adapter.transaction():
        session = db_adapter.session
        assert session is not None
        session.add(
            SourceRow(
                id=src_id,
                database_name="default",
                filename="x.pdf",
                filepath="/tmp/x.pdf",
                file_type="pdf",
                status=SourceStatus.EXTRACTING,
            )
        )

    apply_upgrade_recovery(
        operation=OP_EXTRACT_CHUNK,
        data={"source_id": src_id},  # no database_name
        metadata={"database_name": "default"},
        task_id="tsk_md",
        payload_version=0,
    )

    with db_adapter.transaction():
        session = db_adapter.session
        assert session is not None
        row = session.get(SourceRow, src_id)
        assert row is not None
        assert row.status == SourceStatus.ERROR


def test_source_bound_recovery_missing_source_id_does_not_raise(
    patched_adapter_factory: None,
) -> None:
    """No source_id in the payload → log + swallow, never raise."""
    apply_upgrade_recovery(
        operation=OP_EXTRACT_CHUNK,
        data={"database_name": "default"},
        metadata={},
        task_id="tsk_no_src",
        payload_version=0,
    )  # must not raise


def test_source_bound_recovery_unknown_source_does_not_raise(
    patched_adapter_factory: None,
) -> None:
    """source_id not in DB → log + swallow, never raise."""
    apply_upgrade_recovery(
        operation=OP_EXTRACT_CHUNK,
        data={"source_id": "src_does_not_exist", "database_name": "default"},
        metadata={},
        task_id="tsk_ghost",
        payload_version=0,
    )  # must not raise


# ---------------------------------------------------------------------------
# Chat-bound recovery
# ---------------------------------------------------------------------------


def test_chat_bound_recovery_marks_chat_error(
    db_adapter: SqliteAdapter, patched_adapter_factory: None
) -> None:
    """Chat in 'processing' flips to 'error' for retry UX."""
    chat_id = "chat_upgrade_test"
    with db_adapter.transaction():
        session = db_adapter.session
        assert session is not None
        session.add(
            Chat(
                id=chat_id,
                database_name="default",
                title="Test chat",
                status="processing",
            )
        )

    apply_upgrade_recovery(
        operation=OP_CHAT_BACKGROUND,
        data={"chat_id": chat_id, "database_name": "default"},
        metadata={},
        task_id="tsk_chat_old",
        payload_version=0,
    )

    with db_adapter.transaction():
        session = db_adapter.session
        assert session is not None
        row = session.get(Chat, chat_id)
        assert row is not None
        assert row.status == "error"


def test_chat_bound_recovery_picks_up_chat_id_from_metadata(
    db_adapter: SqliteAdapter, patched_adapter_factory: None
) -> None:
    """When data lacks chat_id, metadata is the fallback (chat send convention)."""
    chat_id = "chat_md_fallback"
    with db_adapter.transaction():
        session = db_adapter.session
        assert session is not None
        session.add(
            Chat(
                id=chat_id,
                database_name="default",
                title="Test",
                status="processing",
            )
        )

    apply_upgrade_recovery(
        operation=OP_CHAT_BACKGROUND,
        data={"database_name": "default"},
        metadata={"chat_id": chat_id},
        task_id="tsk_chat_md",
        payload_version=0,
    )

    with db_adapter.transaction():
        session = db_adapter.session
        assert session is not None
        row = session.get(Chat, chat_id)
        assert row is not None
        assert row.status == "error"


# ---------------------------------------------------------------------------
# Drop-and-log
# ---------------------------------------------------------------------------


def test_drop_and_log_does_not_raise(patched_adapter_factory: None) -> None:
    """System-idempotent ops just log and drop; no state mutation."""
    apply_upgrade_recovery(
        operation=OP_CLEANUP_ORPHANS,
        data={"database_name": "default"},
        metadata={},
        task_id="tsk_idempotent",
        payload_version=0,
    )  # must not raise


def test_unknown_operation_falls_through_to_drop_and_log(
    patched_adapter_factory: None,
) -> None:
    """Unknown op (not in registry) is safe: drop + log."""
    apply_upgrade_recovery(
        operation="op_someone_forgot_to_register",
        data={"source_id": "src_x", "database_name": "default"},
        metadata={},
        task_id="tsk_unknown",
        payload_version=0,
    )  # must not raise
