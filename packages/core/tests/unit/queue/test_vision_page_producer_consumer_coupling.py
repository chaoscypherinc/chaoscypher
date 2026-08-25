# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Producer -> consumer coupling for the OP_VISION_PAGE in-flight guard.

Entry 920 (fix-round follow-up): ``QueueClient.in_flight_vision_page_ids``
filters queued/running ``vision_page`` tasks on ``metadata.source_id`` and
``metadata.database_name``, then reads the page identity back out of
``data.page_id``. The real producer
(``indexing_handler._apply_vision_processing``) originally wrote metadata
without ``database_name`` — the same class of gap A2 fixed for
``extract_chunk`` (``metadata.database_name`` missing means
``task_exists_for_source``/``in_flight_*`` silently never matches). Fixed
alongside the page-scoped filter itself.

``test_client_coverage.py::test_in_flight_vision_page_ids_collects_set``
covers the consumer in isolation with a hand-built metadata dict — that gap
is exactly what let the drift happen for ``extract_chunk``. This test
closes it the same way A2's coupling suite did: drive the REAL producer
against a single in-memory-store-backed ``QueueClient``, then call the REAL
``in_flight_vision_page_ids`` consumer against whatever the producer
actually wrote. A producer that stops writing ``database_name`` — or a
consumer whose key names drift — fails this test immediately instead of
leaving the guard silently inert.
"""

from __future__ import annotations

from itertools import count
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.queue.client import QueueClient


# ---------------------------------------------------------------------------
# Shared in-memory-store-backed QueueClient (mirrors
# test_extract_chunk_producer_consumer_coupling.py's helper exactly).
#
# enqueue()/enqueue_task() write into `store` via a fake pipeline's hset;
# in_flight_vision_page_ids() reads back from the SAME store via fake
# scan_iter/hgetall. A task written by the real producer can therefore be
# read back by the real consumer inside one test, with no re-implemented
# metadata shape on either side of the assertion.
# ---------------------------------------------------------------------------


def _make_store_backed_client() -> tuple[QueueClient, dict[str, dict[str, Any]]]:
    """Build a QueueClient whose enqueue path and scan path share one store."""
    store: dict[str, dict[str, Any]] = {}

    client = QueueClient.__new__(QueueClient)
    client._connected = True
    client._max_pending_queue_depth = 10000
    client._operations_result_ttl = 7200
    client._llm_result_ttl = 3600

    pipeline = MagicMock()

    def _hset(key: str, mapping: dict[str, Any]) -> MagicMock:
        task_id = key.removeprefix("queue:task:")
        store[task_id] = dict(mapping)
        return pipeline

    pipeline.hset.side_effect = _hset
    pipeline.zadd.return_value = pipeline
    pipeline.lpush.return_value = pipeline
    pipeline.ltrim.return_value = pipeline
    pipeline.execute = AsyncMock(return_value=[])

    valkey = MagicMock()
    valkey.zcard = AsyncMock(return_value=0)
    valkey.pipeline = MagicMock(return_value=pipeline)
    valkey.exists = AsyncMock(return_value=0)
    # Pending-ZSET seq counter (queue FIFO tiebreaker, 2026-08-15).
    valkey.incr = AsyncMock(side_effect=lambda _key, _c=count(1): next(_c))
    valkey.incrby = AsyncMock(side_effect=lambda _key, amount: amount)

    def _scan_iter_factory(*_args: Any, **_kwargs: Any) -> Any:
        async def _gen() -> Any:
            for task_id in list(store):
                yield f"queue:task:{task_id}".encode()

        return _gen()

    valkey.scan_iter = _scan_iter_factory

    async def _hgetall(key: Any) -> dict[str, Any]:
        raw = key.decode() if isinstance(key, bytes) else key
        task_id = raw.removeprefix("queue:task:")
        return store.get(task_id, {})

    valkey.hgetall = AsyncMock(side_effect=_hgetall)

    client.client = valkey
    return client, store


def _build_documents_with_pages(n: int) -> list[dict[str, Any]]:
    """Loader-output shape: one doc with N image pages."""
    return [
        {
            "content": "...",
            "metadata": {
                "pages": [{"page_number": i + 1, "has_images": True} for i in range(n)],
            },
        }
    ]


def _build_engine_settings(tmp_path: Path) -> Any:
    """EngineSettings mock with a high fan-out ceiling so it never trips."""
    engine_settings = MagicMock()
    engine_settings.paths.data_dir = str(tmp_path)
    engine_settings.loader.vision_max_pages = 100_000
    return engine_settings


def _build_adapter(page_ids: list[str]) -> Any:
    """Adapter mock whose ``list_vision_page_descriptions`` returns real page rows.

    This is what ``_apply_vision_processing`` iterates to build each
    ``OP_VISION_PAGE`` task's ``data``/``metadata`` — the producer's actual
    per-page identity comes from here, not from the sampled ``image_pages``
    list used only to size ``create_vision_job_with_pages``.
    """
    adapter = MagicMock()
    adapter.create_vision_job_with_pages = MagicMock(return_value="job-vp-coupling")
    adapter.transition_source_status = MagicMock(return_value=True)
    adapter.list_vision_page_descriptions = MagicMock(
        return_value=[{"id": pid, "page_number": i + 1} for i, pid in enumerate(page_ids)]
    )
    adapter.increment_source_counter = MagicMock()
    return adapter


@pytest.mark.asyncio
async def test_apply_vision_processing_producer_satisfies_in_flight_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The real OP_VISION_PAGE producer's metadata matches the consumer predicate.

    ``indexing_handler.py`` imports ``queue_client`` at module scope
    (``from chaoscypher_core.queue import queue_client``), so
    ``_apply_vision_processing`` reads it as a plain module global —
    monkeypatching ``indexing_handler.queue_client`` directly swaps what
    the producer writes through, mirroring how
    ``test_quick_vision_sampling.py`` patches the same name.
    """
    from chaoscypher_core.operations.importing import indexing_handler

    monkeypatch.setattr(indexing_handler, "_get_active_vision_model", lambda s: "fake-vision")

    client, _store = _make_store_backed_client()
    monkeypatch.setattr(indexing_handler, "queue_client", client)

    page_ids = ["page-1", "page-2", "page-3"]
    documents = _build_documents_with_pages(len(page_ids))
    engine_settings = _build_engine_settings(tmp_path)
    adapter = _build_adapter(page_ids)

    _documents_out, job_id = await indexing_handler._apply_vision_processing(
        documents=documents,
        file_id="src-vp-coupling",
        filepath="/tmp/book.pdf",
        enable_vision=True,
        engine_settings=engine_settings,
        database_name="default",
        data_dir=str(tmp_path),
        adapter=adapter,
        analysis_depth="full",
    )

    assert job_id == "job-vp-coupling"

    ids = await client.in_flight_vision_page_ids(
        source_id="src-vp-coupling", database_name="default"
    )
    assert ids == set(page_ids)


@pytest.mark.asyncio
async def test_apply_vision_processing_producer_scoped_to_its_own_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A consumer query for a DIFFERENT database_name must not see these pages.

    Pins the defensive half of the metadata contract (see
    ``QueueClient.task_exists_for_source``'s comment on missing
    ``database_name``): the producer must write the SPECIFIC db it ran
    against, not something that accidentally matches every query.
    """
    from chaoscypher_core.operations.importing import indexing_handler

    monkeypatch.setattr(indexing_handler, "_get_active_vision_model", lambda s: "fake-vision")

    client, _store = _make_store_backed_client()
    monkeypatch.setattr(indexing_handler, "queue_client", client)

    page_ids = ["page-1"]
    documents = _build_documents_with_pages(len(page_ids))
    engine_settings = _build_engine_settings(tmp_path)
    adapter = _build_adapter(page_ids)

    await indexing_handler._apply_vision_processing(
        documents=documents,
        file_id="src-vp-scoped",
        filepath="/tmp/book.pdf",
        enable_vision=True,
        engine_settings=engine_settings,
        database_name="tenant-a",
        data_dir=str(tmp_path),
        adapter=adapter,
        analysis_depth="full",
    )

    same_db_ids = await client.in_flight_vision_page_ids(
        source_id="src-vp-scoped", database_name="tenant-a"
    )
    other_db_ids = await client.in_flight_vision_page_ids(
        source_id="src-vp-scoped", database_name="tenant-b"
    )
    assert same_db_ids == {"page-1"}
    assert other_db_ids == set()
