# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""TLSService.enable_custom must not block the event loop on disk I/O.

The previous version of this test joined a fixed ``for _ in range(20)``
ticker with ``asyncio.gather`` and asserted ``tick_count >= 15`` — but the
gather waits for the ticker to finish, so the count was always exactly 20
and the test could not fail even with every ``asyncio.to_thread`` offload
deleted (hunt queue 2026-08-01). This version asserts the offload mechanism
itself: each disk operation must reach ``asyncio.to_thread``, which is
deterministic and fails the moment any write is made synchronous again.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

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
async def test_enable_custom_offloads_every_disk_op_to_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every filesystem call in enable_custom must go through asyncio.to_thread.

    Replacing any of the four offloads with a direct synchronous call (the
    2026-05 event-loop-starvation regression this file pins) removes that
    entry from the recorded sequence and fails the assertion — no wall-clock
    thresholds involved.
    """
    service = _make_tls_service(tmp_path)
    # Stub the nginx swap so the test focuses on disk I/O.
    service._switch_nginx_config = MagicMock()  # type: ignore[method-assign]

    offloaded: list[str] = []
    real_to_thread = asyncio.to_thread

    async def recording_to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        offloaded.append(getattr(func, "__qualname__", repr(func)))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", recording_to_thread)

    await service.enable_custom(cert_pem=b"-fake-cert-", key_pem=b"-fake-key-")

    assert offloaded == [
        "Path.mkdir",
        "Path.write_bytes",
        "Path.write_bytes",
        "Path.chmod",
    ]

    # And the offloaded calls really performed the writes.
    cert_path = tmp_path / "certs" / "server.crt"
    key_path = tmp_path / "certs" / "server.key"
    assert cert_path.read_bytes() == b"-fake-cert-"
    assert key_path.read_bytes() == b"-fake-key-"
    assert key_path.stat().st_mode & 0o777 == 0o600
    service._switch_nginx_config.assert_called_once_with(https=True)
