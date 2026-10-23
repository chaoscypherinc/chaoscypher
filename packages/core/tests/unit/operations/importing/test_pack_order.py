# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A pack list keeps its score order wherever it is stored and read; only the label sorts.

The group planner strips content with the domain object detection returns
(the first proposed pack's); the chunk handler and a resumed job re-read the
job's stored packs. All three must read the same first pack, so a two-pack
detection whose score order is not alphabetical (``literary`` before
``investigation``) is stored and read in that order.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.services.sources.engine.extraction.domains.packs import (
    detected_packs_of,
    job_packs,
    pack_columns,
    packs_label,
    source_packs,
)


_SCORE_ORDER = ["literary", "investigation"]
"""Book One's raw opening slice proposes these, in this (non-alphabetical) score order."""


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "test.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adp = SqliteAdapter(str(db_path), database_name="default")
    adp.connect()
    adp.upload_source(
        source_id="src1",
        database_name="default",
        filename="x.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )
    yield adp
    adp.disconnect()


def test_the_columns_keep_the_order_and_only_the_label_sorts() -> None:
    columns = pack_columns({"detected_packs": _SCORE_ORDER})
    assert columns == {"detected_packs": _SCORE_ORDER, "detected_domain": "literary"}
    extraction = pack_columns({"extraction_packs": _SCORE_ORDER})
    assert extraction["extraction_packs"] == _SCORE_ORDER
    assert extraction["extraction_domain"] == "investigation+literary" == packs_label(_SCORE_ORDER)


def test_a_two_pack_job_and_source_keep_the_score_order(adapter: SqliteAdapter) -> None:
    job = adapter.create_extraction_job(
        job_id="job1", source_id="src1", database_name="default", detected_packs=_SCORE_ORDER
    )
    assert job["detected_packs"] == _SCORE_ORDER
    assert job["detected_domain"] == "literary"
    stored = adapter.get_extraction_job("job1")
    assert stored is not None
    assert job_packs(stored) == tuple(_SCORE_ORDER)

    adapter.update_file("src1", "default", {"status": "extracting"})
    adapter.complete_extraction(
        source_id="src1", entities=[], relationships=[], detected_packs=_SCORE_ORDER
    )
    row = adapter.get_source("src1", database_name="default")
    assert row is not None
    assert row["extraction_packs"] == _SCORE_ORDER
    assert row["extraction_domain"] == "investigation+literary"
    assert source_packs(row) == tuple(_SCORE_ORDER)


def test_the_planner_the_chunk_handler_and_resume_read_the_same_stack(
    adapter: SqliteAdapter,
) -> None:
    """Every content-exclusion read is the job's whole stack (Task 7: was the first pack's)."""
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        _job_content_matchers,
    )
    from chaoscypher_core.operations.importing.import_service import _resume_extraction_job
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack

    registry = get_domain_registry()
    detection: dict[str, Any] = {"packs": tuple(_SCORE_ORDER), "detected_domain": "literary"}
    # The planner strips with the fresh job's stack.
    planner = DomainStack.from_stored(registry, detected_packs_of(detection))

    job = adapter.create_extraction_job(
        job_id="job2",
        source_id="src1",
        database_name="default",
        detected_packs=list(detected_packs_of(detection)),
    )

    seen: list[Any] = []
    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=registry,
        ),
        patch(
            "chaoscypher_core.operations.importing.import_service.resolve_source_content_matchers",
            side_effect=lambda **kw: seen.append(kw["domain"]) or [],
        ),
    ):
        _job_content_matchers(adapter, job, "default", MagicMock())
    (chunk_handler,) = seen

    adapter.update_source("src1", {"chunk_count": 1})
    with patch.object(adapter, "get_chunks_for_extraction", return_value=[{"id": "c"}]):
        _job_id, resumed, _chunks = _resume_extraction_job(
            adapter=adapter,
            existing_job=job,
            file_id="src1",
            registry=registry,
            database_name="default",
        )

    assert planner.names == chunk_handler.names == resumed.names == ("investigation", "literary")
