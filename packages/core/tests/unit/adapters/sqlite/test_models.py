# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SQLModel table definitions in the SQLite adapter."""


def test_trigger_workflow_id_cascade_fk(tmp_path):
    """triggers.workflow_id must declare a FK to workflows.id with ON DELETE CASCADE."""
    from sqlalchemy import inspect
    from sqlmodel import SQLModel, create_engine

    from chaoscypher_core.adapters.sqlite import models as _  # noqa: F401

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)

    fks = inspect(engine).get_foreign_keys("triggers")
    wf = next((fk for fk in fks if fk["constrained_columns"] == ["workflow_id"]), None)
    assert wf is not None, "triggers.workflow_id must have a foreign key"
    assert wf["referred_table"] == "workflows"
    assert wf["options"].get("ondelete") == "CASCADE"


def test_trigger_execution_fks(tmp_path):
    """trigger_executions.trigger_id and workflow_execution_id must both have CASCADE FKs."""
    from sqlalchemy import inspect
    from sqlmodel import SQLModel, create_engine

    from chaoscypher_core.adapters.sqlite import models as _  # noqa: F401

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)

    fks = {
        fk["constrained_columns"][0]: fk
        for fk in inspect(engine).get_foreign_keys("trigger_executions")
        if fk["constrained_columns"]
    }
    assert "trigger_id" in fks, "trigger_executions.trigger_id must have a foreign key"
    assert fks["trigger_id"]["referred_table"] == "triggers"
    assert fks["trigger_id"]["options"].get("ondelete") == "CASCADE"

    assert "workflow_execution_id" in fks, (
        "trigger_executions.workflow_execution_id must have a foreign key"
    )
    assert fks["workflow_execution_id"]["referred_table"] == "workflow_executions"
    assert fks["workflow_execution_id"]["options"].get("ondelete") == "CASCADE"


def test_extraction_submission_source_id_has_cascade_fk(tmp_path):
    """source_id must declare a FK to sources.id with ON DELETE CASCADE."""
    from sqlalchemy import inspect
    from sqlmodel import SQLModel, create_engine

    from chaoscypher_core.adapters.sqlite import models as _  # noqa: F401

    engine = create_engine(f"sqlite:///{tmp_path / 'fk.db'}")
    SQLModel.metadata.create_all(engine)

    fks = inspect(engine).get_foreign_keys("extraction_submissions")
    src = [fk for fk in fks if fk["constrained_columns"] == ["source_id"]]
    assert src, "source_id must have a foreign key"
    assert src[0]["referred_table"] == "sources"
    assert src[0]["options"].get("ondelete") == "CASCADE"


def test_step_execution_step_id_cascade_fk(tmp_path):
    """workflow_step_executions.step_id must declare a FK to workflow_steps.id with ON DELETE CASCADE."""
    from sqlalchemy import inspect
    from sqlmodel import SQLModel, create_engine

    from chaoscypher_core.adapters.sqlite import models as _  # noqa: F401

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)

    fks = inspect(engine).get_foreign_keys("workflow_step_executions")
    step = next((fk for fk in fks if fk["constrained_columns"] == ["step_id"]), None)
    assert step is not None, "workflow_step_executions.step_id must have a foreign key"
    assert step["referred_table"] == "workflow_steps"
    assert step["options"].get("ondelete") == "CASCADE"


def test_chat_message_chat_id_cascade_fk(tmp_path):
    """chat_messages.chat_id must declare a FK to chats.id with ON DELETE CASCADE."""
    from sqlalchemy import inspect
    from sqlmodel import SQLModel, create_engine

    from chaoscypher_core.adapters.sqlite import models as _  # noqa: F401

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)

    fks = inspect(engine).get_foreign_keys("chat_messages")
    chat = next((fk for fk in fks if fk["constrained_columns"] == ["chat_id"]), None)
    assert chat is not None, "chat_messages.chat_id must have a foreign key"
    assert chat["referred_table"] == "chats"
    assert chat["options"].get("ondelete") == "CASCADE"


def test_source_tag_assignment_unique(tmp_path):
    """SourceTagAssignment(source_id, tag_id) must be unique."""
    from sqlalchemy import inspect
    from sqlmodel import SQLModel, create_engine

    from chaoscypher_core.adapters.sqlite import models as _  # noqa: F401

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)

    uniques = inspect(engine).get_unique_constraints("source_tag_assignments")
    matching = [u for u in uniques if set(u.get("column_names", [])) == {"source_id", "tag_id"}]
    assert matching, (
        f"source_tag_assignments must have a UNIQUE(source_id, tag_id) constraint; got: {uniques}"
    )


def test_graph_template_name_not_unique_per_db(tmp_path):
    """graph_templates must NOT carry UNIQUE(database_name, name).

    Templates are scoped per-source: two sources can each own a template
    named "Location" without colliding. App-layer ``upsert_template``
    enforces idempotency via a content-addressed stable ID.
    """
    from sqlalchemy import inspect
    from sqlmodel import SQLModel, create_engine

    from chaoscypher_core.adapters.sqlite import models as _  # noqa: F401

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)

    uniques = inspect(engine).get_unique_constraints("graph_templates")
    matching = [u for u in uniques if set(u.get("column_names", [])) == {"database_name", "name"}]
    assert not matching, (
        "graph_templates must not constrain (database_name, name) — "
        f"templates are per-source. Found: {matching}"
    )


# ============================================================================
# Status-column CHECK constraints were dropped 2026-04-22 (CC038 tech debt).
# Validation now happens at the Python StrEnum layer. Existing production
# DBs still carry the constraints until migration 0011 runs; new DBs created
# via SQLModel.metadata.create_all() no longer include them.
# ============================================================================


def test_pending_search_index_table_exists(tmp_path):
    """PendingSearchIndex SQLModel must create the pending_search_index table."""
    from sqlalchemy import inspect
    from sqlmodel import SQLModel, create_engine

    from chaoscypher_core.adapters.sqlite import models as _  # noqa: F401

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)

    assert "pending_search_index" in inspect(engine).get_table_names()

    cols = {c["name"] for c in inspect(engine).get_columns("pending_search_index")}
    for expected in (
        "id",
        "kind",
        "item_id",
        "source_id",
        "reason",
        "attempts",
        "last_error",
        "created_at",
    ):
        assert expected in cols, f"missing column {expected}"

    uniques = inspect(engine).get_unique_constraints("pending_search_index")
    assert any(set(u.get("column_names", [])) == {"kind", "item_id"} for u in uniques), (
        f"Expected UNIQUE(kind, item_id); got: {uniques}"
    )


def test_chunk_extraction_task_has_started_at_and_cancelled_at(tmp_path):
    """ChunkExtractionTask has started_at and cancelled_at columns."""
    from sqlalchemy import inspect
    from sqlmodel import SQLModel, create_engine

    from chaoscypher_core.adapters.sqlite import models as _  # noqa: F401

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)

    cols = {c["name"]: c for c in inspect(engine).get_columns("chunk_extraction_tasks")}
    assert "started_at" in cols, "started_at column must exist"
    assert "cancelled_at" in cols, "cancelled_at column must exist"
    # Both nullable (default None)
    assert cols["started_at"]["nullable"], "started_at must be nullable"
    assert cols["cancelled_at"]["nullable"], "cancelled_at must be nullable"


def test_chat_has_composite_db_user_index(tmp_path):
    """Chat has a composite index on (database_name, user_id)."""
    from sqlalchemy import inspect
    from sqlmodel import SQLModel, create_engine

    from chaoscypher_core.adapters.sqlite import models as _  # noqa: F401

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)

    indexes = inspect(engine).get_indexes("chats")
    composite = [
        idx for idx in indexes if set(idx.get("column_names", [])) == {"database_name", "user_id"}
    ]
    assert composite, f"Expected composite index on (database_name, user_id); got: {indexes}"
