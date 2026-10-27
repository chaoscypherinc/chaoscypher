# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The queue path's running list: each chunk task is shown what the job's completed lower chunks named.

The CLI loop and ``extract_from_chunks`` keep one ``KnownEntities`` per source
in memory. The product path runs chunk tasks concurrently, so each task builds
its list at its start from the job's ``completed`` chunk-task rows with a lower
``chunk_index`` (spec 2026-10-11, "The three paths"), through the same
``add_group``, and never waits: a lower chunk still running is simply not in
the list, and the task's ``known_entities`` metric says how many groups it saw.

Driven through the real ``_extract_chunk_handler`` and a file-backed
``SqliteAdapter`` (so the accessor it calls is the real one), with the
extractor faked at its boundary.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.operations.extraction.chunk_extraction_service import (
    ChunkExtractionOperationsService,
)
from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
    KNOWN_ENTITIES_HEADER,
    KnownEntities,
)
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.testing.embedding import FakeEmbeddingProvider


_SOURCE = "src_known_queue"
_JOB = "job_known_queue"


@pytest.fixture
def sqlite_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """File-backed adapter (CC040 forbids ``:memory:``)."""
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    try:
        yield adapter
    finally:
        adapter.disconnect()


def _person(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "type": "Person",
        "aliases": [],
        "description": "x",
        "sent_ref": "S1",
        "confidence": 1.0,
        "properties": {},
    }


def _seed_job(adapter: SqliteAdapter, **snapshot: Any) -> None:
    """Chunk 0 completed (Pierre), 1 running, 2 completed (Natásha), 3 pending.

    ``snapshot`` adds keys to the job's ``extraction_config`` (the settings
    snapshot the importer pins at job creation).
    """
    adapter.create_source(
        {
            "id": _SOURCE,
            "database_name": "default",
            "filename": f"{_SOURCE}.txt",
            "filepath": f"/tmp/{_SOURCE}.txt",
            "file_type": "txt",
            "file_size": 1,
            "content_hash": f"hash-{_SOURCE}",
            "status": "extracting",
        }
    )
    adapter.create_extraction_job(job_id=_JOB, source_id=_SOURCE, database_name="default")
    adapter.update_extraction_job(
        _JOB, {"extraction_config": json.dumps({"node_templates_formatted": "", **snapshot})}
    )
    adapter.update_extraction_job(_JOB, {"status": "in_progress"})
    for index in range(4):
        adapter.create_chunk(
            {
                "id": f"small_{index}",
                "database_name": "default",
                "source_id": _SOURCE,
                "chunk_index": index,
                "content": "Pierre met Natásha at the ball. " * 10,
            }
        )
        adapter.create_chunk_task(
            task_id=f"task_{index}",
            job_id=_JOB,
            database_name="default",
            chunk_index=index,
            small_chunk_ids=[f"small_{index}"],
        )
    adapter.start_chunk_task("task_0")
    adapter.complete_chunk_task(
        "task_0", raw_entities=[_person("Pierre Bezukhov")], raw_relationships=[]
    )
    adapter.start_chunk_task("task_1")
    adapter.start_chunk_task("task_2")
    adapter.complete_chunk_task(
        "task_2", raw_entities=[_person("Natásha Rostova")], raw_relationships=[]
    )


async def _run_chunk(
    adapter: SqliteAdapter, chunk_index: int
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Run the handler on one chunk task: (its result, the extractor kwargs, the metrics dict)."""
    service = ChunkExtractionOperationsService(source_repository=adapter)
    metrics: dict[str, Any] = {
        "raw_llm_response": "",
        "input_tokens": 10,
        "output_tokens": 20,
        "entity_count": 1,
        "relationship_count": 0,
        "parser_lines_parsed": 1,
        "invalid_relationship_count": 0,
        "evidence_stats": {},
        "sentences": [],
        "filtering_log": None,
        "_prompt_data": {},
        "finish_reason": "stop",
        "aborted_by_loop": False,
    }
    extractor = AsyncMock(return_value=([_person("Prince Andrew")], [], 10, 20, metrics))
    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.AIEntityExtractor.extract_single_chunk",
            new=extractor,
        ),
        patch(
            "chaoscypher_core.queue.queue_client.track_tokens",
            new=AsyncMock(return_value=None),
        ),
        patch.object(service, "queue_finalize_extraction", new=AsyncMock(return_value="qid")),
        # the chunk's entities are embedded for finalize; the default provider is the local model
        patch(
            "chaoscypher_core.repo_factories.get_embedding_service",
            return_value=FakeEmbeddingProvider(),
        ),
    ):
        result = await service._extract_chunk_handler(
            data={
                "chunk_task_id": f"task_{chunk_index}",
                "job_id": _JOB,
                "database_name": "default",
                "chunk_index": chunk_index,
                "small_chunk_ids": [f"small_{chunk_index}"],
            }
        )
    extractor.assert_awaited_once()
    return result, extractor.await_args.kwargs, metrics


@pytest.mark.asyncio
async def test_queue_task_builds_the_list_from_completed_lower_chunks_only(
    sqlite_adapter: SqliteAdapter,
) -> None:
    """Chunk 3 sees chunks 0 and 2 (1 is still running); chunk 1 sees chunk 0 alone."""
    _seed_job(sqlite_adapter)

    result, kwargs, metrics = await _run_chunk(sqlite_adapter, 3)
    assert result["success"] is True
    known = kwargs["known"]
    assert isinstance(known, KnownEntities)
    assert known.block() == (
        f"{KNOWN_ENTITIES_HEADER}\n- Pierre Bezukhov (Person)\n- Natásha Rostova (Person)"
    )
    # The task's own output is not folded in: the list is what came before it.
    assert "Prince Andrew" not in known.block()
    # The task's metrics carry the list's size, in the shape the CLI loop reports.
    assert metrics["known_entities"] == {"groups": 2, "entities": 2}

    result, kwargs, metrics = await _run_chunk(sqlite_adapter, 1)
    assert result["success"] is True
    assert kwargs["known"].block() == f"{KNOWN_ENTITIES_HEADER}\n- Pierre Bezukhov (Person)"
    assert metrics["known_entities"] == {"groups": 1, "entities": 1}


@pytest.mark.asyncio
async def test_the_first_chunk_task_is_handed_an_empty_list(
    sqlite_adapter: SqliteAdapter,
) -> None:
    """Chunk 0 has no lower chunk: an empty list (no block), zero groups."""
    _seed_job(sqlite_adapter)
    # Chunk 0 is re-run (a redelivery after a rerun reset): nothing is below it.
    sqlite_adapter.update_chunk_task("task_0", {"status": "pending"})

    result, kwargs, metrics = await _run_chunk(sqlite_adapter, 0)
    assert result["success"] is True
    assert kwargs["known"].block() == ""
    assert metrics["known_entities"] == {"groups": 0, "entities": 0}


@pytest.mark.asyncio
async def test_the_job_snapshot_limit_wins_over_the_live_setting(
    sqlite_adapter: SqliteAdapter,
) -> None:
    """``known_entities_limit`` pinned on the job bounds every task's block, whatever the live value."""
    assert EngineSettings().extraction.known_entities_limit > 1  # the live default
    _seed_job(sqlite_adapter, known_entities_limit=1)

    _result, kwargs, metrics = await _run_chunk(sqlite_adapter, 3)
    assert kwargs["known"].block() == f"{KNOWN_ENTITIES_HEADER}\n- Pierre Bezukhov (Person)"
    assert metrics["known_entities"] == {"groups": 2, "entities": 2}


@pytest.mark.asyncio
async def test_a_zero_limit_skips_the_row_read(sqlite_adapter: SqliteAdapter) -> None:
    """At limit 0 the list is off: no lower row's JSON is loaded, the block is empty."""
    _seed_job(sqlite_adapter, known_entities_limit=0)

    with patch.object(
        sqlite_adapter,
        "get_completed_chunk_entities",
        wraps=sqlite_adapter.get_completed_chunk_entities,
    ) as read:
        result, kwargs, metrics = await _run_chunk(sqlite_adapter, 3)
    assert result["success"] is True
    read.assert_not_called()
    assert kwargs["known"].block() == ""
    assert metrics["known_entities"] == {"groups": 0, "entities": 0}
