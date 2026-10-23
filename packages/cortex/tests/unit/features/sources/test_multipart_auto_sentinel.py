# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Multipart upload normalizes the ``__auto__`` domain sentinel to None, and takes ``packs``.

A client posting ``domain=__auto__`` (the UI's "auto-detect" option value)
must NOT pin a forced ``'__auto__'`` — that bypasses the confirmation gate
(forced packs proceed) and forces a bogus domain. The URL-import path reads
the same fields the same way (``features.sources.packs.forced_packs``); these
tests pin it for the multipart ``/sources`` and ``/sources/batch`` routes:
``domain`` is the single-select UI's one-element form, ``packs`` the list.
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
    """``__auto__`` / empty / None all forward forced_packs=None (gate engages)."""
    svc = _upload_service()

    await sources_api.upload_file(
        _="user",
        upload_service=svc,
        settings=MagicMock(),
        file=_file(),
        domain=domain,
        packs=None,
    )

    svc.upload_single.assert_awaited_once()
    assert svc.upload_single.call_args.kwargs["forced_packs"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("domain", "packs", "forced"),
    [
        ("medical", None, ("medical",)),  # the single-select UI's form
        ("generic", None, ()),  # the floor alone
        (None, ["literary", "historical"], ("literary", "historical")),  # the order given
        (None, ["__auto__"], None),
    ],
)
async def test_upload_single_packs_pass_through(
    domain: str | None, packs: list[str] | None, forced: tuple[str, ...] | None
) -> None:
    """A real domain is NOT normalized away; ``packs`` is the list form."""
    svc = _upload_service()

    await sources_api.upload_file(
        _="user",
        upload_service=svc,
        settings=MagicMock(),
        file=_file(),
        domain=domain,
        packs=packs,
    )

    assert svc.upload_single.call_args.kwargs["forced_packs"] == forced


@pytest.mark.asyncio
async def test_upload_naming_the_packs_twice_is_refused() -> None:
    """``domain`` and ``packs`` together are a 400 (core ValidationError), nothing uploaded."""
    from chaoscypher_core.exceptions import ValidationError

    svc = _upload_service()
    with pytest.raises(ValidationError, match="not both"):
        await sources_api.upload_file(
            _="user",
            upload_service=svc,
            settings=MagicMock(),
            file=_file(),
            domain="legal",
            packs=["medical"],
        )
    svc.upload_single.assert_not_awaited()


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
        packs=None,
    )

    svc.upload_batch.assert_awaited_once()
    assert svc.upload_batch.call_args.kwargs["forced_packs"] is None


@pytest.mark.asyncio
async def test_upload_batch_real_domain_passes_through() -> None:
    svc = _upload_service()

    await sources_api.upload_batch(
        _="user",
        upload_service=svc,
        settings=MagicMock(),
        files=[_file("a.txt")],
        domain="legal",
        packs=None,
    )

    assert svc.upload_batch.call_args.kwargs["forced_packs"] == ("legal",)


@pytest.mark.asyncio
async def test_upload_batch_takes_the_packs_list() -> None:
    svc = _upload_service()

    await sources_api.upload_batch(
        _="user",
        upload_service=svc,
        settings=MagicMock(),
        files=[_file("a.txt")],
        domain=None,
        packs=["literary", "historical"],
    )

    assert svc.upload_batch.call_args.kwargs["forced_packs"] == ("literary", "historical")
