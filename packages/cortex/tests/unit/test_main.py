# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the Cortex Click CLI entrypoint (``chaoscypher_cortex.main``).

Covers the ``start`` and ``status`` commands and the ``_HealthCheckFilter``
log filter. Heavy side effects — building the FastAPI app at import time,
launching uvicorn, initialising the database, and issuing the health-check
HTTP request — are all patched so nothing real is launched.

Importing ``chaoscypher_cortex.main`` runs ``create_app()`` at module scope.
We patch ``app_factory.create_app`` with a lightweight stub *before* the
first import so the module loads cheaply and deterministically.
"""

from __future__ import annotations

import importlib
import logging
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


def _import_main():
    """Import ``chaoscypher_cortex.main`` with ``create_app`` stubbed.

    Returns the imported module. Safe to call repeatedly — if the module is
    already imported the stub patch is a harmless no-op for module scope.
    """
    with patch(
        "chaoscypher_cortex.app_factory.create_app",
        return_value=MagicMock(name="FastAPIApp"),
    ):
        return importlib.import_module("chaoscypher_cortex.main")


@pytest.fixture(scope="module")
def main_module():
    """The imported main module (shared across this file's tests)."""
    return _import_main()


# ---------------------------------------------------------------------------
# _HealthCheckFilter
# ---------------------------------------------------------------------------


def test_health_filter_drops_health_records(main_module) -> None:
    """Records mentioning /health are filtered out of the access log."""
    log_filter = main_module._HealthCheckFilter()
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='GET /health HTTP/1.1" 200',
        args=(),
        exc_info=None,
    )
    assert log_filter.filter(record) is False


def test_health_filter_drops_api_health_records(main_module) -> None:
    """The readiness probe path /api/v1/health is also suppressed."""
    log_filter = main_module._HealthCheckFilter()
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='GET /api/v1/health HTTP/1.1" 200',
        args=(),
        exc_info=None,
    )
    assert log_filter.filter(record) is False


def test_health_filter_keeps_non_health_records(main_module) -> None:
    """Ordinary access-log records pass through the filter."""
    log_filter = main_module._HealthCheckFilter()
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='GET /api/v1/nodes HTTP/1.1" 200',
        args=(),
        exc_info=None,
    )
    assert log_filter.filter(record) is True


# ---------------------------------------------------------------------------
# start command
# ---------------------------------------------------------------------------


def _make_settings(port: int = 8000, workers: int = 4) -> MagicMock:
    settings = MagicMock()
    settings.ports.web_ui_api = port
    settings.current_database = "default"
    settings.services.uvicorn_workers = workers
    settings.timeouts.health_check = 5
    return settings


def test_start_runs_uvicorn_with_default_port(main_module) -> None:
    """``start`` resolves the port from settings and launches uvicorn."""
    settings = _make_settings(port=8123, workers=3)

    with (
        patch("chaoscypher_core.app_config.get_settings", return_value=settings),
        patch("chaoscypher_core.database.engine.init_database") as mock_init,
        patch.object(main_module.uvicorn, "run") as mock_run,
    ):
        result = CliRunner().invoke(main_module.cli, ["start"])

    assert result.exit_code == 0, result.output
    mock_init.assert_called_once_with(
        "default",
        data_dir=settings.paths.data_dir,
        databases_subdir=settings.paths.databases_subdir,
        app_db_filename=settings.paths.app_db_filename,
        strict_schema_drift=settings.database.strict_schema_drift,
    )
    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 8123
    assert kwargs["reload"] is False
    # workers come from settings when not reloading
    assert kwargs["workers"] == 3
    assert kwargs["server_header"] is False


def test_start_custom_port_overrides_settings(main_module) -> None:
    """An explicit --port overrides the settings-derived default."""
    settings = _make_settings(port=8000)

    with (
        patch("chaoscypher_core.app_config.get_settings", return_value=settings),
        patch("chaoscypher_core.database.engine.init_database"),
        patch.object(main_module.uvicorn, "run") as mock_run,
    ):
        result = CliRunner().invoke(main_module.cli, ["start", "--port", "9000"])

    assert result.exit_code == 0, result.output
    assert mock_run.call_args.kwargs["port"] == 9000


def test_start_reload_forces_single_worker(main_module) -> None:
    """--reload forces workers=1 (mutually exclusive with multi-worker)."""
    settings = _make_settings(workers=8)

    with (
        patch("chaoscypher_core.app_config.get_settings", return_value=settings),
        patch("chaoscypher_core.database.engine.init_database"),
        patch.object(main_module.uvicorn, "run") as mock_run,
    ):
        result = CliRunner().invoke(main_module.cli, ["start", "--reload"])

    assert result.exit_code == 0, result.output
    kwargs = mock_run.call_args.kwargs
    assert kwargs["reload"] is True
    assert kwargs["workers"] == 1


def test_start_registers_health_filter(main_module) -> None:
    """``start`` attaches the _HealthCheckFilter to the uvicorn.access logger."""
    settings = _make_settings()
    access_logger = logging.getLogger("uvicorn.access")
    before = list(access_logger.filters)

    with (
        patch("chaoscypher_core.app_config.get_settings", return_value=settings),
        patch("chaoscypher_core.database.engine.init_database"),
        patch.object(main_module.uvicorn, "run"),
    ):
        result = CliRunner().invoke(main_module.cli, ["start"])

    assert result.exit_code == 0, result.output
    added = [f for f in access_logger.filters if f not in before]
    assert any(isinstance(f, main_module._HealthCheckFilter) for f in added)
    # Clean up the filter we added so we don't leak state into other tests.
    for f in added:
        access_logger.removeFilter(f)


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------


def test_status_healthy(main_module) -> None:
    """A 200 from /health prints the healthy message and exits 0."""
    settings = _make_settings(port=8000)
    response = MagicMock()
    response.status_code = 200

    with (
        patch("chaoscypher_core.app_config.get_settings", return_value=settings),
        patch("httpx.get", return_value=response) as mock_get,
    ):
        result = CliRunner().invoke(main_module.cli, ["status"])

    assert result.exit_code == 0, result.output
    assert "healthy" in result.output
    mock_get.assert_called_once()


def test_status_connection_error_aborts(main_module) -> None:
    """A connection error reports the failure and aborts (non-zero exit)."""
    settings = _make_settings(port=8000)

    with (
        patch("chaoscypher_core.app_config.get_settings", return_value=settings),
        patch("httpx.get", side_effect=ConnectionError("refused")),
    ):
        result = CliRunner().invoke(main_module.cli, ["status"])

    assert result.exit_code != 0
    assert "not responding" in result.output


def test_status_unexpected_status_aborts(main_module) -> None:
    """A non-200 response falls through to the 'unexpected status' abort."""
    settings = _make_settings(port=8000)
    response = MagicMock()
    response.status_code = 503

    with (
        patch("chaoscypher_core.app_config.get_settings", return_value=settings),
        patch("httpx.get", return_value=response),
    ):
        result = CliRunner().invoke(main_module.cli, ["status"])

    assert result.exit_code != 0
    assert "unexpected status" in result.output
