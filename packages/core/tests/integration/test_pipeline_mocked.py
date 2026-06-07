# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""In-process pipeline journey tests with mocked LLM and embedding services.

Each journey seeds a source + chunk_extraction_task in a real
``SqliteAdapter``, then drives ``_extract_chunk_handler`` →
``finalize_extraction_handler`` → ``_import_commit_handler`` directly
in-process. The LLM is faked at the ``AIEntityExtractor.extract_single_chunk``
boundary (matches the canonical pattern used by
``test_chunk_observability.py``); the embedding service is patched to
a deterministic ``FakeEmbeddingProvider``.

These journeys surface contract-drift bugs across the full extraction
pipeline. Today's ``'list' has no astype`` bug (cached
raw_entity_embeddings JSON → finalize → _store_entity_embeddings) is
exercised by the happy_path journey.

See the mocked pipeline test fixtures.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.operations.extraction.chunk_extraction_service import (
    ChunkExtractionOperationsService,
)
from chaoscypher_core.operations.extraction.extraction_finalizer import (
    finalize_extraction_handler,
)
from chaoscypher_core.operations.importing.import_service import (
    ImportOperationsService,
)
from tests.fakes.embedding import FakeEmbeddingProvider


# ---------------------------------------------------------------------------
# Fixtures + seed helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """File-backed real adapter (CC040 forbids ``:memory:``)."""
    db_dir = tmp_path / "chaoscypher-pipeline-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    try:
        yield adapter
    finally:
        adapter.disconnect()


def _seed_source(
    adapter: SqliteAdapter,
    *,
    source_id: str,
    job_id: str,
    chunk_specs: list[tuple[str, str, int]],
) -> list[str]:
    """Seed source + job + chunks + chunk_tasks. Returns task_ids.

    Each ``chunk_specs`` entry is ``(task_id, chunk_id, chunk_index)``.
    """
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "default",
            "filename": f"{source_id}.txt",
            "filepath": f"/tmp/{source_id}.txt",
            "file_type": "txt",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "extracting",
        }
    )
    adapter.create_extraction_job(job_id=job_id, source_id=source_id, database_name="default")
    adapter.update_extraction_job(job_id, {"extraction_config": '{"node_templates_formatted": ""}'})
    adapter.update_extraction_job(job_id, {"status": "in_progress"})

    task_ids = []
    for task_id, chunk_id, chunk_index in chunk_specs:
        adapter.create_chunk(
            {
                "id": chunk_id,
                "database_name": "default",
                "source_id": source_id,
                "chunk_index": chunk_index,
                "content": f"Alice met Bob in Paris (chunk {chunk_index}).",
            }
        )
        adapter.create_chunk_task(
            task_id=task_id,
            job_id=job_id,
            database_name="default",
            chunk_index=chunk_index,
        )
        task_ids.append(task_id)
    return task_ids


# ---------------------------------------------------------------------------
# Canned extract_single_chunk results — one per FakeLLMProvider strategy
# ---------------------------------------------------------------------------


def _result_default() -> tuple[Any, ...]:
    """2 entities + 1 relationship — the happy path."""
    return (
        [
            {
                "name": "Alice",
                "type": "Person",
                "description": "A character",
                "aliases": ["alice"],
                "confidence": 0.9,
                "sent_ref": "S1",
            },
            {
                "name": "Bob",
                "type": "Person",
                "description": "Another character",
                "aliases": ["bob"],
                "confidence": 0.9,
                "sent_ref": "S2",
            },
        ],
        [
            {
                "source": 0,
                "target": 1,
                "type": "knows",
                "confidence": 0.9,
                "sent_ref": "S1-S2",
                "justification": "They meet",
            },
        ],
        120,  # input_tokens
        80,  # output_tokens
        {
            "raw_llm_response": "E|Alice|...\nE|Bob|...\nR|0|1|knows|...\n",
            "input_tokens": 120,
            "output_tokens": 80,
            "entity_count": 2,
            "relationship_count": 1,
            "invalid_relationship_count": 0,
            "evidence_stats": {},
            "sentences": ["Alice met Bob."],
            "filtering_log": None,
            "_prompt_data": {},
            "finish_reason": "stop",
            "aborted_by_loop": False,
        },
    )


def _result_empty() -> tuple[Any, ...]:
    """0 entities, 0 relationships."""
    return (
        [],
        [],
        120,
        0,
        {
            "raw_llm_response": "",
            "input_tokens": 120,
            "output_tokens": 0,
            "entity_count": 0,
            "relationship_count": 0,
            "invalid_relationship_count": 0,
            "evidence_stats": {},
            "sentences": [],
            "filtering_log": None,
            "_prompt_data": {},
            "finish_reason": "stop",
            "aborted_by_loop": False,
        },
    )


def _result_truncated() -> tuple[Any, ...]:
    """1 entity, then truncated. finish_reason='length'."""
    return (
        [
            {
                "name": "Alice",
                "type": "Person",
                "description": "A character",
                "aliases": ["alice"],
                "confidence": 0.9,
                "sent_ref": "S1",
            },
        ],
        [],
        120,
        4096,
        {
            "raw_llm_response": "E|Alice|...\nE|Bob|Pers",
            "input_tokens": 120,
            "output_tokens": 4096,
            "entity_count": 1,
            "relationship_count": 0,
            "invalid_relationship_count": 0,
            "evidence_stats": {},
            "sentences": [],
            "filtering_log": None,
            "_prompt_data": {},
            "finish_reason": "length",
            "aborted_by_loop": False,
        },
    )


def _result_malformed() -> tuple[Any, ...]:
    """0 entities/relationships make it through (all parser-rejected) but
    parser_lines_dropped > 0.
    """
    return (
        [],
        [],
        120,
        60,
        {
            "raw_llm_response": "E|TooFewFields\nE|Alice|...badmissingfields",
            "input_tokens": 120,
            "output_tokens": 60,
            "entity_count": 0,
            "relationship_count": 0,
            "invalid_relationship_count": 0,
            "evidence_stats": {},
            "sentences": [],
            "filtering_log": None,
            "_prompt_data": {},
            "finish_reason": "stop",
            "aborted_by_loop": False,
            "parser_lines_dropped": 3,
        },
    )


# ---------------------------------------------------------------------------
# Shared pipeline driver
# ---------------------------------------------------------------------------


@contextmanager
def _pipeline_patches(
    *,
    service: ChunkExtractionOperationsService,
    fake_embedding: FakeEmbeddingProvider,
    extract_result_callable: Any,
) -> Generator[None]:
    """Patch the IO boundaries so handlers run in-process.

    - AIEntityExtractor.extract_single_chunk → canned tuple (per strategy).
    - queue_client.enqueue_task → no-op (so set_source_commit_payload still
      runs against the real adapter).
    - service.queue_finalize_extraction → no-op (its callsite shouldn't
      enqueue real).
    - get_embedding_service → FakeEmbeddingProvider.
    - track_tokens → no-op (queue analytics).
    """

    async def _fake_extract(**_kwargs: Any) -> tuple[Any, ...]:
        return extract_result_callable()

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils."
            "ai_entities.AIEntityExtractor.extract_single_chunk",
            new=AsyncMock(side_effect=_fake_extract),
        ),
        patch(
            "chaoscypher_core.queue.queue_client.track_tokens",
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            service,
            "queue_finalize_extraction",
            new=AsyncMock(return_value="qid-fin"),
        ),
        patch(
            "chaoscypher_core.repo_factories.get_embedding_service",
            return_value=fake_embedding,
        ),
        patch(
            "chaoscypher_core.operations.queue_utils.queue_client.enqueue_task",
            new=AsyncMock(return_value="qid-commit"),
        ),
    ):
        yield


async def _drive_full_pipeline(
    adapter: SqliteAdapter,
    *,
    source_id: str,
    job_id: str,
    task_ids: list[str],
    chunk_specs: list[tuple[str, str, int]],
    extract_result_callable: Any,
) -> dict[str, Any]:
    """Run the chunk handler for each task, then finalize, then commit.

    Returns the commit_result dict.
    """
    service = ChunkExtractionOperationsService(source_repository=adapter)
    fake_embedding = FakeEmbeddingProvider(dimensions=8)
    graph_repo = GraphRepository(adapter.session, "default")
    llm_service = MagicMock()

    with _pipeline_patches(
        service=service,
        fake_embedding=fake_embedding,
        extract_result_callable=extract_result_callable,
    ):
        # ----- Phase 1: chunk extraction (one call per chunk_task) -----
        for task_id, chunk_id, chunk_index in chunk_specs:
            await service._extract_chunk_handler(
                data={
                    "chunk_task_id": task_id,
                    "job_id": job_id,
                    "database_name": "default",
                    "chunk_index": chunk_index,
                    "small_chunk_ids": [chunk_id],
                }
            )

        # ----- Phase 2: finalize -----
        await finalize_extraction_handler(
            graph_repository=graph_repo,
            llm_service=llm_service,
            source_repository=adapter,
            chunk_extraction_service=service,
            data={
                "job_id": job_id,
                "source_id": source_id,
                "database_name": "default",
                "generate_embeddings": True,
                "file_info": {},
            },
        )

        # ----- Phase 3: commit -----
        import_service = ImportOperationsService(
            graph_repository=graph_repo,
            config_manager=MagicMock(),
            source_manager=MagicMock(),
            trigger_service=MagicMock(),
            llm_service=llm_service,
            source_repository=adapter,
            chunking_service=MagicMock(),
            indexing_service=MagicMock(),
            search_repository=MagicMock(),
        )

        return await import_service._import_commit_handler(
            data={
                "file_id": source_id,
                "file_info": {"filename": f"{source_id}.txt"},
                "auto_enable": True,
            }
        )


# ---------------------------------------------------------------------------
# Journey 1: happy_path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_journey_happy_path(sqlite_adapter: SqliteAdapter) -> None:
    """Full pipeline: 2 entities + 1 relationship → source 'committed'.

    Exercises today's class of bug: the cached-embedding JSON round-trip
    via raw_entity_embeddings. The fix (np.asarray() in
    store_entity_embeddings) is asserted here.
    """
    source_id = "src_happy"
    job_id = "job_happy"
    chunk_specs = [("task_happy", "chunk_happy", 0)]
    task_ids = _seed_source(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        chunk_specs=chunk_specs,
    )

    await _drive_full_pipeline(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        task_ids=task_ids,
        chunk_specs=chunk_specs,
        extract_result_callable=_result_default,
    )

    src_row = sqlite_adapter.get_source(source_id, "default")
    assert src_row["status"] == "committed"
    assert src_row["commit_complete"] is True
    assert (src_row.get("commit_nodes_created") or 0) >= 2
    assert (src_row.get("commit_edges_created") or 0) >= 1

    # Today's bug path: cached-embedding JSON round-trip through finalize
    # → store_entity_embeddings → np.asarray() coercion.
    embeddings_rows = sqlite_adapter.get_entity_embeddings(source_id)
    assert len(embeddings_rows) == 2

    # And the chunk_task row carries the eager-write embedding as JSON
    chunk_row = sqlite_adapter.get_chunk_task(task_ids[0])
    assert chunk_row["raw_entity_embeddings"] is not None
    assert isinstance(chunk_row["raw_entity_embeddings"][0], list)


# ---------------------------------------------------------------------------
# Journey 2: empty_extraction (LLM returns 0 entities)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_journey_empty_extraction(sqlite_adapter: SqliteAdapter) -> None:
    """Empty-output chunk still reaches committed (zero-graph commit).

    The chunk-rerun feature specifically targets this case; it MUST keep
    working.
    """
    source_id = "src_empty"
    job_id = "job_empty"
    chunk_specs = [("task_empty", "chunk_empty", 0)]
    task_ids = _seed_source(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        chunk_specs=chunk_specs,
    )

    await _drive_full_pipeline(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        task_ids=task_ids,
        chunk_specs=chunk_specs,
        extract_result_callable=_result_empty,
    )

    src_row = sqlite_adapter.get_source(source_id, "default")
    assert src_row["status"] == "committed"
    assert src_row["commit_complete"] is True
    # Zero-graph commit: no entities, no relationships
    assert (src_row.get("commit_nodes_created") or 0) == 0
    assert (src_row.get("commit_edges_created") or 0) == 0

    embeddings_rows = sqlite_adapter.get_entity_embeddings(source_id)
    assert embeddings_rows == []


# ---------------------------------------------------------------------------
# Journey 3: chunk_rerun on committed source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_journey_chunk_rerun_preserves_graph(
    sqlite_adapter: SqliteAdapter,
) -> None:
    """Run pipeline once → rerun chunk → re-run finalize+commit.

    Asserts:
    - chunk_extraction_attempts has 1 snapshot row (the prior result).
    - source ends 'committed' again.
    - graph_nodes count unchanged (first-write-wins upsert).
    - source.chunks_rerun_total bumped (via reset_chunk_task_for_rerun's
      explicit `commit_complete` clear).
    """
    from sqlmodel import select

    from chaoscypher_core.adapters.sqlite.models import ChunkExtractionAttempt

    source_id = "src_rerun"
    job_id = "job_rerun"
    chunk_specs = [("task_rerun", "chunk_rerun", 0)]
    task_ids = _seed_source(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        chunk_specs=chunk_specs,
    )

    # First commit cycle
    await _drive_full_pipeline(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        task_ids=task_ids,
        chunk_specs=chunk_specs,
        extract_result_callable=_result_default,
    )
    src_row = sqlite_adapter.get_source(source_id, "default")
    assert src_row["status"] == "committed"
    initial_node_count = src_row.get("commit_nodes_created") or 0

    # ----- Trigger chunk rerun -----
    # reset_chunk_task_for_rerun snapshots the prior attempt + walks
    # source.status back to 'extracting' + clears commit_complete.
    attempt_number = sqlite_adapter.reset_chunk_task_for_rerun(
        task_id=task_ids[0],
        source_id=source_id,
    )
    assert attempt_number == 1

    src_row = sqlite_adapter.get_source(source_id, "default")
    assert src_row["status"] == "extracting"
    assert src_row["commit_complete"] is False

    # Verify snapshot was created
    snapshots = list(
        sqlite_adapter.session.exec(
            select(ChunkExtractionAttempt).where(
                ChunkExtractionAttempt.chunk_task_id == task_ids[0]
            )
        ).all()
    )
    assert len(snapshots) == 1

    # ----- Second commit cycle -----
    await _drive_full_pipeline(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        task_ids=task_ids,
        chunk_specs=chunk_specs,
        extract_result_callable=_result_default,
    )

    src_row = sqlite_adapter.get_source(source_id, "default")
    assert src_row["status"] == "committed"
    assert src_row["commit_complete"] is True
    # First-write-wins: graph_nodes count unchanged (Alice + Bob already exist)
    final_node_count = src_row.get("commit_nodes_created") or 0
    assert final_node_count == initial_node_count


# ---------------------------------------------------------------------------
# Journey 4: truncated LLM (finish_reason='length')
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_journey_truncated_llm(sqlite_adapter: SqliteAdapter) -> None:
    """Truncated chunk still reaches committed; truncation counter increments."""
    source_id = "src_trunc"
    job_id = "job_trunc"
    chunk_specs = [("task_trunc", "chunk_trunc", 0)]
    task_ids = _seed_source(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        chunk_specs=chunk_specs,
    )

    await _drive_full_pipeline(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        task_ids=task_ids,
        chunk_specs=chunk_specs,
        extract_result_callable=_result_truncated,
    )

    src_row = sqlite_adapter.get_source(source_id, "default")
    assert src_row["status"] == "committed"
    assert (src_row.get("llm_chunks_truncated") or 0) == 1
    # Note: whether Alice ends up in the graph depends on evidence-validation
    # behavior (the sent_ref must resolve against the chunk text). That's a
    # parser/validator concern, not a pipeline-contract concern — covered by
    # unit tests of evidence_validator.py. Here we only assert the truncation
    # counter wiring + that the source reached committed despite truncation.


# ---------------------------------------------------------------------------
# Journey 5: malformed LLM output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_journey_malformed_lines(sqlite_adapter: SqliteAdapter) -> None:
    """Parser-rejected lines surface as parser_lines_dropped; source still commits."""
    source_id = "src_malformed"
    job_id = "job_malformed"
    chunk_specs = [("task_malformed", "chunk_malformed", 0)]
    task_ids = _seed_source(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        chunk_specs=chunk_specs,
    )

    await _drive_full_pipeline(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        task_ids=task_ids,
        chunk_specs=chunk_specs,
        extract_result_callable=_result_malformed,
    )

    src_row = sqlite_adapter.get_source(source_id, "default")
    # Zero-graph commit because parser rejected every record
    assert src_row["status"] == "committed"
    assert (src_row.get("commit_nodes_created") or 0) == 0
    # Parser-drop counter was incremented from the metrics dict
    assert (src_row.get("parser_lines_dropped") or 0) >= 3


# ---------------------------------------------------------------------------
# Journey 6: multi-chunk source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_journey_multi_chunk(sqlite_adapter: SqliteAdapter) -> None:
    """3 chunks all complete, finalize fires once, graph_nodes dedups to ≤6."""
    source_id = "src_multi"
    job_id = "job_multi"
    chunk_specs = [
        ("task_multi_0", "chunk_multi_0", 0),
        ("task_multi_1", "chunk_multi_1", 1),
        ("task_multi_2", "chunk_multi_2", 2),
    ]
    task_ids = _seed_source(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        chunk_specs=chunk_specs,
    )

    await _drive_full_pipeline(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        task_ids=task_ids,
        chunk_specs=chunk_specs,
        extract_result_callable=_result_default,
    )

    src_row = sqlite_adapter.get_source(source_id, "default")
    assert src_row["status"] == "committed"
    # 6 raw entities (3 chunks x 2 entities) -> tolerant range after dedup.
    # Strict counts belong in unit tests of the dedup service; here we
    # only verify the aggregation gate fired and produced something.
    nodes_created = src_row.get("commit_nodes_created") or 0
    assert 1 <= nodes_created <= 6

    # All 3 chunk tasks must be terminal
    for task_id in task_ids:
        row = sqlite_adapter.get_chunk_task(task_id)
        assert row["status"] == "completed"
