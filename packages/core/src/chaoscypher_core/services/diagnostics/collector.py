# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Diagnostic Collector Service.

Gathers system diagnostics for bug reports and troubleshooting.
Framework-agnostic — used by both Cortex API and CLI.
"""

import contextlib
import importlib.metadata
import json
import platform
import re
import zipfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 - used at runtime
from typing import Any

import structlog

from chaoscypher_core.app_config import _DEFAULT_SECRET_KEYS, mask_secret_value
from chaoscypher_core.services.diagnostics.models import (
    DiagnosticDatabaseStats,
    DiagnosticReport,
    SystemInfo,
)


logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Table name validation
# ---------------------------------------------------------------------------

_VALID_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


# ---------------------------------------------------------------------------
# Log-line scrubbing — applied before including log content in ZIP exports
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[re.Pattern[str]] = [
    # HTTP Authorization header: "Authorization: Bearer TOKEN" or key=value form
    re.compile(r"(authorization\s*[:=]\s*Bearer\s+)(\S+)", re.IGNORECASE),
    # api_key / api-key as query param or structured field (word boundary prevents
    # matching "xapi_keyring", etc.)
    re.compile(r"(\bapi[_-]?key\s*[:=]\s*)([\w.\-+/=]+)", re.IGNORECASE),
    # URL query string: ?api_key=VALUE
    re.compile(r"(\?api_key=)([\w.\-+/=]+)", re.IGNORECASE),
    # token as a standalone keyword (word boundary avoids "tokenizer", "tokenization")
    re.compile(r"(\btoken\s*[:=]\s*)([\w.\-+/=]{16,})", re.IGNORECASE),
]


def _scrub_log_line(line: str) -> str:
    """Mask common credential patterns before including a log line in a diagnostic export."""
    for pat in _SECRET_PATTERNS:
        line = pat.sub(r"\1***", line)
    return line


class DiagnosticCollector:
    """Gathers system diagnostics for bug reports.

    Args:
        db_path: Path to the database file. If None, database stats are skipped.
        log_dir: Path to the log directory. If None, logs are skipped.
    """

    def __init__(
        self,
        db_path: Path | None = None,
        log_dir: Path | None = None,
    ) -> None:
        """Initialize collector with optional paths.

        Args:
            db_path: Path to the SQLite database file.
            log_dir: Path to the directory containing service log files.
        """
        self._db_path = db_path
        self._log_dir = log_dir

    def collect(self, settings: dict[str, Any] | None = None) -> DiagnosticReport:
        """Collect all available diagnostics.

        Args:
            settings: Raw settings dict to sanitize and include.

        Returns:
            Complete diagnostic report.
        """
        return DiagnosticReport(
            timestamp=datetime.now(UTC),
            system=self.collect_system_info(),
            database=self.collect_database_stats(),
            settings=self.sanitize_settings(settings or {}),
            logs=self.collect_logs(),
        )

    def collect_system_info(self) -> SystemInfo:
        """Collect Python version, platform, and installed package versions.

        Returns:
            SystemInfo with version and platform details.
        """
        packages: dict[str, str] = {}
        for dist in importlib.metadata.distributions():
            name = dist.metadata["Name"]
            if name and name.startswith("chaoscypher"):
                packages[name] = dist.metadata["Version"] or "unknown"

        # Include key dependencies
        for pkg in ("pydantic", "structlog", "sqlmodel", "fastapi", "valkey"):
            with contextlib.suppress(importlib.metadata.PackageNotFoundError):
                packages[pkg] = importlib.metadata.version(pkg)

        chaoscypher_version = packages.get("chaoscypher-core", "unknown")

        return SystemInfo(
            chaoscypher_version=chaoscypher_version,
            python_version=platform.python_version(),
            platform=platform.platform(),
            packages=packages,
        )

    def collect_database_stats(self) -> DiagnosticDatabaseStats:
        """Collect database file size and table counts.

        Returns:
            DiagnosticDatabaseStats. Fields are None/empty if db_path is not set.
        """
        if not self._db_path or not self._db_path.exists():
            return DiagnosticDatabaseStats(database_name="unknown")

        file_size = self._db_path.stat().st_size
        db_name = self._db_path.parent.name

        table_counts: dict[str, int] = {}
        try:
            import sqlite3

            conn = sqlite3.connect(str(self._db_path))
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            known_tables = {row[0] for row in cursor.fetchall()}
            for table_name in known_tables:
                try:
                    # table_name comes from sqlite_master (trusted), but we
                    # validate against the known set for defense-in-depth.
                    # sqlite3 doesn't support parameterized table names, so
                    # we quote the identifier after allowlist validation.
                    safe_name = table_name.replace('"', '""')
                    if not _VALID_TABLE_NAME.fullmatch(safe_name):
                        logger.warning("diagnostics_skipped_unsafe_table_name", name=safe_name)
                        continue
                    row = conn.execute(f'SELECT COUNT(*) FROM "{safe_name}"').fetchone()
                    table_counts[table_name] = row[0] if row else 0
                except Exception:
                    table_counts[table_name] = -1
            conn.close()
        except Exception:
            logger.warning("database_stats_failed", db_path=str(self._db_path))

        return DiagnosticDatabaseStats(
            database_name=db_name,
            file_size_bytes=file_size,
            table_counts=table_counts,
        )

    def collect_logs(self) -> dict[str, str]:
        """Read log files from the log directory.

        Returns:
            Dict mapping service name to log content. Empty if no log_dir.
        """
        if not self._log_dir or not self._log_dir.exists():
            return {}

        logs: dict[str, str] = {}
        for log_file in sorted(self._log_dir.iterdir()):
            if not log_file.is_file():
                continue
            if not log_file.name.endswith(".log") and ".log." not in log_file.name:
                continue

            # Map filename to key: cortex.log -> cortex, cortex.log.1 -> cortex.1
            name = log_file.name
            if name.endswith(".log"):
                key = name[:-4]  # strip .log
            elif ".log." in name:
                base, _, suffix = name.partition(".log.")
                key = f"{base}.{suffix}"
            else:
                continue

            try:
                raw = log_file.read_text(errors="replace")
                logs[key] = "\n".join(_scrub_log_line(ln) for ln in raw.splitlines())
            except OSError:
                logger.warning("log_read_failed", file=str(log_file))

        return logs

    def sanitize_settings(
        self,
        settings: dict[str, Any],
        *,
        secret_keys: tuple[str, ...] = _DEFAULT_SECRET_KEYS,
    ) -> dict[str, Any]:
        """Mask secret values in a settings dict.

        Replaces values for keys whose names contain any fragment from
        ``secret_keys`` (case-insensitive) with ``"configured"`` (or
        ``None`` when the field is unset), using the same boolean-style
        masking as ``mask_secret_value``.

        Args:
            settings: Raw settings dict.
            secret_keys: Tuple of key-name fragments to treat as secrets.
                Defaults to ``_DEFAULT_SECRET_KEYS``.

        Returns:
            Deep copy with secrets replaced by ``"configured"`` / ``None``.
        """
        result = deepcopy(settings)
        self._mask_secrets(result, secret_keys=secret_keys)
        return result

    def export_bundle(self, output_path: Path, settings: dict[str, Any] | None = None) -> Path:
        """Export diagnostic report as a ZIP file.

        Args:
            output_path: Where to write the ZIP file.
            settings: Optional settings dict to include.

        Returns:
            Path to the created ZIP file.
        """
        report = self.collect(settings)

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "system_info.json",
                json.dumps(report.system.model_dump(), indent=2),
            )
            zf.writestr(
                "settings.json",
                json.dumps(report.settings, indent=2, default=str),
            )
            zf.writestr(
                "database_stats.json",
                json.dumps(report.database.model_dump(), indent=2),
            )

            for name, content in report.logs.items():
                zf.writestr(f"logs/{name}.log", content)

            if report.queue:
                zf.writestr(
                    "queue_stats.json",
                    json.dumps(report.queue, indent=2, default=str),
                )
            if report.services:
                zf.writestr(
                    "services.json",
                    json.dumps(report.services, indent=2, default=str),
                )

        logger.info("diagnostic_bundle_exported", path=str(output_path))
        return output_path

    def _mask_secrets(
        self,
        d: dict[str, Any],
        *,
        secret_keys: tuple[str, ...] = _DEFAULT_SECRET_KEYS,
    ) -> None:
        """Recursively mask secret values in-place.

        Uses ``mask_secret_value`` to produce a boolean-style
        ``"configured"`` / ``None`` indicator instead of a partial reveal.

        Args:
            d: Dict to mask secrets in (modified in-place).
            secret_keys: Tuple of key-name fragments to treat as secrets.
        """
        for key, value in d.items():
            if isinstance(value, dict):
                self._mask_secrets(value, secret_keys=secret_keys)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._mask_secrets(item, secret_keys=secret_keys)
            elif any(kw in key.lower() for kw in secret_keys):
                d[key] = mask_secret_value(value)
