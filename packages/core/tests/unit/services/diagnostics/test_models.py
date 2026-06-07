# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for diagnostic report models."""

from datetime import UTC, datetime


class TestSystemInfo:
    """Tests for SystemInfo model."""

    def test_create_system_info(self) -> None:
        """Verify SystemInfo creation with all fields."""
        from chaoscypher_core.services.diagnostics.models import SystemInfo

        info = SystemInfo(
            chaoscypher_version="0.1.0",
            python_version="3.14.0",
            platform="Linux-6.1-x86_64",
            packages={"chaoscypher-core": "0.1.0"},
        )
        assert info.chaoscypher_version == "0.1.0"
        assert info.python_version == "3.14.0"
        assert info.packages == {"chaoscypher-core": "0.1.0"}


class TestDiagnosticDatabaseStats:
    """Tests for DiagnosticDatabaseStats model."""

    def test_create_database_stats(self) -> None:
        """Verify DiagnosticDatabaseStats creation with all fields."""
        from chaoscypher_core.services.diagnostics.models import DiagnosticDatabaseStats

        stats = DiagnosticDatabaseStats(
            database_name="default",
            file_size_bytes=1024000,
            table_counts={"nodes": 100, "edges": 200},
        )
        assert stats.database_name == "default"
        assert stats.file_size_bytes == 1024000
        assert stats.table_counts["nodes"] == 100

    def test_database_stats_optional_fields(self) -> None:
        """Verify DiagnosticDatabaseStats works with only required fields."""
        from chaoscypher_core.services.diagnostics.models import DiagnosticDatabaseStats

        stats = DiagnosticDatabaseStats(database_name="test")
        assert stats.file_size_bytes is None
        assert stats.table_counts == {}


class TestDiagnosticReport:
    """Tests for DiagnosticReport model."""

    def test_create_full_report(self) -> None:
        """Verify DiagnosticReport creation with all sections."""
        from chaoscypher_core.services.diagnostics.models import (
            DiagnosticDatabaseStats,
            DiagnosticReport,
            SystemInfo,
        )

        report = DiagnosticReport(
            timestamp=datetime(2026, 4, 3, 14, 0, 0, tzinfo=UTC),
            system=SystemInfo(
                chaoscypher_version="0.1.0",
                python_version="3.14.0",
                platform="Linux",
                packages={},
            ),
            database=DiagnosticDatabaseStats(database_name="default"),
            settings={"llm": {"chat_provider": "ollama"}},
            logs={"cortex": "INFO Started"},
        )
        assert report.system.chaoscypher_version == "0.1.0"
        assert report.database.database_name == "default"
        assert report.logs["cortex"] == "INFO Started"
        assert report.queue is None
        assert report.services is None
