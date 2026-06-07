# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""TLSService.enable_custom must not block the event loop on disk I/O."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_cortex.features.settings.tls_service import TLSService


def _make_tls_service(tmp_path: Path) -> TLSService:
    """Build a TLSService using a minimal TLSSettings stub."""
    cert_dir = tmp_path / "certs"
    cert_dir.mkdir()

    tls_settings = MagicMock()
    tls_settings.cert_dir = str(cert_dir)
    tls_settings.cert_filename = "server.crt"
    tls_settings.key_filename = "server.key"
    tls_settings.nginx_active_conf = str(tmp_path / "active.conf")
    tls_settings.nginx_http_conf = str(tmp_path / "http.conf")
    tls_settings.nginx_https_conf = str(tmp_path / "https.conf")

    return TLSService(tls_settings)


@pytest.mark.asyncio
async def test_enable_custom_does_not_block_event_loop(tmp_path: Path) -> None:
    """A slow write must not stall a concurrent async ticker."""
    service = _make_tls_service(tmp_path)
    # Stub the nginx swap so the test focuses on disk I/O.
    service._switch_nginx_config = MagicMock()  # type: ignore[method-assign]

    tick_count = 0

    async def ticker() -> None:
        nonlocal tick_count
        for _ in range(20):
            await asyncio.sleep(0.02)
            tick_count += 1

    original_write_bytes = Path.write_bytes

    def slow_write_bytes(self: Path, data: bytes) -> int:
        time.sleep(0.3)  # simulate slow disk
        return original_write_bytes(self, data)

    with patch.object(Path, "write_bytes", new=slow_write_bytes):
        ticker_task = asyncio.create_task(ticker())
        enable_task = asyncio.create_task(
            service.enable_custom(cert_pem=b"-fake-cert-", key_pem=b"-fake-key-")
        )
        await asyncio.gather(ticker_task, enable_task)

    # If write_bytes blocked the loop, tick_count would be ~5 (two 0.3s writes
    # consume ~0.6 s, leaving only ~10 of the 20 ticks to fire while the loop
    # is actually free). With asyncio.to_thread, it stays ~20.
    assert tick_count >= 15, f"Event loop appears blocked: only {tick_count} ticks fired"
