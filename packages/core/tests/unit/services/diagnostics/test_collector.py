# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for DiagnosticCollector service."""

import json
import zipfile
from pathlib import Path

import pytest


class TestCollectSystemInfo:
    """Tests for system info collection."""

    def test_returns_system_info(self) -> None:
        """Verify collect_system_info returns a populated SystemInfo."""
        from chaoscypher_core.services.diagnostics.collector import DiagnosticCollector
        from chaoscypher_core.services.diagnostics.models import SystemInfo

        collector = DiagnosticCollector()
        info = collector.collect_system_info()
        assert isinstance(info, SystemInfo)
        assert info.python_version
        assert info.platform

    def test_includes_installed_packages(self) -> None:
        """Verify pydantic appears in discovered packages."""
        from chaoscypher_core.services.diagnostics.collector import DiagnosticCollector

        collector = DiagnosticCollector()
        info = collector.collect_system_info()
        assert "pydantic" in info.packages


class TestSanitizeSettings:
    """Tests for sanitized settings collection."""

    def test_masks_secrets(self) -> None:
        """Verify secret keys are replaced with boolean-style 'configured'."""
        from chaoscypher_core.services.diagnostics.collector import DiagnosticCollector

        collector = DiagnosticCollector()
        settings = {
            "name": "test",
            "api_key": "sk-secret123",
            "nested": {
                "password": "hunter2",
                "host": "localhost",
            },
        }
        sanitized = collector.sanitize_settings(settings)
        assert sanitized["name"] == "test"
        # Must not leak any portion of the real secret value
        assert sanitized["api_key"] == "configured"
        assert "sk-" not in str(sanitized["api_key"])
        assert sanitized["nested"]["password"] == "configured"
        assert sanitized["nested"]["host"] == "localhost"

    def test_handles_empty_settings(self) -> None:
        """Verify sanitize_settings works with empty dict."""
        from chaoscypher_core.services.diagnostics.collector import DiagnosticCollector

        collector = DiagnosticCollector()
        assert collector.sanitize_settings({}) == {}


class TestScrubLogLine:
    """Tests for the _scrub_log_line helper."""

    def test_scrubs_authorization_bearer(self) -> None:
        """Bearer token in Authorization header is replaced with ***."""
        from chaoscypher_core.services.diagnostics.collector import _scrub_log_line

        line = "2026-04-26 INFO endpoint_call authorization: Bearer cc_live_supersecret"
        result = _scrub_log_line(line)
        assert "cc_live_supersecret" not in result
        assert "Bearer ***" in result

    def test_scrubs_api_key_query_param(self) -> None:
        """api_key in URL query string is replaced with ***."""
        from chaoscypher_core.services.diagnostics.collector import _scrub_log_line

        line = "2026-04-26 INFO outbound_call ?api_key=sk-realsecret"
        result = _scrub_log_line(line)
        assert "sk-realsecret" not in result
        assert "***" in result

    def test_scrubs_api_key_structured_field(self) -> None:
        """api_key as a structured key=value field is replaced with ***."""
        from chaoscypher_core.services.diagnostics.collector import _scrub_log_line

        line = "llm_request api_key=sk-abc123xyz provider=openai"
        result = _scrub_log_line(line)
        assert "sk-abc123xyz" not in result
        assert "***" in result
        assert "provider=openai" in result

    def test_scrubs_token_field(self) -> None:
        """Long token values in key=value form are replaced with ***."""
        from chaoscypher_core.services.diagnostics.collector import _scrub_log_line

        line = "auth_check token=eyJhbGciOiJIUzI1NiJ9.payload.signature"
        result = _scrub_log_line(line)
        assert "eyJhbGciOiJIUzI1NiJ9.payload.signature" not in result
        assert "***" in result

    def test_does_not_scrub_tokenizer_word(self) -> None:
        """The word 'tokenizer' should not be affected by the token pattern."""
        from chaoscypher_core.services.diagnostics.collector import _scrub_log_line

        line = "embedding_init tokenizer=cl100k_base model=text-embedding-3-small"
        result = _scrub_log_line(line)
        # Should be unchanged — "tokenizer" is not a secret key
        assert result == line

    def test_leaves_safe_lines_unchanged(self) -> None:
        """Log lines with no credentials pass through unmodified."""
        from chaoscypher_core.services.diagnostics.collector import _scrub_log_line

        line = "2026-04-26 INFO worker_started queue=operations concurrency=8"
        assert _scrub_log_line(line) == line

    def test_short_token_values_not_scrubbed(self) -> None:
        """Token values shorter than 16 chars are not scrubbed (avoids false positives)."""
        from chaoscypher_core.services.diagnostics.collector import _scrub_log_line

        line = "cfg_loaded token=short"
        result = _scrub_log_line(line)
        assert result == line


class TestCollectLogs:
    """Tests for log file collection."""

    def test_reads_log_files(self, tmp_path: Path) -> None:
        """Verify log files are read and keyed by service name."""
        from chaoscypher_core.services.diagnostics.collector import DiagnosticCollector

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "cortex.log").write_text("INFO Started\nINFO Ready\n")
        (log_dir / "neuron.log").write_text("INFO Worker started\n")

        collector = DiagnosticCollector(log_dir=log_dir)
        logs = collector.collect_logs()
        assert "cortex" in logs
        assert "INFO Started" in logs["cortex"]
        assert "neuron" in logs

    def test_no_log_dir_returns_empty(self) -> None:
        """Verify missing log_dir returns empty dict."""
        from chaoscypher_core.services.diagnostics.collector import DiagnosticCollector

        collector = DiagnosticCollector(log_dir=Path("/nonexistent"))
        logs = collector.collect_logs()
        assert logs == {}

    def test_skips_non_log_files(self, tmp_path: Path) -> None:
        """Verify non-.log files are not included."""
        from chaoscypher_core.services.diagnostics.collector import DiagnosticCollector

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "cortex.log").write_text("log data")
        (log_dir / "readme.txt").write_text("not a log")

        collector = DiagnosticCollector(log_dir=log_dir)
        logs = collector.collect_logs()
        assert "cortex" in logs
        assert "readme" not in logs

    def test_includes_rotated_log_files(self, tmp_path: Path) -> None:
        """Verify rotated log files (e.g. cortex.log.1) are included."""
        from chaoscypher_core.services.diagnostics.collector import DiagnosticCollector

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "cortex.log").write_text("current log")
        (log_dir / "cortex.log.1").write_text("previous log")

        collector = DiagnosticCollector(log_dir=log_dir)
        logs = collector.collect_logs()
        assert "cortex" in logs
        assert "cortex.1" in logs


class TestExportBundle:
    """Tests for ZIP bundle export."""

    def test_creates_zip_file(self, tmp_path: Path) -> None:
        """Verify export_bundle creates a valid ZIP with expected entries."""
        from chaoscypher_core.services.diagnostics.collector import DiagnosticCollector

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "cortex.log").write_text("INFO test")

        collector = DiagnosticCollector(log_dir=log_dir)
        output = tmp_path / "bundle.zip"
        result = collector.export_bundle(output)

        assert result == output
        assert output.exists()

        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
            assert "system_info.json" in names
            assert "settings.json" in names
            assert "logs/cortex.log" in names

    def test_system_info_json_is_valid(self, tmp_path: Path) -> None:
        """Verify system_info.json in the ZIP is valid JSON."""
        from chaoscypher_core.services.diagnostics.collector import DiagnosticCollector

        collector = DiagnosticCollector()
        output = tmp_path / "bundle.zip"
        collector.export_bundle(output)

        with zipfile.ZipFile(output) as zf:
            data = json.loads(zf.read("system_info.json"))
            assert "python_version" in data
            assert "platform" in data

    @pytest.mark.unit
    def test_export_zip_scrubs_authorization_headers(self, tmp_path: Path) -> None:
        """Log files in the ZIP must not contain raw Bearer tokens or api_key values."""
        from chaoscypher_core.services.diagnostics.collector import DiagnosticCollector

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_path = log_dir / "cortex.log"
        log_path.write_text(
            "2026-04-26 INFO endpoint_call authorization: Bearer cc_live_supersecret\n"
            "2026-04-26 INFO outbound_call ?api_key=sk-realsecret\n"
            "2026-04-26 INFO worker_started queue=operations concurrency=8\n"
        )

        collector = DiagnosticCollector(log_dir=log_dir)
        output = tmp_path / "bundle.zip"
        collector.export_bundle(output)

        with zipfile.ZipFile(output) as zf:
            body = zf.read("logs/cortex.log").decode()

        assert "cc_live_supersecret" not in body
        assert "sk-realsecret" not in body
        assert "***" in body
        # Safe content must be preserved
        assert "worker_started" in body


class TestCollectDatabaseStats:
    """Tests for database-stats collection."""

    def test_returns_counts_for_real_db(self, tmp_path: Path) -> None:
        """A real DB yields table counts without error."""
        import sqlite3

        from chaoscypher_core.services.diagnostics.collector import DiagnosticCollector

        db_dir = tmp_path / "default"
        db_dir.mkdir()
        db_path = db_dir / "app.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO widgets (id) VALUES (1), (2)")
        conn.commit()
        conn.close()

        stats = DiagnosticCollector(db_path=db_path).collect_database_stats()
        assert stats.table_counts == {"widgets": 2}

    def test_closes_connection_when_query_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: the sqlite connection must be closed even when a query
        raises — the old code only closed it on the success path and leaked
        the connection on any failure.
        """
        import sqlite3

        from chaoscypher_core.services.diagnostics.collector import DiagnosticCollector

        db_dir = tmp_path / "default"
        db_dir.mkdir()
        db_path = db_dir / "app.db"
        db_path.write_bytes(b"")  # must exist so stats collection is attempted

        closed = {"count": 0}

        class _FakeConn:
            def execute(self, *_args: object) -> object:
                raise sqlite3.OperationalError("boom")

            def close(self) -> None:
                closed["count"] += 1

        monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_kw: _FakeConn())

        # Failure is swallowed into a warning; the return is still well-formed.
        stats = DiagnosticCollector(db_path=db_path).collect_database_stats()
        assert stats.table_counts == {}
        # The connection must have been closed exactly once despite the error.
        assert closed["count"] == 1
