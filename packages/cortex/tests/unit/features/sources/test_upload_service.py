# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for UploadService — encapsulates sources upload orchestration.

Part of Workstream B / Decision 4 of the 2026-04-23 architecture audit.
Moves ~150 lines of upload logic (semaphore, preflight, streaming, single,
batch) out of sources/api.py into a dedicated service class.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import UploadFile

from chaoscypher_cortex.features.sources.upload_service import UploadService


def _settings(max_bytes: int = 10_485_760, upload_concurrency: int = 4, chunk_size: int = 4096):
    return SimpleNamespace(
        batching=SimpleNamespace(
            max_upload_bytes=max_bytes,
            upload_max_concurrent=upload_concurrency,
            upload_chunk_size=chunk_size,
            upload_disk_headroom_bytes=10_000_000,
            upload_content_type_allowlist={"*"},
            max_upload_files=20,
        ),
        data_dir=str(Path("/tmp")),
        database_dir=Path("/tmp"),
        current_database="test_db",
    )


def _upload_file_from_bytes(data: bytes, filename: str = "test.txt") -> UploadFile:
    """Build a FastAPI UploadFile backed by an in-memory buffer."""
    return UploadFile(filename=filename, file=io.BytesIO(data))


def test_upload_service_semaphore_is_instance_attribute() -> None:
    """UploadService owns its own semaphore (no module-level mutable state)."""
    service = UploadService(
        settings=_settings(upload_concurrency=3),
        source_processing_service=AsyncMock(),
    )
    assert isinstance(service._semaphore, asyncio.Semaphore)
    # Two instances of UploadService have independent semaphores
    service2 = UploadService(
        settings=_settings(upload_concurrency=7),
        source_processing_service=AsyncMock(),
    )
    assert service._semaphore is not service2._semaphore


@pytest.mark.asyncio
async def test_stream_upload_to_temp_computes_sha256_and_size() -> None:
    """stream_upload_to_temp writes a temp file, computes SHA-256, returns size."""
    import hashlib

    data = b"hello world" * 100  # 1100 bytes
    expected_hash = hashlib.sha256(data).hexdigest()
    service = UploadService(
        settings=_settings(),
        source_processing_service=AsyncMock(),
    )
    upload = _upload_file_from_bytes(data)
    temp_path, content_hash, size = await service.stream_upload_to_temp(upload)
    try:
        assert temp_path.exists()
        assert temp_path.read_bytes() == data
        assert content_hash == expected_hash
        assert size == len(data)
    finally:
        temp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_stream_upload_to_temp_enforces_max_bytes() -> None:
    """Uploads that exceed max_upload_bytes raise ValidationError and clean up."""
    from chaoscypher_core.exceptions import ValidationError

    service = UploadService(
        settings=_settings(max_bytes=100),
        source_processing_service=AsyncMock(),
    )
    upload = _upload_file_from_bytes(b"x" * 10_000)
    with pytest.raises(ValidationError):
        await service.stream_upload_to_temp(upload)


def test_validate_content_type_allows_star() -> None:
    """Content-type check is a no-op when allowlist contains '*'."""
    service = UploadService(
        settings=_settings(),
        source_processing_service=AsyncMock(),
    )
    # No exception for any content type
    service.validate_content_type(MagicMock(content_type="application/octet-stream"))


def test_validate_content_type_rejects_unknown() -> None:
    """Content-type check raises ValidationError for types not in allowlist."""
    from chaoscypher_core.exceptions import ValidationError

    s = _settings()
    s.batching.upload_content_type_allowlist = {"text/plain", "application/pdf"}
    service = UploadService(
        settings=s,
        source_processing_service=AsyncMock(),
    )
    with pytest.raises(ValidationError) as exc_info:
        service.validate_content_type(MagicMock(content_type="image/png"))
    assert "image/png" in exc_info.value.message


def test_preflight_disk_for_upload_uses_max_upload_bytes_when_total_omitted() -> None:
    """F9: preflight without total_bytes retains single-file behavior."""
    service = UploadService(
        settings=_settings(),
        source_processing_service=AsyncMock(),
    )

    captured: dict[str, int] = {}

    def _fake_check(_path, *, min_bytes: int) -> None:
        captured["min_bytes"] = min_bytes

    import chaoscypher_cortex.features.sources.upload_service as us

    original = us.check_disk_space
    us.check_disk_space = _fake_check
    try:
        service.preflight_disk_for_upload()
    finally:
        us.check_disk_space = original

    # Single-file path: max_upload_bytes (10MB) + headroom (10MB) = 20MB
    assert captured["min_bytes"] == 10_485_760 + 10_000_000


def test_preflight_disk_for_upload_uses_total_bytes_when_provided() -> None:
    """F9: preflight with total_bytes adds headroom to the batch total."""
    service = UploadService(
        settings=_settings(),
        source_processing_service=AsyncMock(),
    )

    captured: dict[str, int] = {}

    def _fake_check(_path, *, min_bytes: int) -> None:
        captured["min_bytes"] = min_bytes

    import chaoscypher_cortex.features.sources.upload_service as us

    original = us.check_disk_space
    us.check_disk_space = _fake_check
    try:
        service.preflight_disk_for_upload(total_bytes=42_000_000)
    finally:
        us.check_disk_space = original

    # Batch path: 42MB + headroom (10MB) = 52MB
    assert captured["min_bytes"] == 42_000_000 + 10_000_000


@pytest.mark.asyncio
async def test_batch_upload_preflights_total_size_not_per_file() -> None:
    """F9: 10x50MB batch onto 100MB free disk rejects upfront, no files written."""
    from chaoscypher_core.exceptions import InsufficientStorageError

    settings = MagicMock()
    settings.batching.upload_max_concurrent = 4
    settings.batching.upload_chunk_size = 1024
    settings.batching.max_upload_bytes = 50 * 1024 * 1024  # 50MB per file
    settings.batching.upload_disk_headroom_bytes = 10 * 1024 * 1024
    settings.batching.upload_content_type_allowlist = ["*"]
    settings.batching.max_upload_files = 50
    settings.current_database = "default"
    settings.data_dir = "/tmp"

    service = UploadService(
        settings=settings,
        source_processing_service=AsyncMock(),
    )

    # Patch check_disk_space at the module level so the real shutil call doesn't
    # query the host filesystem. Simulate ~100MB free.
    captured_min_bytes: list[int] = []

    def _fake_check(_path, *, min_bytes: int) -> None:
        captured_min_bytes.append(min_bytes)
        free = 100 * 1024 * 1024  # 100MB free
        if free < min_bytes:
            raise InsufficientStorageError(
                f"Insufficient disk for batch: required={min_bytes} bytes, available={free}"
            )

    import chaoscypher_cortex.features.sources.upload_service as us

    original = us.check_disk_space
    us.check_disk_space = _fake_check

    # Spy on upload_single — it should NEVER be called because preflight rejects.
    upload_single_spy = AsyncMock()
    service.upload_single = upload_single_spy

    try:
        files = []
        for i in range(10):
            f = MagicMock()
            f.filename = f"file_{i}.bin"
            f.size = 50 * 1024 * 1024  # 50MB each → total 500MB
            files.append(f)

        with pytest.raises(InsufficientStorageError) as exc_info:
            await service.upload_batch(
                files=files,
                sanitize_filename=lambda n: n or "unknown",
                extract_entities=True,
                analysis_depth="full",
                enable_normalization=True,
                forced_domain=None,
                skip_duplicates=False,
                content_filtering=True,
                filtering_mode=None,
            )

        # Required = 500MB total + 10MB headroom = 510MB
        expected = (10 * 50 * 1024 * 1024) + (10 * 1024 * 1024)
        assert captured_min_bytes[0] == expected
        assert "Insufficient" in str(exc_info.value)
        upload_single_spy.assert_not_called()
    finally:
        us.check_disk_space = original


@pytest.mark.asyncio
async def test_batch_upload_falls_back_to_max_per_file_when_size_unknown() -> None:
    """F9: when UploadFile.size is None, treat each file as worst-case (max_upload_bytes)."""
    settings = MagicMock()
    settings.batching.upload_max_concurrent = 4
    settings.batching.upload_chunk_size = 1024
    settings.batching.max_upload_bytes = 50 * 1024 * 1024  # 50MB per file
    settings.batching.upload_disk_headroom_bytes = 10 * 1024 * 1024
    settings.batching.upload_content_type_allowlist = ["*"]
    settings.batching.max_upload_files = 50
    settings.current_database = "default"
    settings.data_dir = "/tmp"

    service = UploadService(
        settings=settings,
        source_processing_service=AsyncMock(),
    )

    captured_min_bytes: list[int] = []

    def _fake_check(_path, *, min_bytes: int) -> None:
        captured_min_bytes.append(min_bytes)
        # Plenty of disk so we just observe the requested min_bytes.

    import chaoscypher_cortex.features.sources.upload_service as us

    original = us.check_disk_space
    us.check_disk_space = _fake_check

    # Stub upload_single so the test doesn't need a real streaming path.
    async def _ok(**kwargs):
        return {"id": "src", "filename": kwargs["safe_filename"]}

    service.upload_single = AsyncMock(side_effect=_ok)

    try:
        files = []
        for i in range(3):
            f = MagicMock()
            f.filename = f"file_{i}.bin"
            f.size = None  # client omitted Content-Length / unknown
            files.append(f)

        await service.upload_batch(
            files=files,
            sanitize_filename=lambda n: n or "unknown",
            extract_entities=True,
            analysis_depth="full",
            enable_normalization=True,
            forced_domain=None,
            skip_duplicates=False,
            content_filtering=True,
            filtering_mode=None,
        )

        # Worst-case: 3 x 50MB + 10MB headroom on the upfront batch preflight.
        # captured_min_bytes[0] is the batch preflight; later entries are the
        # single-file preflights inside upload_single - but we stubbed it.
        expected = (3 * 50 * 1024 * 1024) + (10 * 1024 * 1024)
        assert captured_min_bytes[0] == expected
    finally:
        us.check_disk_space = original


@pytest.mark.asyncio
async def test_batch_upload_threads_enable_vision_to_each_file() -> None:
    """F1: enable_vision=True on /sources/batch reaches every queued task."""
    settings = MagicMock()
    settings.batching.upload_max_concurrent = 4
    settings.batching.upload_chunk_size = 1024
    settings.batching.max_upload_bytes = 10_000
    settings.batching.upload_disk_headroom_bytes = 0
    settings.batching.upload_content_type_allowlist = ["*"]
    settings.batching.max_upload_files = 10
    settings.current_database = "default"
    settings.data_dir = "/tmp"

    queued: list[dict] = []

    async def _capture(**kwargs):
        queued.append(kwargs)
        return {"id": "src_" + kwargs["filename"], "filename": kwargs["filename"]}

    core_service = MagicMock()
    core_service.upload_file = AsyncMock(side_effect=_capture)

    service = UploadService(
        settings=settings,
        source_processing_service=core_service,
    )
    service.preflight_disk_for_upload = MagicMock()
    service.validate_content_type = MagicMock()
    service.stream_upload_to_temp = AsyncMock(return_value=(Path("/tmp/x"), "h", 5))

    file_a = MagicMock()
    file_a.filename = "a.txt"
    file_a.size = 5
    file_b = MagicMock()
    file_b.filename = "b.txt"
    file_b.size = 5

    await service.upload_batch(
        files=[file_a, file_b],
        sanitize_filename=lambda n: n or "unknown",
        extract_entities=True,
        analysis_depth="full",
        enable_normalization=True,
        forced_domain=None,
        skip_duplicates=False,
        enable_vision=True,
        content_filtering=True,
        filtering_mode=None,
    )

    assert len(queued) == 2
    assert all(p["enable_vision"] is True for p in queued)


@pytest.mark.asyncio
async def test_batch_upload_surfaces_validation_error_message() -> None:
    """F2/F4/F10: per-file ValidationError surfaces real message, capped + prefixed."""
    from chaoscypher_core.exceptions import ValidationError

    settings = MagicMock()
    settings.batching.upload_max_concurrent = 4
    settings.batching.upload_chunk_size = 1024
    settings.batching.max_upload_bytes = 10_000
    settings.batching.upload_disk_headroom_bytes = 0
    settings.batching.upload_content_type_allowlist = ["*"]
    settings.batching.max_upload_files = 10
    settings.current_database = "default"
    settings.data_dir = "/tmp"
    settings.logs.error_message_preview_chars = 200

    service = UploadService(
        settings=settings,
        source_processing_service=AsyncMock(),
    )
    service.preflight_disk_for_upload = MagicMock()
    service.validate_content_type = MagicMock()

    async def _raise_validation(**_kwargs):
        raise ValidationError("Upload exceeds max_upload_bytes=10000", field="file")

    service.upload_single = AsyncMock(side_effect=_raise_validation)

    bad_file = MagicMock()
    bad_file.filename = "huge.bin"
    bad_file.size = 5

    successes, errors = await service.upload_batch(
        files=[bad_file],
        sanitize_filename=lambda n: n or "unknown",
        extract_entities=True,
        analysis_depth="full",
        enable_normalization=True,
        forced_domain=None,
        skip_duplicates=False,
        content_filtering=True,
        filtering_mode=None,
    )

    assert successes == []
    assert len(errors) == 1
    fn, msg = errors[0]
    assert fn == "huge.bin"
    assert msg.startswith("ValidationError: ")
    assert "max_upload_bytes=10000" in msg
    assert len(msg) <= len("ValidationError: ") + 200


@pytest.mark.asyncio
async def test_batch_upload_distinguishes_disk_full_from_validation() -> None:
    """F2/F4/F10: InsufficientStorageError and ValidationError surface distinguishably."""
    from chaoscypher_core.exceptions import InsufficientStorageError, ValidationError

    settings = MagicMock()
    settings.batching.upload_max_concurrent = 4
    settings.batching.upload_chunk_size = 1024
    settings.batching.max_upload_bytes = 10_000
    settings.batching.upload_disk_headroom_bytes = 0
    settings.batching.upload_content_type_allowlist = ["*"]
    settings.batching.max_upload_files = 10
    settings.current_database = "default"
    settings.data_dir = "/tmp"
    settings.logs.error_message_preview_chars = 200

    service = UploadService(
        settings=settings,
        source_processing_service=AsyncMock(),
    )
    service.preflight_disk_for_upload = MagicMock()
    service.validate_content_type = MagicMock()

    async def _dispatch(**kwargs):
        if kwargs["safe_filename"] == "big.bin":
            raise ValidationError("Upload exceeds max_upload_bytes=10000", field="file")
        if kwargs["safe_filename"] == "fullfs.bin":
            raise InsufficientStorageError("Insufficient disk space: 5MB available, 100MB required")
        return {"id": "src_ok", "filename": kwargs["safe_filename"]}

    service.upload_single = AsyncMock(side_effect=_dispatch)

    big = MagicMock()
    big.filename = "big.bin"
    big.size = 5
    full = MagicMock()
    full.filename = "fullfs.bin"
    full.size = 5
    ok = MagicMock()
    ok.filename = "ok.bin"
    ok.size = 5

    successes, errors = await service.upload_batch(
        files=[big, full, ok],
        sanitize_filename=lambda n: n or "unknown",
        extract_entities=True,
        analysis_depth="full",
        enable_normalization=True,
        forced_domain=None,
        skip_duplicates=False,
        content_filtering=True,
        filtering_mode=None,
    )

    assert len(successes) == 1
    assert len(errors) == 2
    err_by_file = dict(errors)
    assert err_by_file["big.bin"].startswith("ValidationError: ")
    assert "max_upload_bytes" in err_by_file["big.bin"]
    assert err_by_file["fullfs.bin"].startswith("InsufficientStorageError: ")
    assert "Insufficient disk" in err_by_file["fullfs.bin"]


@pytest.mark.asyncio
async def test_batch_upload_caps_long_exception_messages_at_200_chars() -> None:
    """F2/F4/F10: long underlying messages are truncated to 200 chars."""
    settings = MagicMock()
    settings.batching.upload_max_concurrent = 4
    settings.batching.upload_chunk_size = 1024
    settings.batching.max_upload_bytes = 10_000
    settings.batching.upload_disk_headroom_bytes = 0
    settings.batching.upload_content_type_allowlist = ["*"]
    settings.batching.max_upload_files = 10
    settings.current_database = "default"
    settings.data_dir = "/tmp"
    settings.logs.error_message_preview_chars = 200

    service = UploadService(
        settings=settings,
        source_processing_service=AsyncMock(),
    )
    service.preflight_disk_for_upload = MagicMock()
    service.validate_content_type = MagicMock()

    long_msg = "x" * 1000

    async def _raise_runtime(**_kwargs):
        raise RuntimeError(long_msg)

    service.upload_single = AsyncMock(side_effect=_raise_runtime)

    f = MagicMock()
    f.filename = "any.bin"
    f.size = 5

    _, errors = await service.upload_batch(
        files=[f],
        sanitize_filename=lambda n: n or "unknown",
        extract_entities=True,
        analysis_depth="full",
        enable_normalization=True,
        forced_domain=None,
        skip_duplicates=False,
        content_filtering=True,
        filtering_mode=None,
    )

    assert len(errors) == 1
    _, msg = errors[0]
    # Prefix + at most 200 chars of message body.
    assert msg.startswith("RuntimeError: ")
    body = msg[len("RuntimeError: ") :]
    assert len(body) == 200
    assert body == "x" * 200


@pytest.mark.asyncio
async def test_batch_upload_enable_vision_defaults_to_none() -> None:
    """F1: omitting enable_vision (default None) forwards None per file."""
    settings = MagicMock()
    settings.batching.upload_max_concurrent = 4
    settings.batching.upload_chunk_size = 1024
    settings.batching.max_upload_bytes = 10_000
    settings.batching.upload_disk_headroom_bytes = 0
    settings.batching.upload_content_type_allowlist = ["*"]
    settings.batching.max_upload_files = 10
    settings.current_database = "default"
    settings.data_dir = "/tmp"

    queued: list[dict] = []

    async def _capture(**kwargs):
        queued.append(kwargs)
        return {"id": "src_" + kwargs["filename"], "filename": kwargs["filename"]}

    core_service = MagicMock()
    core_service.upload_file = AsyncMock(side_effect=_capture)

    service = UploadService(
        settings=settings,
        source_processing_service=core_service,
    )
    service.preflight_disk_for_upload = MagicMock()
    service.validate_content_type = MagicMock()
    service.stream_upload_to_temp = AsyncMock(return_value=(Path("/tmp/x"), "h", 5))

    file_a = MagicMock()
    file_a.filename = "a.txt"
    file_a.size = 5

    await service.upload_batch(
        files=[file_a],
        sanitize_filename=lambda n: n or "unknown",
        extract_entities=True,
        analysis_depth="full",
        enable_normalization=True,
        forced_domain=None,
        skip_duplicates=False,
        content_filtering=True,
        filtering_mode=None,
    )

    assert len(queued) == 1
    assert queued[0]["enable_vision"] is None
