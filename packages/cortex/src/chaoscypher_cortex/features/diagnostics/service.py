# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Diagnostics Service.

Combines core DiagnosticCollector with container-specific data
to produce a comprehensive diagnostic ZIP bundle.
"""

import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import structlog

from chaoscypher_core.services.diagnostics import DiagnosticCollector


logger = structlog.get_logger(__name__)


class DiagnosticsService:
    """Produces diagnostic ZIP bundles for bug reports.

    Wraps the core DiagnosticCollector and enriches with container-specific
    data (queue stats, service status).

    Args:
        data_dir: Root data directory path.
        log_dir: Path to the service log directory.
        database_name: Current active database name.
        settings_dict: Sanitizable settings dict.
        queue_client: Async Valkey client (None if unavailable).
        log_service: LogService for supervisord status (None if unavailable).
    """

    def __init__(
        self,
        data_dir: str,
        log_dir: str,
        database_name: str,
        settings_dict: dict[str, Any],
        queue_client: Any | None = None,
        log_service: Any | None = None,
    ) -> None:
        """Initialize with data paths and optional container services.

        Args:
            data_dir: Root data directory path.
            log_dir: Path to the service log directory.
            database_name: Current active database name.
            settings_dict: Sanitizable settings dict.
            queue_client: Async Valkey client (None if unavailable).
            log_service: LogService for supervisord status (None if unavailable).
        """
        self._data_dir = data_dir
        self._log_dir = log_dir
        self._database_name = database_name
        self._settings_dict = settings_dict
        self._queue_client = queue_client
        self._log_service = log_service

    async def create_bundle(self) -> Path:
        """Create a diagnostic ZIP bundle.

        Returns:
            Path to the created ZIP file in a temporary directory.
        """
        db_file = Path(os.path.join(self._data_dir, "databases", self._database_name, "app.db"))
        db_path = db_file if db_file.is_file() else None  # noqa: ASYNC240

        collector = DiagnosticCollector(
            db_path=db_path,
            log_dir=Path(self._log_dir),
        )

        # Enrich with container-specific data
        queue_stats = await self._collect_queue_stats()
        service_status = self._collect_service_status()

        # Write base bundle
        tmp_dir = tempfile.mkdtemp(prefix="chaoscypher-diag-")
        output_path = Path(tmp_dir) / "chaoscypher-diagnostics.zip"
        collector.export_bundle(output_path, settings=self._settings_dict)

        # Append enriched data to existing ZIP
        if queue_stats or service_status:
            with zipfile.ZipFile(output_path, "a") as zf:
                if queue_stats:
                    zf.writestr(
                        "queue_stats.json",
                        json.dumps(queue_stats, indent=2, default=str),
                    )
                if service_status:
                    zf.writestr(
                        "services.json",
                        json.dumps(service_status, indent=2, default=str),
                    )

        logger.info("diagnostic_bundle_created", path=str(output_path))
        return output_path

    async def _collect_queue_stats(self) -> dict[str, Any] | None:
        """Gather queue statistics from Valkey.

        Returns:
            Queue stats dict, or None if unavailable.
        """
        if not self._queue_client:
            return None

        try:
            info = await self._queue_client.info()
            return {
                "connected_clients": info.get("connected_clients"),
                "used_memory_human": info.get("used_memory_human"),
                "total_commands_processed": info.get("total_commands_processed"),
                "uptime_in_seconds": info.get("uptime_in_seconds"),
            }
        except Exception:
            logger.warning("queue_stats_failed")
            return None

    def _collect_service_status(self) -> list[dict[str, Any]] | None:
        """Gather service status from supervisord via LogService.

        Returns:
            List of service status dicts, or None if unavailable.
        """
        if not self._log_service:
            return None

        try:
            response = self._log_service.get_service_status()
            if not response.available:
                return None
            return [s.model_dump() for s in response.services]
        except Exception:
            logger.warning("service_status_failed")
            return None
