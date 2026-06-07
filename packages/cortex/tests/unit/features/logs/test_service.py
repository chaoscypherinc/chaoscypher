# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for LogService."""

from pathlib import Path


class TestGetLogs:
    """Tests for single-service log retrieval."""

    def test_returns_log_lines(self, tmp_path: Path) -> None:
        """Verify log lines are read from a service log file."""
        from chaoscypher_cortex.features.logs.service import LogService

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "cortex.log").write_text("line1\nline2\nline3\n")

        service = LogService(log_dir=str(log_dir))
        response = service.get_logs("cortex", lines=10)
        assert response.service == "cortex"
        assert len(response.lines) == 3
        assert response.lines[0] == "line1"

    def test_tail_limits_lines(self, tmp_path: Path) -> None:
        """Verify only the last N lines are returned."""
        from chaoscypher_cortex.features.logs.service import LogService

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        content = "\n".join(f"line{i}" for i in range(100)) + "\n"
        (log_dir / "cortex.log").write_text(content)

        service = LogService(log_dir=str(log_dir))
        response = service.get_logs("cortex", lines=10)
        assert len(response.lines) == 10
        assert response.lines[-1] == "line99"

    def test_unknown_service_returns_empty(self, tmp_path: Path) -> None:
        """Verify unknown service name returns empty response."""
        from chaoscypher_cortex.features.logs.service import LogService

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        service = LogService(log_dir=str(log_dir))
        response = service.get_logs("nonexistent", lines=10)
        assert response.lines == []
        assert response.total_lines == 0


class TestGetAllLogs:
    """Tests for merged log retrieval."""

    def test_merges_services_sorted_by_timestamp(self, tmp_path: Path) -> None:
        """Verify logs from multiple services are interleaved by timestamp."""
        from chaoscypher_cortex.features.logs.service import LogService

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "cortex.log").write_text(
            "2026-04-03T14:00:01 INFO cortex started\n2026-04-03T14:00:03 INFO cortex ready\n"
        )
        (log_dir / "neuron.log").write_text("2026-04-03T14:00:02 INFO neuron started\n")

        service = LogService(log_dir=str(log_dir))
        response = service.get_all_logs(lines=10)
        assert response.service is None
        assert len(response.lines) == 3
        assert "cortex started" in response.lines[0]
        assert "neuron started" in response.lines[1]
        assert "cortex ready" in response.lines[2]

    def test_fallback_when_timestamps_unparseable(self, tmp_path: Path) -> None:
        """Verify logs without timestamps don't cause errors."""
        from chaoscypher_cortex.features.logs.service import LogService

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "cortex.log").write_text("no timestamp here\n")
        (log_dir / "neuron.log").write_text("also no timestamp\n")

        service = LogService(log_dir=str(log_dir))
        response = service.get_all_logs(lines=10)
        assert len(response.lines) == 2


class TestGetServiceStatus:
    """Tests for supervisord status retrieval."""

    def test_returns_unavailable_when_no_socket(self, tmp_path: Path) -> None:
        """Verify graceful fallback when supervisord socket doesn't exist."""
        from chaoscypher_cortex.features.logs.service import LogService

        service = LogService(
            log_dir=str(tmp_path),
            supervisor_socket="/nonexistent/supervisor.sock",
            supervisor_password="test",
        )
        response = service.get_service_status()
        assert response.available is False
        assert response.services == []
