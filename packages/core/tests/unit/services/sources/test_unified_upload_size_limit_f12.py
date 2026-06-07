# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""F12: file uploads and URL fetches share batching.max_upload_bytes.

Before this change the file-upload path consulted the legacy
``source_processing.source_processing_max_file_size_gb`` (default 100 GB)
while the URL-fetch path consulted ``batching.max_upload_bytes`` (default
500 MB), so the same content was accepted or rejected depending on entry
path. F12 unifies both paths on ``batching.max_upload_bytes``; this module
exercises the shared cap end-to-end with a small (1 MiB) test value to
keep the tests fast and small on disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.operations.sources.url_fetch_handler import handle_fetch_url
from chaoscypher_core.services.sources.management.service import (
    SourceProcessingService,
)
from chaoscypher_core.settings import (
    SourceProcessingSettings,
    _reset_max_file_size_gb_deprecation_warning,
)


_TEST_CAP_BYTES = 1024 * 1024  # 1 MiB — used as the unified upload cap in tests
_OVER_CAP = _TEST_CAP_BYTES + 1024  # 1 KiB over the cap
_UNDER_CAP = _TEST_CAP_BYTES // 2  # comfortably under the cap


# ---------------------------------------------------------------------------
# File-upload path (SourceProcessingService.upload_file with staged_file_path)
# ---------------------------------------------------------------------------


def _make_file_service(
    tmp_path: Path,
    max_upload_bytes: int = _TEST_CAP_BYTES,
) -> SourceProcessingService:
    """Build a SourceProcessingService mocked just enough to exercise the size check."""
    source_manager = MagicMock()
    source_manager.find_by_content_hash.return_value = None
    source_manager.upload_source.return_value = {
        "id": "src_under",
        "status": "pending",
        "filename": "doc.bin",
    }
    source_manager.get_file.return_value = None

    settings = MagicMock()
    settings.current_database = "default"
    settings.database_dir = tmp_path
    settings.batching.max_upload_bytes = max_upload_bytes
    settings.priorities.background = 50

    config_manager = MagicMock()
    config_manager.get_settings.return_value = settings

    operations_manager = MagicMock()

    async def fake_queue(**_: Any) -> dict[str, str]:
        return {"task_id": "tsk_1", "status": "queued"}

    operations_manager.queue_import_indexing.side_effect = fake_queue

    return SourceProcessingService(
        source_manager=source_manager,
        operations_manager=operations_manager,
        config_manager=config_manager,
        validators=MagicMock(),
    )


@pytest.mark.asyncio
async def test_file_upload_rejects_file_over_cap(tmp_path: Path) -> None:
    """File-upload path rejects bytes > batching.max_upload_bytes."""
    import hashlib

    service = _make_file_service(tmp_path)

    staged = tmp_path / "over_cap.bin"
    staged.write_bytes(b"x" * _OVER_CAP)
    # F14 (PR 3) verifies caller-supplied content_hash matches actual content,
    # so pass the real hash to ensure we test the size cap, not hash mismatch.
    real_hash = hashlib.sha256(b"x" * _OVER_CAP).hexdigest()

    with pytest.raises(ValidationError) as exc_info:
        await service.upload_file(
            filename="over_cap.bin",
            staged_file_path=staged,
            content_hash=real_hash,
            file_size=_OVER_CAP,
        )

    rendered = str(exc_info.value)
    # Error must reference the unified cap, not the legacy GB cap.
    assert "max_upload_bytes" in rendered
    assert str(_TEST_CAP_BYTES) in rendered


@pytest.mark.asyncio
async def test_file_upload_accepts_file_under_cap(tmp_path: Path) -> None:
    """File-upload path accepts bytes <= batching.max_upload_bytes."""
    import hashlib

    service = _make_file_service(tmp_path)

    staged = tmp_path / "under_cap.bin"
    staged.write_bytes(b"x" * _UNDER_CAP)
    real_hash = hashlib.sha256(b"x" * _UNDER_CAP).hexdigest()

    result = await service.upload_file(
        filename="under_cap.bin",
        staged_file_path=staged,
        content_hash=real_hash,
        file_size=_UNDER_CAP,
    )

    assert result["id"] == "src_under"


# ---------------------------------------------------------------------------
# URL-fetch path (handle_fetch_url → SourceProcessingService.upload_file)
# ---------------------------------------------------------------------------


def _make_url_sps_with_real_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    max_upload_bytes: int = _TEST_CAP_BYTES,
) -> tuple[MagicMock, MagicMock, list[ValidationError]]:
    """Wire up the URL handler so its size check uses the test cap.

    Returns (sps, storage, raised_validation_errors). The SPS upload_file
    delegates to a small stub that performs the same size check the real
    service performs, so we can assert both paths hit the same cap with the
    same error message.
    """
    storage = MagicMock()

    settings = MagicMock()
    settings.batching.max_upload_bytes = max_upload_bytes
    settings.batching.upload_content_type_allowlist = [
        "text/html",
        "text/plain",
        "text/markdown",
        "application/pdf",
    ]
    monkeypatch.setattr(
        "chaoscypher_core.operations.sources.url_fetch_handler.get_settings",
        lambda: settings,
    )

    sps = MagicMock()
    sps.source_manager = storage

    raised: list[ValidationError] = []

    async def stub_upload_file(**kwargs: Any) -> dict[str, Any]:
        # Mirror the real service's check so we can assert that both entry
        # paths produce the *same* error when the cap is exceeded.
        size = kwargs.get("file_size", 0)
        if size > max_upload_bytes:
            err = ValidationError(
                f"File size exceeds maximum upload size of "
                f"{max_upload_bytes} bytes (max_upload_bytes)"
            )
            raised.append(err)
            raise err
        return {"id": "src_url", "filename": kwargs.get("filename", "")}

    sps.upload_file = stub_upload_file
    return sps, storage, raised


@pytest.mark.asyncio
async def test_url_fetch_rejects_content_over_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """URL fetch path rejects fetched content > batching.max_upload_bytes."""
    sps, storage, _ = _make_url_sps_with_real_check(monkeypatch, tmp_path)

    from chaoscypher_core.adapters.web.search import FetchResult

    big_content = "y" * _OVER_CAP

    with patch("chaoscypher_core.adapters.web.search.WebScraper") as mock_scraper:
        scraper_instance = mock_scraper.return_value
        scraper_instance.extract_full_content = AsyncMock(
            return_value=FetchResult(
                content=big_content,
                content_type="text/html",
                title="Big",
                error=None,
            )
        )

        result = await handle_fetch_url(
            data={"url": "https://example.com/", "options": {}},
            source_processing_service=sps,
            metadata={"database_name": "default", "operation_type": "fetch_url"},
            task_id="tsk_url_over",
        )

    # The url_fetch_handler catches the over-cap case before delegating to
    # upload_file (its own pre-encode check fires) and returns an error
    # dict mentioning the unified cap.
    assert result["status"] == "error"
    assert "max_upload_bytes" in result["error"]
    storage.fail_url_fetch.assert_called_once()


@pytest.mark.asyncio
async def test_url_fetch_accepts_content_under_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """URL fetch path accepts content <= batching.max_upload_bytes."""
    sps, storage, raised = _make_url_sps_with_real_check(monkeypatch, tmp_path)

    from chaoscypher_core.adapters.web.search import FetchResult

    small_content = "z" * _UNDER_CAP

    with patch("chaoscypher_core.adapters.web.search.WebScraper") as mock_scraper:
        scraper_instance = mock_scraper.return_value
        scraper_instance.extract_full_content = AsyncMock(
            return_value=FetchResult(
                content=small_content,
                content_type="text/html",
                title="Small",
                error=None,
            )
        )

        result = await handle_fetch_url(
            data={"url": "https://example.com/", "options": {}},
            source_processing_service=sps,
            metadata={"database_name": "default", "operation_type": "fetch_url"},
            task_id="tsk_url_under",
        )

    # No size-related errors raised by the inner stub.
    assert raised == []
    assert result["id"] == "src_url"
    storage.fail_url_fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Deprecation warning for the legacy field
# ---------------------------------------------------------------------------


def _render_warnings(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> str:
    """Return all warning output from both structlog renderers.

    structlog's renderer routing depends on whether something else has
    bridged it to stdlib ``logging`` (conftest-driven setups frequently do
    this). To stay portable across single-file and broader test runs, we
    union ``capsys`` (ConsoleRenderer → stdout/stderr) and ``caplog``
    (LoggerFactory → stdlib logging) outputs.
    """
    captured = capsys.readouterr()
    caplog_text = " ".join(
        f"{record.levelname} {record.name}:{record.message} {record.__dict__}"
        for record in caplog.records
    )
    return captured.out + captured.err + " " + caplog_text


def test_legacy_field_emits_deprecation_warning_when_set(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Setting the deprecated GB cap to a non-default value logs a warning.

    The warning is fired by ``_warn_if_deprecated_max_file_size_gb`` and is
    gated by a module-level flag so we reset it explicitly here to make the
    assertion deterministic regardless of test order.
    """
    _reset_max_file_size_gb_deprecation_warning()
    caplog.set_level("WARNING")

    SourceProcessingSettings(source_processing_max_file_size_gb=42)

    rendered = _render_warnings(capsys, caplog)
    assert "source_processing_max_file_size_gb_deprecated" in rendered
    assert "batching.max_upload_bytes" in rendered


def test_legacy_field_at_default_does_not_warn(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Constructing with the default value does NOT emit a warning."""
    _reset_max_file_size_gb_deprecation_warning()
    caplog.set_level("WARNING")

    SourceProcessingSettings()  # default = 100

    rendered = _render_warnings(capsys, caplog)
    assert "source_processing_max_file_size_gb_deprecated" not in rendered


def test_legacy_field_warning_fires_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated construction with the deprecated value warns at most once.

    The module-level ``_max_file_size_gb_deprecation_warned`` flag prevents
    the warning from spamming the log when the same legacy setting is read
    on every request (or on every test fixture build).

    Spies on ``structlog.get_logger(__name__).warning`` indirectly by
    intercepting the structlog ``BoundLogger.warning`` method on a fresh
    logger — independent of caplog/capsys routing which differs between
    isolated and full-sweep runs.
    """
    import structlog

    _reset_max_file_size_gb_deprecation_warning()

    emissions: list[str] = []
    # Patch structlog.get_logger to return a logger whose .warning records
    # the event name. Affects only this test (monkeypatch auto-reverts).
    original_get_logger = structlog.get_logger

    class _RecordingLogger:
        def warning(self, event: str, **_kwargs: object) -> None:
            emissions.append(event)

        def __getattr__(self, _name: str) -> object:
            # No-op for any other log methods (info/debug/error/exception).
            return lambda *_a, **_k: None

    monkeypatch.setattr(structlog, "get_logger", lambda *_a, **_k: _RecordingLogger())

    SourceProcessingSettings(source_processing_max_file_size_gb=42)
    after_first = sum(1 for e in emissions if e == "source_processing_max_file_size_gb_deprecated")

    SourceProcessingSettings(source_processing_max_file_size_gb=42)
    SourceProcessingSettings(source_processing_max_file_size_gb=99)
    after_total = sum(1 for e in emissions if e == "source_processing_max_file_size_gb_deprecated")

    # Restore (defensive — monkeypatch auto-restores at test exit anyway).
    monkeypatch.setattr(structlog, "get_logger", original_get_logger)

    assert after_first == 1, (
        f"Expected exactly one deprecation emission after first construction; got {after_first}"
    )
    assert after_total == 1, (
        f"Expected the once-per-process guard to suppress subsequent constructions; "
        f"got {after_total} total emissions across three constructions"
    )
