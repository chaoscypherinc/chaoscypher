# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chaos Cypher Backend - Vertical Slice Architecture.

Thin entrypoint: configures logging, constructs the FastAPI app via the
factory, and exposes the Click CLI. The heavy lifting lives in sibling
modules (``boot``, ``shutdown``, ``lifespan``, ``middleware``, ``app_factory``).

Usage:
    cc-cortex start
    cc-cortex start --port 9000
    cc-cortex start --reload
"""

from __future__ import annotations

import logging

import click
import structlog
import uvicorn

from chaoscypher_cortex.app_factory import create_app

# Boot import runs configure_logging() as a side effect — must be first so
# every subsequent logger in the package is created against the configured
# structlog processors.
from chaoscypher_cortex.boot import _SCHEMA_ONLY


logger = structlog.get_logger(__name__)


# Module-level app for uvicorn/supervisord entrypoints that reference
# ``chaoscypher_cortex.main:app`` as an import string. Schema-only
# construction used by the Dockerfile types-builder stage.
app = create_app(schema_only=_SCHEMA_ONLY)


# ============================================================================
# CLI Interface
# ============================================================================


class _HealthCheckFilter(logging.Filter):
    """Filter out health check requests from Uvicorn access logs.

    Suppresses both the liveness probe (GET /health) and the readiness
    probe (GET /api/v1/health) which fire every 30s from Docker.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Drop access-log records whose message references /health."""
        message = record.getMessage()
        return "/health" not in message


@click.group()
def cli() -> None:
    """Chaos Cypher Cortex - Full-featured backend API."""


@cli.command()
@click.option("--host", default="0.0.0.0", help="Host to bind to")
@click.option("--port", default=None, type=int, help="Port to bind to (default: from settings)")
@click.option("--reload", is_flag=True, help="Enable auto-reload")
def start(host: str, port: int | None, reload: bool) -> None:
    """Start Cortex API server.

    Examples:
        cc-cortex start                    # Start on default port
        cc-cortex start --port 9000        # Custom port
        cc-cortex start --reload           # Auto-reload for development
    """
    from chaoscypher_core.app_config import get_settings

    settings = get_settings()
    port = port or settings.ports.web_ui_api

    logger.info(
        "cortex_cli_starting",
        host=host,
        port=port,
        reload=reload,
    )

    from chaoscypher_core.database.engine import init_database

    logger.info("initializing_database", database_name=settings.current_database)
    init_database(
        settings.current_database,
        data_dir=settings.paths.data_dir,
        databases_subdir=settings.paths.databases_subdir,
        app_db_filename=settings.paths.app_db_filename,
        strict_schema_drift=settings.database.strict_schema_drift,
    )

    # Suppress /health access log spam (fires every 30s from Docker healthcheck)
    logging.getLogger("uvicorn.access").addFilter(_HealthCheckFilter())

    # Workers and reload are mutually exclusive in uvicorn
    workers = settings.services.uvicorn_workers if not reload else 1

    uvicorn.run(
        "chaoscypher_cortex.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
        workers=workers,
        server_header=False,
    )


@cli.command()
def status() -> None:
    """Check Cortex API status."""
    import httpx

    from chaoscypher_core.app_config import get_settings

    settings = get_settings()

    try:
        response = httpx.get(
            f"http://localhost:{settings.ports.web_ui_api}/health",
            timeout=settings.timeouts.health_check,
        )
        if response.status_code == 200:
            click.echo("✓ Cortex is healthy")
            return
    except Exception as e:
        click.echo(f"✗ Cortex is not responding: {e}", err=True)
        raise click.Abort from e

    click.echo("✗ Cortex returned unexpected status", err=True)
    raise click.Abort
