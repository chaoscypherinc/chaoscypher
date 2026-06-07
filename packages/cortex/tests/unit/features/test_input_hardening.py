# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Input-hardening guards on read/import endpoints (DoS defense).

Covers the launch-readiness round-2 hardening:
- GET /system/processing/events clamps ``limit`` to the pagination ceiling.
- GET /sources/{id}/chunks/batch caps the number of ids.
- POST /exports/import enforces a streamed size cap (no unbounded temp/RAM).

Follows the cortex pattern of calling endpoint functions directly with mocked
dependencies rather than spinning up a TestClient.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import ValidationError


@pytest.mark.asyncio
async def test_list_system_events_clamps_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A huge ?limit is clamped to pagination.max_page_size before hitting storage."""
    from chaoscypher_cortex.features.pause import api as pause_api

    fake_adapter = MagicMock()
    fake_adapter.list_system_events.return_value = []
    monkeypatch.setattr(
        "chaoscypher_core.database.adapter_factory.get_sqlite_adapter",
        lambda **_: fake_adapter,
    )

    await pause_api.list_system_events(_="user", event_type=None, limit=10_000_000)

    cap = pause_api.get_settings().pagination.max_page_size
    assert fake_adapter.list_system_events.call_args.kwargs["limit"] == cap


@pytest.mark.asyncio
async def test_get_chunks_batch_caps_id_count() -> None:
    """An oversized comma-separated id list is truncated to the pagination ceiling."""
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_cortex.features.sources.chunks_api import get_chunks_batch

    cap = get_settings().pagination.max_page_size
    service = MagicMock()
    service.get_source.return_value = {"id": "s-1"}
    service.get_chunks_by_ids.return_value = []

    ids = ",".join(f"c{i}" for i in range(cap + 50))
    await get_chunks_batch(_="user", source_id="s-1", service=service, ids=ids)

    passed = service.get_chunks_by_ids.call_args.kwargs["chunk_ids"]
    assert len(passed) == cap


@pytest.mark.asyncio
async def test_create_import_rejects_oversized_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Streaming past max_upload_bytes raises ValidationError (no unbounded read)."""
    from chaoscypher_cortex.features.export import api as export_api

    # Tiny cap so a few bytes trip it; no-op the disk preflight.
    fake_settings = SimpleNamespace(
        batching=SimpleNamespace(
            upload_chunk_size=4,
            max_upload_bytes=10,
            upload_disk_headroom_bytes=0,
        ),
        data_dir="/tmp",
    )
    monkeypatch.setattr("chaoscypher_core.app_config.get_settings", lambda: fake_settings)
    monkeypatch.setattr("chaoscypher_core.utils.disk.check_disk_space", lambda *a, **k: None)

    file = MagicMock()
    # 12 bytes total > 10-byte cap (chunks of 4).
    file.read = AsyncMock(side_effect=[b"1234", b"5678", b"9012", b""])
    export_service = MagicMock()
    export_service.queue_import = AsyncMock()

    with pytest.raises(ValidationError):
        await export_api.create_import(
            _="user", export_service=export_service, file=file, merge=False
        )

    # The oversized upload never reaches the queue.
    export_service.queue_import.assert_not_called()
