# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Diagnostics bundle creation must not block the event loop or leak temp dirs.

``create_bundle`` ran ``export_bundle``/``zipfile`` inline in an async
method (blocking the loop for the whole DB-stat + log-read + zip write),
and ``tempfile.mkdtemp`` had no failure-path cleanup — the API's
BackgroundTask only removes the dir after a *successful* FileResponse.
"""

import asyncio
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cortex.features.diagnostics.service import DiagnosticsService


_SETTINGS_DICT: dict[str, object] = {"provider": "ollama"}


def _make_service(tmp_path: Path) -> DiagnosticsService:
    """Return a DiagnosticsService wired to a temp directory."""
    return DiagnosticsService(
        data_dir=str(tmp_path),
        log_dir=str(tmp_path / "logs"),
        database_name="default",
        settings_dict=_SETTINGS_DICT,
    )


def _write_stub_zip(output_path: Path, **_kwargs: object) -> None:
    """Write a valid but empty ZIP to *output_path*."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w"):
        pass


@pytest.mark.unit
class TestCreateBundleOffload:
    """Blocking disk work runs via asyncio.to_thread."""

    @pytest.mark.asyncio
    async def test_bundle_write_offloaded_to_thread(self, tmp_path: Path) -> None:
        """export_bundle/zip write happen through asyncio.to_thread."""
        service = _make_service(tmp_path)
        mock_collector = MagicMock()
        mock_collector.export_bundle.side_effect = _write_stub_zip

        real_to_thread = asyncio.to_thread
        to_thread_spy = AsyncMock(side_effect=real_to_thread)

        with (
            patch(
                "chaoscypher_cortex.features.diagnostics.service.DiagnosticCollector",
                return_value=mock_collector,
            ),
            patch("asyncio.to_thread", to_thread_spy),
        ):
            result = await service.create_bundle()

        assert to_thread_spy.await_count >= 1
        mock_collector.export_bundle.assert_called_once()
        assert result.exists()
        assert result.suffix == ".zip"


@pytest.mark.unit
class TestCreateBundleTempDirCleanup:
    """A failing export must not leak its temp directory."""

    @pytest.mark.asyncio
    async def test_failed_export_removes_temp_dir(self, tmp_path: Path) -> None:
        """export_bundle raising cleans up the mkdtemp directory."""
        service = _make_service(tmp_path)
        mock_collector = MagicMock()
        mock_collector.export_bundle.side_effect = OSError("disk full")

        scratch = tmp_path / "scratch"
        scratch.mkdir()

        # Capture the real function — the patch below replaces the module
        # attribute, so calling tempfile.mkdtemp inside would recurse.
        real_mkdtemp = tempfile.mkdtemp

        def _mkdtemp_in_scratch(prefix: str) -> str:
            return real_mkdtemp(prefix=prefix, dir=scratch)

        with (
            patch(
                "chaoscypher_cortex.features.diagnostics.service.DiagnosticCollector",
                return_value=mock_collector,
            ),
            patch(
                "chaoscypher_cortex.features.diagnostics.service.tempfile.mkdtemp",
                side_effect=_mkdtemp_in_scratch,
            ),
        ):
            with pytest.raises(OSError, match="disk full"):
                await service.create_bundle()

        # The temp dir created for the bundle is gone.
        assert list(scratch.iterdir()) == []
