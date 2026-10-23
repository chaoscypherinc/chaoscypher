# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``read_capped_upload``: the 413 cap every buffering upload handler shares.

The Lexicon upload route is exempt from the body-size middleware
(``_UPLOAD_PATHS``), so this cap is its whole server-side limit; the TLS
cert/key upload uses it too. Each caller is pinned below, plus the helper's
bounded read (an unbounded ``file.read()`` would buffer any body first).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from chaoscypher_cortex.features.lexicon.api import upload_package
from chaoscypher_cortex.features.settings import api as settings_api
from chaoscypher_cortex.shared.api.errors import read_capped_upload


class _Upload:
    """An UploadFile stand-in that serves at most the requested bytes.

    It refuses an unbounded read, so a handler that drops the size bound
    fails here instead of passing on a small payload.
    """

    def __init__(self, data: bytes, filename: str = "upload.bin") -> None:
        self._data = data
        self.filename = filename
        self.sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            msg = "unbounded read of an upload"
            raise AssertionError(msg)
        self.sizes.append(size)
        return self._data[:size]


@pytest.mark.asyncio
async def test_returns_content_at_the_cap() -> None:
    upload = _Upload(b"x" * 10)
    assert await read_capped_upload(upload, 10, "Thing") == b"x" * 10  # type: ignore[arg-type]
    assert upload.sizes == [11]


@pytest.mark.asyncio
async def test_rejects_one_byte_over_the_cap() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await read_capped_upload(_Upload(b"x" * 11), 10, "Thing")  # type: ignore[arg-type]
    assert exc_info.value.status_code == 413
    assert exc_info.value.detail["code"] == "PAYLOAD_TOO_LARGE"
    assert exc_info.value.detail["message"].startswith("Thing too large")


@pytest.mark.asyncio
async def test_lexicon_upload_rejects_an_oversized_package() -> None:
    service = AsyncMock()
    settings = MagicMock()
    settings.batching.max_upload_bytes = 10
    upload = _Upload(b"x" * 11, filename="pkg.ccx")

    with pytest.raises(HTTPException) as exc_info:
        await upload_package(
            _="test-user",
            service=service,
            settings=settings,
            file=upload,  # type: ignore[arg-type]
            public=True,
            message=None,
        )

    assert exc_info.value.status_code == 413
    assert upload.sizes == [11]
    service.upload.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("cert_len", "key_len"), [(11, 3), (3, 11)])
async def test_custom_tls_rejects_an_oversized_cert_or_key(cert_len: int, key_len: int) -> None:
    service = MagicMock()
    service.enable_custom = AsyncMock()
    settings = MagicMock()
    settings.batching.tls_cert_max_size = 10

    with pytest.raises(HTTPException) as exc_info:
        await settings_api.enable_custom_tls(
            _="test-user",
            cert_file=_Upload(b"c" * cert_len),  # type: ignore[arg-type]
            key_file=_Upload(b"k" * key_len),  # type: ignore[arg-type]
            service=service,
            settings=settings,
        )

    assert exc_info.value.status_code == 413
    service.enable_custom.assert_not_awaited()
