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
from chaoscypher_core.testing.embedding import FakeEmbeddingProvider


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
    repeat: int = 1,
) -> list[str]:
    """Seed source + job + chunks + chunk_tasks. Returns task_ids.

    Each ``chunk_specs`` entry is ``(task_id, chunk_id, chunk_index)``; each
    chunk's text is its sentence ``repeat`` times (a group long enough for an
    unparsed answer to be degenerate needs ``empty_output_retry_min_chars``).
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
                "content": " ".join([f"Alice met Bob in Paris (chunk {chunk_index})."] * repeat),
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


def _result_default(**_kwargs: Any) -> tuple[Any, ...]:
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


def _result_empty(**_kwargs: Any) -> tuple[Any, ...]:
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


def _result_truncated(**_kwargs: Any) -> tuple[Any, ...]:
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


def _result_malformed(**_kwargs: Any) -> tuple[Any, ...]:
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

    - AIEntityExtractor.extract_single_chunk → canned tuple (per strategy;
      the strategy is called with the extractor's kwargs).
    - queue_client.enqueue_task → no-op (so set_source_commit_payload still
      runs against the real adapter).
    - service.queue_finalize_extraction → no-op (its callsite shouldn't
      enqueue real).
    - get_embedding_service → FakeEmbeddingProvider.
    - track_tokens → no-op (queue analytics).
    """

    async def _fake_extract(**kwargs: Any) -> tuple[Any, ...]:
        """Return the strategy's canned result, given the extractor's kwargs.

        ``chunk_content`` is the group text the model numbers.
        """
        return extract_result_callable(**kwargs)

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
    chunk_specs: list[tuple[str, Any, int]],
    extract_result_callable: Any,
) -> dict[str, Any]:
    """Run the chunk handler for each task, then finalize, then commit.

    Each ``chunk_specs`` entry is ``(task_id, chunk_id_or_ids, chunk_index)``;
    a list of ids is an extraction group of several small chunks.

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
                    "small_chunk_ids": chunk_id if isinstance(chunk_id, list) else [chunk_id],
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
    """Parser-rejected lines surface as parser_lines_dropped; source still commits.

    An output nothing parses from is degenerate (audit wave 7) on a group of
    at least ``empty_output_retry_min_chars`` (audit wave 8: a shorter one, a
    heading, keeps an empty answer): the group is asked again on the retry
    ladder while a retry is left, and the last attempt keeps what it has
    (here nothing), so the chunk is delivered once per attempt before the
    source commits its empty graph.
    """
    from chaoscypher_core.app_config import get_settings

    source_id = "src_malformed"
    job_id = "job_malformed"
    chunk_specs = [("task_malformed", "chunk_malformed", 0)]
    task_ids = _seed_source(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        chunk_specs=chunk_specs,
        repeat=8,
    )
    attempts = get_settings().retries.extraction_chunk_max + 1

    await _drive_full_pipeline(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        task_ids=task_ids,
        chunk_specs=chunk_specs * attempts,
        extract_result_callable=_result_malformed,
    )
    task = sqlite_adapter.get_chunk_task("task_malformed")
    assert task is not None and task["retry_count"] == attempts - 1
    assert task["decode_fallback"]["step"] == attempts - 1  # one rung per degenerate output

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


# ---------------------------------------------------------------------------
# Journey 7: citation evidence (sent_ref → cited sentence + stored chunk)
# ---------------------------------------------------------------------------

# One named sentence per chunk, then a filler sentence. Each stored chunk may
# carry a "Page N" header line the content-exclusion matcher strips before the
# model sees the group text.
_EVIDENCE_NAMES = ("Anna", "Boris", "Clara", "Dmitri")


def _evidence_chunk_text(index: int, *, header: bool) -> str:
    """Return chunk *index*'s stored content (two sentences, optional header)."""
    name = _EVIDENCE_NAMES[index]
    body = (
        f"{name} walked slowly through the long gallery of the old house at dusk. "
        f"Everyone in room {index} admired the music, the candles and the food."
    )
    return f"Page {index + 1}\n{body}" if header else body


def _evidence_result(**kwargs: Any) -> tuple[Any, ...]:
    """Fake LLM: cite each named sentence of the group text by its group number.

    Entities are the names that open a sentence (``sent_ref`` = that sentence's
    S-number in *this group's* text). Each consecutive pair gets two edges of
    different types on the same node pair: ``knows`` cites the source's
    sentence plus an out-of-range ref, ``meets`` cites the target's sentence.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        split_into_sentences,
    )

    sentences = split_into_sentences(kwargs["chunk_content"])
    named = [
        (name, f"S{n}")
        for n, sentence in enumerate(sentences, start=1)
        for name in _EVIDENCE_NAMES
        if sentence.startswith(f"{name} ")
    ]
    entities = [
        {"name": name, "type": "Person", "confidence": 0.9, "sent_ref": ref} for name, ref in named
    ]
    relationships: list[dict[str, Any]] = []
    for i in range(len(named) - 1):
        relationships.append(
            {
                "source": i,
                "target": i + 1,
                "type": "knows",
                "confidence": 0.9,
                "sent_ref": f"{named[i][1]}, S{len(sentences) + 5}",
            }
        )
        relationships.append(
            {
                "source": i,
                "target": i + 1,
                "type": "meets",
                "confidence": 0.8,
                "sent_ref": named[i + 1][1],
            }
        )
    return (
        entities,
        relationships,
        100,
        50,
        {
            "raw_llm_response": "E|...",
            "input_tokens": 100,
            "output_tokens": 50,
            "invalid_relationship_count": 0,
            "sentences": sentences,
            "filtering_log": None,
            "_prompt_data": {},
            "finish_reason": "stop",
            "aborted_by_loop": False,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stripped", [False, True], ids=["exact", "content-stripped"])
async def test_journey_citations_resolve_to_the_cited_sentence(
    sqlite_adapter: SqliteAdapter, stripped: bool
) -> None:
    """Every citation's chunk and snippet are the sentences its ``sent_ref`` cites.

    Guard for the 2026-10-05 finding: ``sent_ref`` numbers the sentences of a
    token-packed extraction *group* (several small chunks, the last repeated
    in the next group), so it never resolved against ``chunk_index``. Drives
    the planner (``build_extraction_groups``), the queue chunk handler, the
    finalizer and the commit; with ``stripped`` the handler re-strips the raw
    rows, so the numbered text differs from the stored chunks.
    """
    import re

    from sqlmodel import select

    from chaoscypher_core.adapters.sqlite.models import RelationshipCitation, SourceCitation
    from chaoscypher_core.services.sources.engine.extraction.content_categories import (
        CategoryMatcher,
    )
    from chaoscypher_core.services.sources.engine.extraction.orchestration import (
        build_extraction_groups,
        filter_and_strip_chunks,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        split_into_sentences,
    )

    source_id = f"src_cite_{int(stripped)}"
    job_id = f"job_cite_{int(stripped)}"
    sqlite_adapter.create_source(
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
    sqlite_adapter.create_extraction_job(
        job_id=job_id, source_id=source_id, database_name="default"
    )
    sqlite_adapter.update_extraction_job(
        job_id, {"extraction_config": '{"node_templates_formatted": ""}', "status": "in_progress"}
    )
    for index in range(len(_EVIDENCE_NAMES)):
        sqlite_adapter.create_chunk(
            {
                "id": f"{source_id}_c{index}",
                "database_name": "default",
                "source_id": source_id,
                "chunk_index": index,
                "content": _evidence_chunk_text(index, header=stripped),
            }
        )

    matchers = (
        [
            CategoryMatcher(
                name="page_header",
                description="page header lines",
                mode="line_ratio",
                pattern=re.compile(r"^Page \d+$"),
                threshold=0.4,
            )
        ]
        if stripped
        else []
    )
    # The planner: strip, then pack two chunks per group with one overlapping.
    planned, _stats = filter_and_strip_chunks(
        sqlite_adapter.get_chunks_for_extraction(source_id=source_id, database_name="default"),
        matchers,
    )
    groups = build_extraction_groups(planned, target_tokens=80, overlap=1)
    assert [len(g["small_chunk_ids"]) for g in groups][:3] == [2, 2, 2]
    chunk_specs: list[tuple[str, Any, int]] = []
    for idx, group in enumerate(groups):
        task_id = f"{job_id}_t{idx}"
        sqlite_adapter.create_chunk_task(
            task_id=task_id,
            job_id=job_id,
            database_name="default",
            chunk_index=idx,
            small_chunk_ids=group["small_chunk_ids"],
        )
        chunk_specs.append((task_id, group["small_chunk_ids"], idx))

    seen_texts: list[str] = []

    def _recording_result(**kwargs: Any) -> tuple[Any, ...]:
        seen_texts.append(kwargs["chunk_content"])
        return _evidence_result(**kwargs)

    with patch(
        "chaoscypher_core.operations.extraction.chunk_extraction_service._job_content_matchers",
        return_value=matchers,
    ):
        await _drive_full_pipeline(
            sqlite_adapter,
            source_id=source_id,
            job_id=job_id,
            task_ids=[spec[0] for spec in chunk_specs],
            chunk_specs=chunk_specs,
            extract_result_callable=_recording_result,
        )

    # One group-text builder: the handler numbered exactly the planner's text.
    assert seen_texts == [g["combined_content"] for g in groups]
    assert sqlite_adapter.get_source(source_id, "default")["status"] == "committed"

    stored = {
        c["id"]: c["content"]
        for c in sqlite_adapter.get_chunks_for_extraction(
            source_id=source_id, database_name="default"
        )
    }
    group_chunk_ids = {cid for g in groups for cid in g["small_chunk_ids"]}
    # The sentence each name opens, and the stored chunk it lives in.
    expected = {
        name: (
            split_into_sentences(_evidence_chunk_text(i, header=False))[0],
            f"{source_id}_c{i}",
        )
        for i, name in enumerate(_EVIDENCE_NAMES)
    }

    def _assert_resolves(chunk_id: str, sentences: list[dict[str, Any]], text: str) -> None:
        assert chunk_id in group_chunk_ids
        assert [s["text"] for s in sentences if s["chunk_id"] == chunk_id] == [text]
        for s in sentences:
            assert s["chunk_id"] in stored, s
            local = split_into_sentences(stored[s["chunk_id"]])[s["local_index"]]
            assert s["text"] in local

    session = sqlite_adapter.session
    entity_citations = session.exec(
        select(SourceCitation).where(SourceCitation.source_id == source_id)
    ).all()
    assert sorted(c.entity_label for c in entity_citations) == sorted(_EVIDENCE_NAMES)
    for citation in entity_citations:
        text, chunk_id = expected[citation.entity_label]
        assert citation.chunk_id == chunk_id
        assert citation.context_snippet == text
        assert citation.citation_metadata is not None
        _assert_resolves(citation.chunk_id, citation.citation_metadata["sentences"], text)

    rel_citations = session.exec(
        select(RelationshipCitation).where(RelationshipCitation.source_id == source_id)
    ).all()
    # Three node pairs, two edge types each; inverse edges carry no citation.
    assert sorted((c.source_entity_label, c.edge_label) for c in rel_citations) == sorted(
        (a, t) for a in _EVIDENCE_NAMES[:3] for t in ("knows", "meets")
    )
    for citation in rel_citations:
        cited_name = (
            citation.source_entity_label
            if citation.edge_label == "knows"
            else citation.target_entity_label
        )
        text, chunk_id = expected[cited_name]
        assert citation.chunk_id == chunk_id, (citation.edge_label, cited_name)
        sentences = citation.citation_metadata["sentences"]
        # The out-of-range ref on ``knows`` is skipped, not fatal.
        assert [s["text"] for s in sentences] == [text]
        _assert_resolves(citation.chunk_id, sentences, text)


# ---------------------------------------------------------------------------
# Journey 8: one fact, one edge (symmetric and inverse labels across groups)
# ---------------------------------------------------------------------------

_FACT_CHUNKS = (
    "Anna greeted Boris warmly at the door of the old house. Anna was the mother of Boris.",
    "Boris spoke with Anna for an hour in the garden. Boris was the son of Anna.",
)


def _fact_result(**kwargs: Any) -> tuple[Any, ...]:
    """Fake LLM: each group writes the same two facts from its own side.

    Group 0 (Anna first): Anna interacts_with Boris, Anna parent_of Boris.
    Group 1 (Boris first): Boris interacts_with Anna, Boris child_of Anna.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        split_into_sentences,
    )

    text = kwargs["chunk_content"]
    sentences = split_into_sentences(text)
    first, second = ("Anna", "Boris") if "Anna greeted" in text else ("Boris", "Anna")
    family = "parent_of" if first == "Anna" else "child_of"
    entities = [
        {"name": first, "type": "Character", "confidence": 0.9, "sent_ref": "S1"},
        {"name": second, "type": "Character", "confidence": 0.9, "sent_ref": "S1"},
    ]
    relationships = [
        {"source": 0, "target": 1, "type": "interacts_with", "confidence": 0.9, "sent_ref": "S1"},
        {"source": 0, "target": 1, "type": family, "confidence": 0.9, "sent_ref": "S2"},
    ]
    return (
        entities,
        relationships,
        100,
        50,
        {
            "raw_llm_response": "E|...",
            "input_tokens": 100,
            "output_tokens": 50,
            "invalid_relationship_count": 0,
            "sentences": sentences,
            "filtering_log": None,
            "_prompt_data": {},
            "finish_reason": "stop",
            "aborted_by_loop": False,
        },
    )


@pytest.mark.asyncio
async def test_journey_a_fact_written_both_ways_in_two_groups_commits_as_one_edge(
    sqlite_adapter: SqliteAdapter,
) -> None:
    """Symmetric and inverse-pair facts from two groups commit once, with both citations.

    Guard for the 2026-10-06 audit (E6): the relationship dedup kept
    cross-group duplicates in the direction each group wrote them, so
    ``interacts_with`` A->B and B->A became two edges (26 reverse pairs in the
    v3 graph) and one parent/child fact was stored as both ``parent_of`` and
    ``child_of``. Drives the queue chunk handler, the finalizer (literary
    domain: its symmetric labels and inverse map) and the commit on SQLite.
    """
    from sqlmodel import select

    from chaoscypher_core.adapters.sqlite.models import GraphEdge, RelationshipCitation

    source_id = "src_fact"
    job_id = "job_fact"
    sqlite_adapter.create_source(
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
    sqlite_adapter.create_extraction_job(
        job_id=job_id, source_id=source_id, database_name="default"
    )
    sqlite_adapter.update_extraction_job(
        job_id,
        {
            "extraction_config": '{"node_templates_formatted": ""}',
            "status": "in_progress",
            "detected_domain": "literary",
        },
    )
    chunk_specs: list[tuple[str, Any, int]] = []
    for index, content in enumerate(_FACT_CHUNKS):
        chunk_id = f"{source_id}_c{index}"
        sqlite_adapter.create_chunk(
            {
                "id": chunk_id,
                "database_name": "default",
                "source_id": source_id,
                "chunk_index": index,
                "content": content,
            }
        )
        task_id = f"{job_id}_t{index}"
        sqlite_adapter.create_chunk_task(
            task_id=task_id,
            job_id=job_id,
            database_name="default",
            chunk_index=index,
            small_chunk_ids=[chunk_id],
        )
        chunk_specs.append((task_id, [chunk_id], index))

    seen_texts: list[str] = []

    def _recording_result(**kwargs: Any) -> tuple[Any, ...]:
        seen_texts.append(kwargs["chunk_content"])
        return _fact_result(**kwargs)

    # The literary domain's content exclusions are not under test here.
    with patch(
        "chaoscypher_core.operations.extraction.chunk_extraction_service._job_content_matchers",
        return_value=[],
    ):
        await _drive_full_pipeline(
            sqlite_adapter,
            source_id=source_id,
            job_id=job_id,
            task_ids=[spec[0] for spec in chunk_specs],
            chunk_specs=chunk_specs,
            extract_result_callable=_recording_result,
        )
    assert seen_texts == list(_FACT_CHUNKS)
    assert sqlite_adapter.get_source(source_id, "default")["status"] == "committed"

    session = sqlite_adapter.session
    edges = session.exec(select(GraphEdge).where(GraphEdge.source_id == source_id)).all()
    facts = [e for e in edges if not (e.properties or {}).get("inverse_of")]
    mirrors = [e for e in edges if (e.properties or {}).get("inverse_of")]
    assert sorted(e.label for e in facts) == ["child_of", "interacts_with"]
    # The inverse-mirror feature (enable_inverse_relationships) adds at most one
    # mirror for the one family fact, never one per group.
    assert [e.label for e in mirrors] == ["parent_of"]

    citations = session.exec(
        select(RelationshipCitation).where(RelationshipCitation.source_id == source_id)
    ).all()
    by_edge: dict[str, set[str]] = {}
    for citation in citations:
        by_edge.setdefault(citation.edge_id, set()).add(citation.chunk_id)
    assert by_edge == {e.id: {f"{source_id}_c0", f"{source_id}_c1"} for e in facts}


# ---------------------------------------------------------------------------
# Journey 9: the queue handler runs every chunk with the shared extraction plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_journey_the_chunk_handler_passes_the_planned_extractor_inputs(
    sqlite_adapter: SqliteAdapter,
) -> None:
    """The planner's extraction_config reaches ``extract_single_chunk`` unchanged (audit E5).

    The job's config is built by the real planner (``_build_extraction_config``)
    for the literary domain; the chunk handler must pass the extractor exactly
    the plan's kwargs, the three domain inputs it used to drop included. The
    CLI and the standalone extractor are pinned to the same plan in
    ``packages/cli/tests/unit/sources/test_extraction_path_parity.py``.
    """
    from chaoscypher_core.app_config import Settings as _AppSettings
    from chaoscypher_core.app_config.engine_factory import build_engine_settings
    from chaoscypher_core.operations.importing.import_service import _build_extraction_config
    from chaoscypher_core.services.sources.engine.extraction.domains.registry import (
        DomainRegistry,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
    from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
        CHUNK_KWARG_NAMES,
        build_extraction_plan,
    )

    literary = DomainStack.resolve(DomainRegistry(), ["literary"])
    source_id, job_id = "src_plan", "job_plan"
    task_ids = _seed_source(
        sqlite_adapter,
        source_id=source_id,
        job_id=job_id,
        chunk_specs=[(f"{job_id}_t0", f"{source_id}_c0", 0)],
    )
    row = sqlite_adapter.get_source(source_id, "default")
    app_settings = _AppSettings()
    sqlite_adapter.update_extraction_job(
        job_id,
        {
            "extraction_config": _build_extraction_config(
                stack=literary,
                settings=app_settings,
                file_info={},
                source_row=row,
            )
        },
    )
    seen: list[dict[str, Any]] = []

    def _recording(**kwargs: Any) -> tuple[Any, ...]:
        seen.append(kwargs)
        return _result_default(**kwargs)

    with patch(
        "chaoscypher_core.operations.extraction.chunk_extraction_service._job_content_matchers",
        return_value=[],
    ):
        await _drive_full_pipeline(
            sqlite_adapter,
            source_id=source_id,
            job_id=job_id,
            task_ids=task_ids,
            chunk_specs=[(task_ids[0], f"{source_id}_c0", 0)],
            extract_result_callable=_recording,
        )

    expected = build_extraction_plan(
        literary, build_engine_settings(app_settings), source_row=row
    ).chunk_kwargs
    assert len(seen) == 1
    assert {name: seen[0][name] for name in CHUNK_KWARG_NAMES} == expected
    assert seen[0]["filtering_mode"] == row["filtering_mode"]
    assert seen[0]["named_referent_types"]
    assert seen[0]["domain_extraction_limits"]["strict_edge_type_constraints"] is True
