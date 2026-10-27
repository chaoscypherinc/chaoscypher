# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A database error never quotes the values of the statement that failed.

SQLAlchemy writes ``[parameters: (...)]`` into a statement error's text, and every log of
it (a message, a traceback, at any level) carried them: a node's label and properties, which
an agent writes a memory into. The app's engines are built with ``hide_parameters=True``:
the error keeps its kind and its statement, never the values.
"""

from __future__ import annotations

import traceback
from pathlib import Path

import pytest
import structlog
from sqlalchemy.exc import IntegrityError

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import GraphNode
from chaoscypher_core.adapters.sqlite.safe_session import SafeSession
from chaoscypher_core.database.migrations.runner import upgrade_to_head
from chaoscypher_core.utils.logging.privacy import log_failure


MARKER = "ZQX-a-memorys-words"


def _failed_insert(tmp_path: Path) -> IntegrityError:
    db = tmp_path / "app.db"
    upgrade_to_head(db)
    session = SafeSession(get_engine(db))
    session.add(
        GraphNode(
            id="node_x",
            database_name="notes",
            graph_name="knowledge",
            template_id="no_such_template",
            label=MARKER,
            properties={"text": MARKER},
        )
    )
    try:
        with pytest.raises(IntegrityError) as raised:
            session.commit()
    finally:
        session.close()
    return raised.value


def test_the_error_keeps_its_kind_and_statement_and_drops_the_values(tmp_path: Path) -> None:
    error = _failed_insert(tmp_path)
    text = str(error)
    assert "FOREIGN KEY constraint failed" in text
    assert "INSERT INTO graph_nodes" in text
    assert MARKER not in text
    assert MARKER not in "".join(traceback.format_exception(error))


def test_no_log_of_the_error_holds_the_values_at_any_level(tmp_path: Path) -> None:
    error = _failed_insert(tmp_path)
    logger = structlog.get_logger("test")
    with structlog.testing.capture_logs() as logs:
        log_failure(logger, "insert_failed", error)  # message and traceback, outside private_text
        logger.debug("insert_failed_detail", exc_info=error)
    rendered = [
        str(r) + "".join(traceback.format_exception(r["exc_info"])) if "exc_info" in r else str(r)
        for r in logs
    ]
    assert rendered and all(MARKER not in line for line in rendered)
