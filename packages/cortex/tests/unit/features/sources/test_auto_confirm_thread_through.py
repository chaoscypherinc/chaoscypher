# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""auto_confirm threads UploadService.upload_single -> core upload_file,
and confirmation_required is computed (not auto_confirm) and (domain is auto).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _build_settings() -> MagicMock:
    settings = MagicMock()
    settings.batching.upload_max_concurrent = 4
    settings.batching.upload_chunk_size = 1024
    settings.batching.max_upload_bytes = 10_000
    settings.batching.max_upload_files = 100
    settings.batching.upload_disk_headroom_bytes = 0
    settings.batching.upload_content_type_allowlist = ["*"]
    settings.current_database = "default"
    settings.data_dir = "/tmp"
    return settings


def _make_service(captured: list[dict]):
    from chaoscypher_cortex.features.sources.upload_service import UploadService

    async def _capture_upload_file(**kwargs):
        captured.append(kwargs)
        return {"id": "src_xyz", "filename": kwargs["filename"]}

    core_service = MagicMock()
    core_service.upload_file = AsyncMock(side_effect=_capture_upload_file)
    service = UploadService(
        settings=_build_settings(),
        source_processing_service=core_service,
    )
    service.preflight_disk_for_upload = MagicMock()
    service.validate_content_type = MagicMock()
    service.stream_upload_to_temp = AsyncMock(return_value=(Path("/tmp/x"), "hash", 5))
    return service


@pytest.mark.asyncio
async def test_upload_single_forwards_auto_confirm() -> None:
    captured: list[dict] = []
    service = _make_service(captured)
    upload_file = MagicMock()
    upload_file.filename = "t.txt"

    await service.upload_single(
        file=upload_file,
        safe_filename="t.txt",
        extract_entities=True,
        analysis_depth="full",
        enable_normalization=None,
        forced_domain=None,
        skip_duplicates=False,
        enable_vision=None,
        content_filtering=True,
        auto_confirm=False,
    )

    assert captured[0]["auto_confirm"] is False


@pytest.mark.asyncio
async def test_upload_batch_forwards_auto_confirm() -> None:
    captured: list[dict] = []
    service = _make_service(captured)
    f = MagicMock()
    f.filename = "a.txt"
    f.size = 5

    await service.upload_batch(
        files=[f],
        sanitize_filename=lambda n: n or "unknown",
        extract_entities=True,
        analysis_depth="full",
        enable_normalization=None,
        forced_domain=None,
        skip_duplicates=False,
        enable_vision=None,
        content_filtering=True,
        auto_confirm=True,
    )

    assert captured[0]["auto_confirm"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auto_confirm", "forced_domain", "expected"),
    [
        (False, None, True),  # auto domain, no bypass -> requires confirmation
        (True, None, False),  # auto domain, bypass set -> no confirmation
        (False, "medical", False),  # forced domain -> never requires confirmation
        (True, "medical", False),
    ],
)
async def test_core_upload_file_persists_confirmation_required(
    auto_confirm: bool, forced_domain: str | None, expected: bool
) -> None:
    """Core upload_file persists confirmation_required = not auto_confirm and (domain is auto)."""
    from chaoscypher_core.services.sources.management.service import SourceProcessingService

    svc = SourceProcessingService.__new__(SourceProcessingService)
    svc.validators = MagicMock()
    svc.config_manager = MagicMock()
    settings = MagicMock()
    settings.current_database = "default"
    settings.database_dir = Path("/tmp/db")
    settings.batching.max_upload_bytes = 10_000
    settings.priorities.background = 50
    svc.config_manager.get_settings.return_value = settings

    source_manager = MagicMock()
    source_manager.find_by_content_hash.return_value = None
    source_manager.upload_source.return_value = {"id": "src_1"}
    svc.source_manager = source_manager
    svc.operations_manager = MagicMock()
    svc.operations_manager.queue_import_indexing = AsyncMock(return_value={"task_id": "t"})

    await svc.upload_file(
        file_content=b"test content",
        filename="doc.txt",
        file_size=12,
        forced_domain=forced_domain,
        auto_confirm=auto_confirm,
    )

    kwargs = source_manager.upload_source.call_args.kwargs
    assert kwargs["confirmation_required"] is expected


@pytest.mark.asyncio
async def test_import_url_forwards_auto_confirm_option(monkeypatch) -> None:
    """import_url puts auto_confirm into the OP_FETCH_URL queue options."""
    from chaoscypher_cortex.features.sources import api as sources_api
    from chaoscypher_cortex.features.sources.models import UrlImportRequest

    captured: list[dict] = []

    async def fake_queue_fetch_url(**kwargs):
        captured.append(kwargs)
        return "tsk_url"

    async def fake_require_ready(_settings) -> None:
        return None

    monkeypatch.setattr(sources_api.queue_utils, "queue_fetch_url", fake_queue_fetch_url)
    monkeypatch.setattr(
        "chaoscypher_core.services.llm.require_extraction_ready", fake_require_ready
    )
    monkeypatch.setattr(sources_api, "validate_url_safety", lambda url, strict: True)

    settings = MagicMock()
    settings.current_database = "default"
    settings.priorities.background = 50

    req = UrlImportRequest(url="https://example.com/page", auto_confirm=False)
    await sources_api.import_url(_="u", request=req, settings=settings)

    assert captured[0]["options"]["auto_confirm"] is False
