# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Audit fix F32: orphan vision PNGs are removed on indexing failure.

When ``_run_indexing`` raises (e.g. the chunker / vision LLM blows up
mid-pipeline), the partial ``{data_dir}/databases/<db>/images/<src>/``
directory must be removed before the function re-raises. Otherwise
PNGs accumulate forever — there is no other code path that cleans
the directory until the source itself is deleted (see F32 part 2:
``test_vision_images_dir_removed_on_source_delete`` in
``tests/unit/services/graph/test_source_delete_atomic.py``).

The cleanup itself is best-effort — the underlying indexing exception
must still propagate.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_run_indexing_removes_vision_images_dir_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Crash mid-pipeline → vision images directory removed before re-raise."""
    from chaoscypher_core.operations.importing import indexing_handler

    # Pre-create the images directory with three "rendered" PNGs so we are
    # asserting the cleanup actually deleted real files (not just a no-op
    # on a non-existent directory).
    images_dir = indexing_handler.vision_images_dir(
        data_dir=tmp_path, database_name="default", source_id="src_fail"
    )
    images_dir.mkdir(parents=True, exist_ok=True)
    for n in (1, 2, 3):
        (images_dir / f"page_{n}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert images_dir.exists()
    assert sorted(p.name for p in images_dir.iterdir()) == [
        "page_1.png",
        "page_2.png",
        "page_3.png",
    ]

    # Stub the loader registry so we never touch a real file. The crash
    # is injected at the chunking step so the failure path runs after
    # vision (the realistic ordering for an LLM-emitted exception).
    fake_registry = MagicMock()
    fake_registry.load_document.return_value = [{"content": "x", "metadata": {}}]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_registry,
    )

    monkeypatch.setattr(
        indexing_handler,
        "_extract_text",
        lambda **kw: (
            "x" * 200,
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        ),
    )
    monkeypatch.setattr(
        indexing_handler,
        "_apply_vision_processing",
        AsyncMock(side_effect=lambda **kw: (kw["documents"], None)),
    )
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    # Force the chunker to explode — this is the failure that should
    # trigger orphan cleanup.
    chunking_service = MagicMock()
    chunking_service.create_chunks = AsyncMock(
        side_effect=RuntimeError("simulated chunker failure")
    )
    chunking_service.store_chunks = MagicMock()

    adapter = MagicMock()

    from chaoscypher_core.settings import EngineSettings

    # The failure-path cleanup reads its data root from
    # ``engine_settings.paths.data_dir`` (the unified engine view), so wire a
    # real EngineSettings pointing at tmp_path. ``priorities`` is app-only and
    # stays on the app settings mock.
    engine_settings = EngineSettings(current_database="default")
    engine_settings.paths.data_dir = str(tmp_path)
    settings = MagicMock()
    settings.priorities.background = 50

    with pytest.raises(RuntimeError, match="simulated chunker failure"):
        await indexing_handler._run_indexing(
            file_id="src_fail",
            file_info={"filename": "x.pdf"},
            filepath="/tmp/x.pdf",
            analysis_depth="full",
            enable_normalization=False,
            enable_vision=True,
            adapter=adapter,
            chunking_service=chunking_service,
            engine_settings=engine_settings,
            settings=settings,
            database_name="default",
        )

    # The exception still propagated AND the orphan directory is gone.
    assert not images_dir.exists(), (
        f"vision images dir was not cleaned up after indexing failure: "
        f"{images_dir} still exists with {list(images_dir.iterdir())}"
    )
    # And the lifecycle write still ran — cleanup must not interfere.
    adapter.fail_indexing.assert_called_once_with("src_fail", "simulated chunker failure")


@pytest.mark.asyncio
async def test_run_indexing_failure_no_images_dir_is_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No images dir → cleanup is a no-op (does not raise, does not log error)."""
    from chaoscypher_core.operations.importing import indexing_handler

    # Deliberately do NOT create the images directory. The handler must
    # still re-raise the underlying error and not blow up trying to clean
    # up a directory that never existed.
    images_dir = indexing_handler.vision_images_dir(
        data_dir=tmp_path, database_name="default", source_id="src_noimg"
    )
    assert not images_dir.exists()

    fake_registry = MagicMock()
    fake_registry.load_document.return_value = [{"content": "x", "metadata": {}}]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_registry,
    )

    monkeypatch.setattr(
        indexing_handler,
        "_extract_text",
        lambda **kw: (
            "x" * 200,
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        ),
    )
    monkeypatch.setattr(
        indexing_handler,
        "_apply_vision_processing",
        AsyncMock(side_effect=lambda **kw: (kw["documents"], None)),
    )
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    chunking_service = MagicMock()
    chunking_service.create_chunks = AsyncMock(side_effect=RuntimeError("boom"))
    chunking_service.store_chunks = MagicMock()

    adapter = MagicMock()
    from chaoscypher_core.settings import EngineSettings

    engine_settings = EngineSettings(current_database="default")
    engine_settings.paths.data_dir = str(tmp_path)
    settings = MagicMock()
    settings.priorities.background = 50

    with pytest.raises(RuntimeError, match="boom"):
        await indexing_handler._run_indexing(
            file_id="src_noimg",
            file_info={"filename": "x.pdf"},
            filepath="/tmp/x.pdf",
            analysis_depth="full",
            enable_normalization=False,
            enable_vision=False,
            adapter=adapter,
            chunking_service=chunking_service,
            engine_settings=engine_settings,
            settings=settings,
            database_name="default",
        )

    # Still re-raised, fail_indexing still called.
    adapter.fail_indexing.assert_called_once()


def test_cleanup_vision_images_swallows_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``shutil.rmtree`` raising OSError must be swallowed (best-effort contract)."""
    from chaoscypher_core.operations.importing import indexing_handler

    images_dir = indexing_handler.vision_images_dir(
        data_dir=tmp_path, database_name="default", source_id="src_oserror"
    )
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / "page_1.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def _boom(*args, **kwargs) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(indexing_handler.shutil, "rmtree", _boom)

    # Must NOT raise — best-effort contract.
    indexing_handler.cleanup_vision_images(
        data_dir=tmp_path, database_name="default", source_id="src_oserror"
    )
    # Directory still there because rmtree was forced to fail; the helper
    # logged a warning and returned cleanly.
    assert images_dir.exists()
