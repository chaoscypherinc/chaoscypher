# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``get_stuck_extracting_sources`` must not defeat its own ``load_only``.

The method selects sources under a 7-column projection, then per iteration
calls ``get_extraction_job`` — which opens with ``session.expire_all()``.
Expiring the still-live ``SourceRow`` instances made the following
``_entity_to_dict`` fall through to its getattr re-hydration path, and that
refresh does NOT replay ``load_only``: it re-read every column of the row,
once per stuck source, at every worker boot.

Pinned by the observable consequence — the returned dicts carry the projected
columns and nothing else. Defeat the projection again and they widen.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


# The exact set selected by the ``load_only`` in ``get_stuck_extracting_sources``.
PROJECTED_COLUMNS = {
    "id",
    "database_name",
    "filename",
    "status",
    "current_extraction_job_id",
    "error_message",
    "error_stage",
}


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Full SqliteAdapter backed by a per-test tmp_path database."""
    db_dir = tmp_path / "cc-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


def _seed_extracting_source(
    adapter: SqliteAdapter,
    source_id: str,
    *,
    job_id: str | None,
    database_name: str = "test",
) -> None:
    """Seed a source sitting in ``extracting``, optionally pointing at a job."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
        }
    )
    updates: dict[str, object] = {"status": "extracting"}
    if job_id is not None:
        updates["current_extraction_job_id"] = job_id
    adapter.update_file(source_id, database_name=database_name, updates=updates)


def test_stuck_source_without_job_stays_projected(adapter: SqliteAdapter) -> None:
    """A stuck source with no job returns exactly the projected columns."""
    _seed_extracting_source(adapter, "src-no-job", job_id=None)

    stuck = adapter.get_stuck_extracting_sources("test")

    assert len(stuck) == 1
    assert set(stuck[0]) == PROJECTED_COLUMNS
    assert stuck[0]["id"] == "src-no-job"


def test_job_lookup_does_not_widen_the_projection(adapter: SqliteAdapter) -> None:
    """The per-source ``get_extraction_job`` must not re-hydrate the full row.

    This is the regression itself: ``get_extraction_job`` calls
    ``expire_all()``, and before the fix that turned every following
    ``_entity_to_dict`` into an unprojected 162-column refresh SELECT.
    """
    _seed_extracting_source(adapter, "src-a", job_id="job-a")
    _seed_extracting_source(adapter, "src-b", job_id="job-b")

    for source_id, job_id in (("src-a", "job-a"), ("src-b", "job-b")):
        adapter.create_extraction_job(job_id, source_id, "test")
        adapter.update_extraction_job(job_id, {"status": "failed"})

    stuck = adapter.get_stuck_extracting_sources("test")

    assert len(stuck) == 2
    for entry in stuck:
        # Projected columns plus the one key the method adds itself.
        assert set(entry) == PROJECTED_COLUMNS | {"extraction_job_status"}
        assert entry["extraction_job_status"] == "failed"


def test_running_job_is_not_reported_stuck(adapter: SqliteAdapter) -> None:
    """A source whose job is still running is skipped, not returned."""
    _seed_extracting_source(adapter, "src-live", job_id="job-live")
    adapter.create_extraction_job("job-live", "src-live", "test")
    adapter.update_extraction_job("job-live", {"status": "running"})

    assert adapter.get_stuck_extracting_sources("test") == []
