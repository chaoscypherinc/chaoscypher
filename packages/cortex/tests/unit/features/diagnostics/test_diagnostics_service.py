# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for DiagnosticsService.

Covers bundle creation (with and without optional services) and the
helper methods _collect_queue_stats / _collect_service_status.
"""

import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cortex.features.diagnostics.service import DiagnosticsService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SETTINGS_DICT: dict[str, object] = {"provider": "ollama", "debug": False}


def _make_service(
    tmp_path: Path,
    *,
    queue_client: object | None = None,
    log_service: object | None = None,
) -> DiagnosticsService:
    """Return a DiagnosticsService wired to a temp directory."""
    return DiagnosticsService(
        data_dir=str(tmp_path),
        log_dir=str(tmp_path / "logs"),
        database_name="default",
        settings_dict=_SETTINGS_DICT,
        queue_client=queue_client,
        log_service=log_service,
    )


def _patch_collector(export_side_effect: object = None) -> MagicMock:
    """Return a MagicMock for DiagnosticCollector."""
    mock_collector = MagicMock()
    if export_side_effect:
        mock_collector.export_bundle.side_effect = export_side_effect
    else:
        # Simulate writing a minimal ZIP so subsequent ZipFile("a") works.
        mock_collector.export_bundle.side_effect = _write_stub_zip
    return mock_collector


def _write_stub_zip(output_path: Path, **_kwargs: object) -> None:
    """Write a valid but empty ZIP to *output_path*."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w"):
        pass


# ---------------------------------------------------------------------------
# create_bundle tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateBundle:
    """Tests for DiagnosticsService.create_bundle."""

    @pytest.mark.asyncio
    async def test_returns_path_to_zip_file(self, tmp_path: Path) -> None:
        """create_bundle returns a Path that points to an existing ZIP file."""
        service = _make_service(tmp_path)
        mock_collector = _patch_collector()

        with patch(
            "chaoscypher_cortex.features.diagnostics.service.DiagnosticCollector",
            return_value=mock_collector,
        ):
            result = await service.create_bundle()

        assert isinstance(result, Path)
        assert result.exists()
        assert result.suffix == ".zip"

    @pytest.mark.asyncio
    async def test_bundle_includes_queue_stats_when_client_provided(self, tmp_path: Path) -> None:
        """create_bundle appends queue_stats.json when a queue client is given."""
        mock_client = AsyncMock()
        mock_client.info = AsyncMock(
            return_value={
                "connected_clients": 1,
                "used_memory_human": "1M",
                "total_commands_processed": 100,
                "uptime_in_seconds": 3600,
            }
        )

        service = _make_service(tmp_path, queue_client=mock_client)
        mock_collector = _patch_collector()

        with patch(
            "chaoscypher_cortex.features.diagnostics.service.DiagnosticCollector",
            return_value=mock_collector,
        ):
            result = await service.create_bundle()

        with zipfile.ZipFile(result) as zf:
            assert "queue_stats.json" in zf.namelist()

    @pytest.mark.asyncio
    async def test_bundle_includes_services_json_when_log_service_provided(
        self, tmp_path: Path
    ) -> None:
        """create_bundle appends services.json when a log_service is given."""
        mock_status = MagicMock()
        mock_status.available = True
        mock_service_item = MagicMock()
        mock_service_item.model_dump.return_value = {"name": "cortex", "status": "RUNNING"}
        mock_status.services = [mock_service_item]

        mock_log_service = MagicMock()
        mock_log_service.get_service_status.return_value = mock_status

        service = _make_service(tmp_path, log_service=mock_log_service)
        mock_collector = _patch_collector()

        with patch(
            "chaoscypher_cortex.features.diagnostics.service.DiagnosticCollector",
            return_value=mock_collector,
        ):
            result = await service.create_bundle()

        with zipfile.ZipFile(result) as zf:
            assert "services.json" in zf.namelist()

    @pytest.mark.asyncio
    async def test_bundle_works_when_both_optional_services_are_none(self, tmp_path: Path) -> None:
        """create_bundle succeeds and returns a valid ZIP when no optional services."""
        service = _make_service(tmp_path)
        mock_collector = _patch_collector()

        with patch(
            "chaoscypher_cortex.features.diagnostics.service.DiagnosticCollector",
            return_value=mock_collector,
        ):
            result = await service.create_bundle()

        assert result.exists()
        with zipfile.ZipFile(result) as zf:
            # Neither optional file should be present
            assert "queue_stats.json" not in zf.namelist()
            assert "services.json" not in zf.namelist()


# ---------------------------------------------------------------------------
# _collect_queue_stats tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCollectQueueStats:
    """Tests for DiagnosticsService._collect_queue_stats."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_client(self, tmp_path: Path) -> None:
        """_collect_queue_stats returns None when queue_client is not set."""
        service = _make_service(tmp_path)
        result = await service._collect_queue_stats()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_stats_dict_from_client(self, tmp_path: Path) -> None:
        """_collect_queue_stats returns a dict with the expected keys."""
        mock_client = AsyncMock()
        mock_client.info = AsyncMock(
            return_value={
                "connected_clients": 2,
                "used_memory_human": "4M",
                "total_commands_processed": 999,
                "uptime_in_seconds": 7200,
                "extra_key": "ignored",
            }
        )

        service = _make_service(tmp_path, queue_client=mock_client)
        result = await service._collect_queue_stats()

        assert result is not None
        assert result["connected_clients"] == 2
        assert result["used_memory_human"] == "4M"
        assert result["total_commands_processed"] == 999
        assert result["uptime_in_seconds"] == 7200
        assert "extra_key" not in result

    @pytest.mark.asyncio
    async def test_returns_none_when_client_raises(self, tmp_path: Path) -> None:
        """_collect_queue_stats returns None and does not re-raise on client error."""
        mock_client = AsyncMock()
        mock_client.info = AsyncMock(side_effect=ConnectionError("refused"))

        service = _make_service(tmp_path, queue_client=mock_client)
        result = await service._collect_queue_stats()

        assert result is None


# ---------------------------------------------------------------------------
# _collect_service_status tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCollectServiceStatus:
    """Tests for DiagnosticsService._collect_service_status."""

    def test_returns_none_when_no_log_service(self, tmp_path: Path) -> None:
        """_collect_service_status returns None when log_service is not set."""
        service = _make_service(tmp_path)
        result = service._collect_service_status()
        assert result is None

    def test_returns_none_when_supervisord_unavailable(self, tmp_path: Path) -> None:
        """_collect_service_status returns None when supervisord reports unavailable."""
        mock_response = MagicMock()
        mock_response.available = False
        mock_response.services = []

        mock_log_service = MagicMock()
        mock_log_service.get_service_status.return_value = mock_response

        service = _make_service(tmp_path, log_service=mock_log_service)
        result = service._collect_service_status()

        assert result is None

    def test_returns_list_of_dicts_when_available(self, tmp_path: Path) -> None:
        """_collect_service_status returns list of model_dump dicts when available."""
        svc_item = MagicMock()
        svc_item.model_dump.return_value = {"name": "neuron", "status": "RUNNING"}

        mock_response = MagicMock()
        mock_response.available = True
        mock_response.services = [svc_item]

        mock_log_service = MagicMock()
        mock_log_service.get_service_status.return_value = mock_response

        service = _make_service(tmp_path, log_service=mock_log_service)
        result = service._collect_service_status()

        assert result == [{"name": "neuron", "status": "RUNNING"}]

    def test_returns_none_when_log_service_raises(self, tmp_path: Path) -> None:
        """_collect_service_status returns None and does not re-raise on error."""
        mock_log_service = MagicMock()
        mock_log_service.get_service_status.side_effect = RuntimeError("socket error")

        service = _make_service(tmp_path, log_service=mock_log_service)
        result = service._collect_service_status()

        assert result is None
