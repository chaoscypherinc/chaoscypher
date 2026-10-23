# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: the forced packs must persist on the SourceRow at upload.

``upload_file`` takes ``forced_packs`` (a list; ``[]`` the floor alone) or
the one-name ``forced_domain`` (a pack, the packs' label, ``generic``) and
hands the storage adapter the packs, which writes the list and the old
``forced_domain`` together (``pack_columns``).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.sources.management.service import SourceProcessingService


# F14: hash verification rejects mismatched content_hash. Compute the real
# hash for these stub uploads instead of passing a placeholder.
_STAGED_BODY = b"x" * 32
_STAGED_HASH = hashlib.sha256(_STAGED_BODY).hexdigest()


@pytest.mark.asyncio
async def test_forced_domain_passed_to_storage_adapter(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_upload_source(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"id": "src_test", **kwargs}

    source_manager = MagicMock()
    source_manager.upload_source.side_effect = fake_upload_source
    source_manager.find_by_content_hash.return_value = None

    settings = MagicMock()
    settings.current_database = "default"
    settings.database_dir = tmp_path
    settings.batching.max_upload_bytes = 10 * 1024 * 1024 * 1024  # 10 GB
    settings.priorities.background = 50
    config_manager = MagicMock()
    config_manager.get_settings.return_value = settings

    operations_manager = MagicMock()

    async def fake_queue(**kwargs: object) -> dict[str, str]:
        return {"task_id": "tsk_1", "status": "queued"}

    operations_manager.queue_import_indexing.side_effect = fake_queue
    validators = MagicMock()

    staged = tmp_path / "doc.pdf"
    staged.write_bytes(_STAGED_BODY)

    service = SourceProcessingService(
        source_manager=source_manager,
        operations_manager=operations_manager,
        config_manager=config_manager,
        validators=validators,
    )

    await service.upload_file(
        filename="doc.pdf",
        forced_domain="technical",
        staged_file_path=staged,
        content_hash=_STAGED_HASH,
        file_size=len(_STAGED_BODY),
    )

    assert captured["forced_packs"] == ("technical",)
    assert "forced_domain" not in captured  # one form reaches the adapter


@pytest.mark.asyncio
async def test_forced_domain_none_remains_none(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_upload_source(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"id": "src_test", **kwargs}

    source_manager = MagicMock()
    source_manager.upload_source.side_effect = fake_upload_source
    source_manager.find_by_content_hash.return_value = None

    settings = MagicMock()
    settings.current_database = "default"
    settings.database_dir = tmp_path
    settings.batching.max_upload_bytes = 10 * 1024 * 1024 * 1024  # 10 GB
    settings.priorities.background = 50
    config_manager = MagicMock()
    config_manager.get_settings.return_value = settings

    operations_manager = MagicMock()

    async def fake_queue(**kwargs: object) -> dict[str, str]:
        return {"task_id": "tsk_1", "status": "queued"}

    operations_manager.queue_import_indexing.side_effect = fake_queue
    validators = MagicMock()

    staged = tmp_path / "doc.pdf"
    staged.write_bytes(_STAGED_BODY)

    service = SourceProcessingService(
        source_manager=source_manager,
        operations_manager=operations_manager,
        config_manager=config_manager,
        validators=validators,
    )

    await service.upload_file(
        filename="doc.pdf",
        staged_file_path=staged,
        content_hash=_STAGED_HASH,
        file_size=len(_STAGED_BODY),
    )

    assert captured["forced_packs"] is None
    assert captured["confirmation_required"] is True  # nothing forced: the gate engages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "forced"),
    [
        ({"forced_packs": ["literary", "historical"]}, ("literary", "historical")),
        ({"forced_packs": []}, ()),
        ({"forced_domain": "generic"}, ()),  # the floor alone, chosen
        ({"forced_domain": "literary+historical"}, ("literary", "historical")),  # in its order
    ],
)
async def test_forced_packs_reach_the_storage_adapter(
    tmp_path: Path, kwargs: dict[str, object], forced: tuple[str, ...]
) -> None:
    captured: dict[str, object] = {}

    def fake_upload_source(**kw: object) -> dict[str, object]:
        captured.update(kw)
        return {"id": "src_test", **kw}

    source_manager = MagicMock()
    source_manager.upload_source.side_effect = fake_upload_source
    source_manager.find_by_content_hash.return_value = None
    settings = MagicMock()
    settings.current_database = "default"
    settings.database_dir = tmp_path
    settings.batching.max_upload_bytes = 10 * 1024 * 1024 * 1024
    settings.priorities.background = 50
    config_manager = MagicMock()
    config_manager.get_settings.return_value = settings
    operations_manager = MagicMock()

    async def fake_queue(**_kw: object) -> dict[str, str]:
        return {"task_id": "tsk_1", "status": "queued"}

    operations_manager.queue_import_indexing.side_effect = fake_queue
    staged = tmp_path / "doc.pdf"
    staged.write_bytes(_STAGED_BODY)
    service = SourceProcessingService(
        source_manager=source_manager,
        operations_manager=operations_manager,
        config_manager=config_manager,
        validators=MagicMock(),
    )

    await service.upload_file(
        filename="doc.pdf",
        staged_file_path=staged,
        content_hash=_STAGED_HASH,
        file_size=len(_STAGED_BODY),
        **kwargs,  # type: ignore[arg-type]
    )

    assert captured["forced_packs"] == forced
    assert captured["confirmation_required"] is False  # a choice, never parked
