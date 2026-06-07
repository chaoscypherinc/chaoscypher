# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Serve command - Start local API server.

Launches Cortex for local knowledge graph access.
"""

import os
import subprocess
import sys
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel

from chaoscypher_cli.context import get_context
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.settings import CLISettings


_cli_defaults = CLISettings()

# FastAPI Query() defaults must be evaluated at module import (they go into the
# OpenAPI schema), so we resolve the page size once here. Operators changing
# settings.cli.serve_default_page_size after server startup must restart `serve`.
_SERVE_PAGE_SIZE = get_settings().cli.serve_default_page_size

console = Console()


@click.command()
@click.option("--port", "-p", default=_cli_defaults.api_port, help="API port")
@click.option("--host", "-h", "host_addr", default="localhost", help="Host to bind to")
@click.option("--database", "-d", default="default", help="Database to serve")
@click.option("--reload", is_flag=True, help="Auto-reload on file changes (dev mode)")
def serve(
    port: int,
    host_addr: str,
    database: str,
    reload: bool,
) -> None:
    """Start the local API server.

    Launches Cortex for access to the knowledge graph.

    Example:
        chaoscypher serve
        chaoscypher serve --port 9000
        chaoscypher serve --database my-project
        chaoscypher serve --reload
    """
    try:
        # Initialize context to verify database and get paths
        ctx = get_context(database_name=database)

        # Display startup info
        console.print(
            Panel(
                f"[bold]ChaosCypher Local Server[/bold]\n\n"
                f"[dim]Database:[/dim] {database}\n"
                f"[dim]API URL:[/dim] http://{host_addr}:{port}\n"
                f"[dim]Data dir:[/dim] {ctx.database_dir}",
                title="Server",
                border_style="cyan",
            )
        )

        # Show database stats
        stats = ctx.get_stats()
        console.print("\n[cyan]Database Statistics:[/cyan]")
        console.print(f"  Nodes: {stats.get('nodes', 0)}")
        console.print(f"  Edges: {stats.get('edges', 0)}")
        console.print(f"  Templates: {stats.get('templates', 0)}")

        # Set environment for server
        env = os.environ.copy()
        env["CHAOSCYPHER_DATABASE"] = database
        env["CHAOSCYPHER_DATA_DIR"] = str(ctx.data_dir)

        # Check if Cortex is installed
        cortex_available = False
        try:
            import importlib.util

            cortex_available = importlib.util.find_spec("chaoscypher_cortex") is not None
        except ImportError:
            pass

        if cortex_available:
            console.print("\n[green]Starting Cortex...[/green]")
            console.print("[dim]Press Ctrl+C to stop[/dim]\n")

            cmd = [
                sys.executable,
                "-m",
                "chaoscypher_cortex.main",
                "start",
                "--host",
                host_addr,
                "--port",
                str(port),
            ]

            if reload:
                cmd.append("--reload")

            try:
                subprocess.run(cmd, env=env, check=True)
            except subprocess.CalledProcessError as e:
                console.print(f"[red]Error:[/red] Server failed with exit code {e.returncode}")
                sys.exit(e.returncode)
        else:
            console.print(
                Panel(
                    "[yellow]chaoscypher-cortex is not installed.[/yellow]\n\n"
                    "[bold]Lightweight read-only fallback active[/bold] — "
                    "GET-only API with no auth, dev-mode CORS, no queue/workers.\n"
                    "Suitable for quick local inspection only.\n\n"
                    "For the full server install with:\n"
                    "  [cyan]pip install chaoscypher-cortex[/cyan]\n"
                    "or include the bundled extra:\n"
                    "  [cyan]pip install 'chaoscypher-cli[server]'[/cyan]",
                    title="Fallback server",
                    border_style="yellow",
                )
            )
            console.print("[dim]Press Ctrl+C to stop[/dim]\n")
            _run_builtin_server(database, host_addr, port, reload)

    except KeyboardInterrupt:
        console.print("\n[yellow]Server stopped.[/yellow]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


def _run_builtin_server(database: str, host: str, port: int, reload: bool) -> None:
    """Run the built-in lightweight server as fallback.

    This is used when chaoscypher-cortex is not installed.
    Provides basic read-only API endpoints.
    """
    try:
        import uvicorn
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware

        from chaoscypher_cli.context import get_context

        # Create minimal FastAPI app
        app = FastAPI(
            title="ChaosCypher Query API (Fallback)",
            description="Lightweight local knowledge graph query API",
            version="0.1.0",
        )

        # Add CORS middleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(get_settings().cors.dev_fallback_origins),
            allow_credentials=True,
            allow_methods=["GET", "OPTIONS", "HEAD"],  # Read-only
            allow_headers=["Content-Type", "Authorization"],
        )

        # Health check endpoint
        @app.get("/health")
        def health() -> dict[str, str]:
            return {"status": "ok", "database": database}

        # Stats endpoint
        @app.get("/api/v1/stats")
        def get_stats() -> dict[str, Any]:
            ctx = get_context(database_name=database)
            return ctx.get_stats()

        # Nodes endpoint
        @app.get("/api/v1/nodes")
        def list_nodes(
            limit: int = _SERVE_PAGE_SIZE,
            offset: int = 0,
            template_id: str | None = None,
        ) -> dict[str, Any]:
            ctx = get_context(database_name=database)
            return ctx.node_service.list_nodes(
                template_id=template_id,
                page=offset // limit + 1,
                page_size=limit,
            )

        # Node by ID endpoint
        @app.get("/api/v1/nodes/{node_id}")
        def get_node(node_id: str) -> dict[str, Any]:
            ctx = get_context(database_name=database)
            node = ctx.node_service.get_node(node_id)
            if not node:
                raise HTTPException(status_code=404, detail="Node not found")
            return node

        # Edges endpoint
        @app.get("/api/v1/edges")
        def list_edges(
            limit: int = _SERVE_PAGE_SIZE,
            offset: int = 0,
            source_node_id: str | None = None,
        ) -> dict[str, Any]:
            ctx = get_context(database_name=database)
            return ctx.edge_service.list_edges(
                source_node_id=source_node_id,
                page=offset // limit + 1,
                page_size=limit,
            )

        # Templates endpoint
        @app.get("/api/v1/templates")
        def list_templates() -> dict[str, Any]:
            ctx = get_context(database_name=database)
            return ctx.template_service.list_templates()

        # Template by ID endpoint
        @app.get("/api/v1/templates/{template_id}")
        def get_template(template_id: str) -> dict[str, Any]:
            ctx = get_context(database_name=database)
            template = ctx.template_service.get_template(template_id)
            if not template:
                raise HTTPException(status_code=404, detail="Template not found")
            return template

        console.print("[green]Fallback server starting...[/green]")

        # Run the server
        uvicorn.run(
            app,
            host=host,
            port=port,
            reload=reload,
            log_level="info",
        )

    except ImportError:
        console.print("\n[red]Error:[/red] Missing server dependencies (uvicorn, fastapi).")
        console.print("Install with: pip install 'chaoscypher-cli[server]'")
        console.print("\n[dim]Or use the full Cortex server: pip install chaoscypher-cortex[/dim]")
        sys.exit(1)
