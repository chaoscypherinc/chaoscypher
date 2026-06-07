# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""F47: Pydantic schema validation on ChunkExtractionTask JSON columns.

Three behaviours are pinned here:

  1. **Valid round-trip.** A canonical entity/relationship dict (matching the
     shape produced by ``parse_extraction_output`` plus the ``chunk_index``
     decoration) survives ``RawEntity.model_validate`` / ``RawRelationship.model_validate``
     unchanged.

  2. **Malformed write rejected.** Calling ``validate_raw_entities`` with an
     entity dict missing a required field (``name``) raises ``DataIntegrityError``
     and never persists.

  3. **Legacy/drifted JSON in the DB raises on read.** A real
     ``SqliteAdapter`` row with a malformed ``raw_entities`` list (inserted
     via raw SQL bypassing the validator) is rejected by the read-side
     validator, simulating the finalizer's pre-aggregation guard.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import structlog
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.exceptions import DataIntegrityError
from chaoscypher_core.operations.extraction.schemas import (
    RawEntity,
    RawRelationship,
    validate_raw_entities,
    validate_raw_relationships,
)


# ---------------------------------------------------------------------------
# Canonical fixtures — match parse_extraction_output + chunk_extraction_service
# decoration exactly so a future shape drift breaks these tests loudly.
# ---------------------------------------------------------------------------


def _valid_entity(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "Napoleon",
        "type": "Person",
        "description": "French military and political leader",
        "aliases": ["Bonaparte"],
        "confidence": 0.92,
        "sent_ref": "S1",
        "chunk_index": 0,
    }
    base.update(overrides)
    return base


def _valid_relationship(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "source": 0,
        "target": 1,
        "type": "born_in",
        "confidence": 0.88,
        "justification": "explicit assertion in source",
        "sent_ref": "S2",
        "chunk_index": 0,
    }
    base.update(overrides)
    return base


_LOG = structlog.get_logger("test_chunk_task_validation")


# ---------------------------------------------------------------------------
# Test 1 — Round-trip: canonical dict validates to itself.
# ---------------------------------------------------------------------------


def test_valid_entity_validates_unchanged() -> None:
    entity = _valid_entity()
    model = RawEntity.model_validate(entity)
    # Round-trip via model_dump preserves all fields; extra="allow" keeps unknowns.
    dumped = model.model_dump(exclude_none=True)
    for key, value in entity.items():
        assert dumped[key] == value, f"field {key!r} round-trip changed"


def test_valid_relationship_validates_unchanged() -> None:
    rel = _valid_relationship()
    model = RawRelationship.model_validate(rel)
    dumped = model.model_dump(exclude_none=True)
    for key, value in rel.items():
        assert dumped[key] == value


def test_validate_raw_entities_returns_input_on_success() -> None:
    items = [_valid_entity(), _valid_entity(name="Wellington")]
    result = validate_raw_entities(
        items,
        chunk_task_id="task_a",
        stage="write",
        logger=_LOG,
    )
    assert result is items  # same list object — no copy made


def test_validate_raw_relationships_returns_input_on_success() -> None:
    items = [_valid_relationship(), _valid_relationship(source=1, target=0)]
    result = validate_raw_relationships(
        items,
        chunk_task_id="task_a",
        stage="write",
        logger=_LOG,
    )
    assert result is items


def test_extra_fields_allowed() -> None:
    """``extra="allow"`` keeps the validator forgiving for future annotations."""
    entity = _valid_entity()
    entity["future_field"] = {"some": "annotation"}
    model = RawEntity.model_validate(entity)
    # Pydantic preserves extra fields when extra="allow".
    assert model.future_field == {"some": "annotation"}  # type: ignore[attr-defined]


def test_none_input_yields_empty_list() -> None:
    assert validate_raw_entities(None, chunk_task_id="t", stage="read", logger=_LOG) == []
    assert validate_raw_relationships(None, chunk_task_id="t", stage="read", logger=_LOG) == []


# ---------------------------------------------------------------------------
# Test 2 — Malformed write rejected with DataIntegrityError.
# ---------------------------------------------------------------------------


def test_entity_missing_name_raises_on_write() -> None:
    bad = _valid_entity()
    del bad["name"]
    with pytest.raises(DataIntegrityError) as excinfo:
        validate_raw_entities(
            [bad],
            chunk_task_id="task_b",
            stage="write",
            logger=_LOG,
        )
    err = excinfo.value
    assert err.details["chunk_task_id"] == "task_b"
    assert err.details["stage"] == "write"
    assert err.details["kind"] == "entity"
    assert err.details["index"] == 0
    assert err.code == "DATA_INTEGRITY_ERROR"


def test_entity_missing_type_raises_on_write() -> None:
    bad = _valid_entity()
    del bad["type"]
    with pytest.raises(DataIntegrityError):
        validate_raw_entities([bad], chunk_task_id="t", stage="write", logger=_LOG)


def test_entity_missing_confidence_raises_on_write() -> None:
    bad = _valid_entity()
    del bad["confidence"]
    with pytest.raises(DataIntegrityError):
        validate_raw_entities([bad], chunk_task_id="t", stage="write", logger=_LOG)


def test_entity_missing_sent_ref_raises_on_write() -> None:
    bad = _valid_entity()
    del bad["sent_ref"]
    with pytest.raises(DataIntegrityError):
        validate_raw_entities([bad], chunk_task_id="t", stage="write", logger=_LOG)


def test_relationship_missing_source_raises_on_write() -> None:
    bad = _valid_relationship()
    del bad["source"]
    with pytest.raises(DataIntegrityError) as excinfo:
        validate_raw_relationships([bad], chunk_task_id="task_c", stage="write", logger=_LOG)
    assert excinfo.value.details["kind"] == "relationship"
    assert excinfo.value.details["chunk_task_id"] == "task_c"


def test_relationship_negative_source_raises_on_write() -> None:
    bad = _valid_relationship(source=-1)
    with pytest.raises(DataIntegrityError):
        validate_raw_relationships([bad], chunk_task_id="t", stage="write", logger=_LOG)


def test_relationship_non_integer_source_raises_on_write() -> None:
    bad = _valid_relationship(source="not_an_int")
    with pytest.raises(DataIntegrityError):
        validate_raw_relationships([bad], chunk_task_id="t", stage="write", logger=_LOG)


def test_first_bad_item_in_batch_aborts() -> None:
    """A single bad entity in the middle of a batch raises immediately."""
    items = [_valid_entity(), _valid_entity(confidence="not_a_float"), _valid_entity()]
    with pytest.raises(DataIntegrityError) as excinfo:
        validate_raw_entities(items, chunk_task_id="t", stage="write", logger=_LOG)
    assert excinfo.value.details["index"] == 1


# ---------------------------------------------------------------------------
# Test 3 — Legacy/drift JSON in DB raises DataIntegrityError on read.
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Real SqliteAdapter backed by a tmp_path SQLite file (CC040)."""
    db_dir = tmp_path / "chaoscypher-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    yield adapter
    adapter.disconnect()


def _seed_source_job_task(
    adapter: SqliteAdapter,
    *,
    source_id: str,
    job_id: str,
    task_id: str,
) -> None:
    """Seed source -> job -> chunk task rows so we can write a JSON column."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "default",
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "extracting",
        }
    )
    adapter.create_extraction_job(
        job_id=job_id,
        source_id=source_id,
        database_name="default",
    )
    adapter.create_chunk_task(
        task_id=task_id,
        job_id=job_id,
        database_name="default",
        chunk_index=0,
    )


def test_valid_round_trip_through_adapter(adapter: SqliteAdapter) -> None:
    """Write a valid chunk result, read it back, validate — no drift."""
    source_id = "src_round_trip"
    job_id = "job_round_trip"
    task_id = "task_round_trip"
    _seed_source_job_task(adapter, source_id=source_id, job_id=job_id, task_id=task_id)

    entities = [_valid_entity(name="Alpha"), _valid_entity(name="Beta")]
    relationships = [_valid_relationship(source=0, target=1, type="related_to")]

    adapter.complete_chunk_task_with_output(
        task_id=task_id,
        llm_response_json="{}",
        llm_duration_ms=100,
        raw_entities=entities,
        raw_relationships=relationships,
    )

    completed = adapter.get_completed_chunk_results(job_id)
    assert len(completed) == 1
    row = completed[0]
    assert row["raw_entities"] is not None
    assert row["raw_relationships"] is not None

    # Read-side validation should accept the round-tripped data.
    validate_raw_entities(row["raw_entities"], chunk_task_id=task_id, stage="read", logger=_LOG)
    validate_raw_relationships(
        row["raw_relationships"], chunk_task_id=task_id, stage="read", logger=_LOG
    )

    # The persisted dicts equal what we wrote, modulo dict-order JSON quirks.
    assert row["raw_entities"][0]["name"] == "Alpha"
    assert row["raw_entities"][1]["name"] == "Beta"
    assert row["raw_relationships"][0]["type"] == "related_to"


def test_legacy_drifted_json_raises_on_read(adapter: SqliteAdapter) -> None:
    """A drifted entity dict in raw_entities (legacy data) must raise on read.

    Simulates the finalizer's read-side guard: even if the writer side
    didn't validate (or pre-F47 data is sitting in the DB), the reader
    refuses to aggregate corrupt payloads. Inserts the bad JSON via raw
    UPDATE on the underlying ChunkExtractionTask row to bypass the writer-
    side Pydantic check entirely.
    """
    source_id = "src_drift"
    job_id = "job_drift"
    task_id = "task_drift"
    _seed_source_job_task(adapter, source_id=source_id, job_id=job_id, task_id=task_id)

    # Bypass the validator entirely with a direct ORM write.
    bad_entities = [_valid_entity(), {"type": "Person"}]  # missing 'name', 'confidence', 'sent_ref'
    drifted_json = json.dumps(bad_entities)

    # Use raw SQL to plant the row in 'completed' state with malformed JSON.
    from sqlalchemy import text

    with adapter.transaction():
        adapter.session.execute(
            text(
                "UPDATE chunk_extraction_tasks "
                "SET status = 'completed', "
                "    raw_entities = :ents, "
                "    raw_relationships = :rels, "
                "    completed_at = :now "
                "WHERE id = :task_id"
            ),
            {
                "ents": drifted_json,
                "rels": "[]",
                "now": datetime.now(UTC).isoformat(),
                "task_id": task_id,
            },
        )

    completed = adapter.get_completed_chunk_results(job_id)
    assert len(completed) == 1
    row = completed[0]

    # Read-side validation rejects the drifted data with a clear,
    # chunk-id-anchored DataIntegrityError.
    with pytest.raises(DataIntegrityError) as excinfo:
        validate_raw_entities(row["raw_entities"], chunk_task_id=task_id, stage="read", logger=_LOG)
    err = excinfo.value
    assert err.details["chunk_task_id"] == task_id
    assert err.details["stage"] == "read"
    assert err.details["kind"] == "entity"
    assert err.details["index"] == 1
    assert "raw_entities[1]" in err.message


def test_legacy_drifted_relationship_json_raises_on_read(adapter: SqliteAdapter) -> None:
    """A drifted relationship dict (e.g., source as string) raises on read."""
    source_id = "src_rel_drift"
    job_id = "job_rel_drift"
    task_id = "task_rel_drift"
    _seed_source_job_task(adapter, source_id=source_id, job_id=job_id, task_id=task_id)

    # Plant a legacy V1 relationship using string "source"/"target" (the old
    # name-based format that's no longer supported).
    bad_rels = [
        {
            "source": "Napoleon",  # should be int
            "target": "France",
            "type": "born_in",
            "confidence": 0.9,
            "sent_ref": "S1",
        }
    ]

    from sqlalchemy import text

    with adapter.transaction():
        adapter.session.execute(
            text(
                "UPDATE chunk_extraction_tasks "
                "SET status = 'completed', "
                "    raw_entities = :ents, "
                "    raw_relationships = :rels, "
                "    completed_at = :now "
                "WHERE id = :task_id"
            ),
            {
                "ents": "[]",
                "rels": json.dumps(bad_rels),
                "now": datetime.now(UTC).isoformat(),
                "task_id": task_id,
            },
        )

    completed = adapter.get_completed_chunk_results(job_id)
    row = completed[0]

    with pytest.raises(DataIntegrityError) as excinfo:
        validate_raw_relationships(
            row["raw_relationships"], chunk_task_id=task_id, stage="read", logger=_LOG
        )
    assert excinfo.value.details["kind"] == "relationship"
    assert excinfo.value.details["index"] == 0
