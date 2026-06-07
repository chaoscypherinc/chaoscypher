# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""application/octet-stream is rejected by default; operators can re-enable.

Workstream 6 (2026-05-07): the previous default allowlist included
``application/octet-stream``, which defeated the allowlist altogether
because every binary the browser doesn't recognise is uploaded as
octet-stream. The new default omits it; operators who explicitly want
binary uploads can add it back via ``settings.yaml``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.app_config import BatchSettings
from chaoscypher_core.exceptions import ValidationError
from chaoscypher_cortex.features.sources.upload_service import UploadService


def _settings_with_allowlist(allowlist: list[str]) -> SimpleNamespace:
    """Build a minimal settings stub that mirrors the production shape."""
    return SimpleNamespace(
        batching=SimpleNamespace(
            max_upload_bytes=10_485_760,
            upload_max_concurrent=4,
            upload_chunk_size=4096,
            upload_disk_headroom_bytes=10_000_000,
            upload_content_type_allowlist=set(allowlist),
            max_upload_files=20,
        ),
        data_dir=str(Path("/tmp")),
        current_database="test_db",
    )


def test_octet_stream_not_in_default_allowlist() -> None:
    """The Pydantic default factory must not include ``application/octet-stream``."""
    defaults = BatchSettings().upload_content_type_allowlist
    assert "application/octet-stream" not in defaults
    # Sanity: other common types still present so we didn't gut the list.
    assert "application/pdf" in defaults
    assert "text/plain" in defaults


def test_octet_stream_upload_rejected_by_default() -> None:
    """An UploadService backed by the production default allowlist rejects octet-stream."""
    settings = _settings_with_allowlist(BatchSettings().upload_content_type_allowlist)
    service = UploadService(
        settings=settings,
        source_processing_service=AsyncMock(),
    )
    with pytest.raises(ValidationError) as exc_info:
        service.validate_content_type(MagicMock(content_type="application/octet-stream"))
    assert "application/octet-stream" in exc_info.value.message


def test_operator_can_re_enable_octet_stream() -> None:
    """Adding ``application/octet-stream`` to the allowlist re-enables it."""
    extras = [*BatchSettings().upload_content_type_allowlist, "application/octet-stream"]
    settings = _settings_with_allowlist(extras)
    service = UploadService(
        settings=settings,
        source_processing_service=AsyncMock(),
    )
    # No exception raised.
    service.validate_content_type(MagicMock(content_type="application/octet-stream"))
