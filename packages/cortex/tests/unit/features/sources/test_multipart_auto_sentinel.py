# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Multipart upload normalizes the ``__auto__`` domain sentinel to None.

A client posting ``domain=__auto__`` (the UI's "auto-detect" option value)
must NOT pin ``forced_domain='__auto__'`` — that bypasses the confirmation gate
(a non-empty forced_domain proceeds) and forces a bogus domain. The URL-import
path already normalizes (api.py:~338); these tests pin the same behaviour for
the multipart ``/sources`` and ``/sources/batch`` routes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_cortex.features.sources import api as sources_api


@pytest.fixture(autouse=True)
def _stub_require_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ready(_settings: object) -> None:
        return None

    monkeypatch.setattr("chaoscypher_core.services.llm.require_extraction_ready", _ready)


def _upload_service() -> MagicMock:
    svc = MagicMock()
    svc.upload_single = AsyncMock(return_value={"id": "src_1", "filename": "t.txt"})
    svc.upload_batch = AsyncMock(return_value=([], []))
    return svc


def _file(name: str = "t.txt") -> MagicMock:
    f = MagicMock()
    f.filename = name
    return f


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["__auto__", "", None])
async def test_upload_single_normalizes_auto_sentinel(domain: str | None) -> None:
    """``__auto__`` / empty / None all forward forced_domain=None (gate engages)."""
    svc = _upload_service()

    await sources_api.upload_file(
        _="user",
        upload_service=svc,
        settings=MagicMock(),
        file=_file(),
        domain=domain,
    )

    svc.upload_single.assert_awaited_once()
    assert svc.upload_single.call_args.kwargs["forced_domain"] is None


@pytest.mark.asyncio
async def test_upload_single_real_domain_passes_through() -> None:
    """A real domain is NOT normalized away."""
    svc = _upload_service()

    await sources_api.upload_file(
        _="user",
        upload_service=svc,
        settings=MagicMock(),
        file=_file(),
        domain="medical",
    )

    assert svc.upload_single.call_args.kwargs["forced_domain"] == "medical"


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["__auto__", "", None])
async def test_upload_batch_normalizes_auto_sentinel(domain: str | None) -> None:
    """Batch route mirrors the single-upload normalization."""
    svc = _upload_service()

    await sources_api.upload_batch(
        _="user",
        upload_service=svc,
        settings=MagicMock(),
        files=[_file("a.txt")],
        domain=domain,
    )

    svc.upload_batch.assert_awaited_once()
    assert svc.upload_batch.call_args.kwargs["forced_domain"] is None


@pytest.mark.asyncio
async def test_upload_batch_real_domain_passes_through() -> None:
    svc = _upload_service()

    await sources_api.upload_batch(
        _="user",
        upload_service=svc,
        settings=MagicMock(),
        files=[_file("a.txt")],
        domain="legal",
    )

    assert svc.upload_batch.call_args.kwargs["forced_domain"] == "legal"
