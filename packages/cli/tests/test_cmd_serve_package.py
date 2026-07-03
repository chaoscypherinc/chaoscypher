# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for serve, mcp, package load, and package export commands.

Covers:
- commands/runtime/serve.py — happy path (cortex available + fallback), error paths
- mcp/command.py — wiring, happy path with asyncio.run mocked, mode/flags
- commands/package/load.py — happy path, merge mode, error path, display (CcxImporter)
- commands/package/export.py — happy path, auto-filename, .ccx extension fix, empty guard,
  _display_export_results helper (CcxExporter; export() returns raw bytes)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.package.export import export
from chaoscypher_cli.commands.package.load import load
from chaoscypher_cli.commands.runtime.serve import serve
from chaoscypher_cli.mcp.command import mcp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_ctx(database_name: str = "default", data_dir: Path | None = None) -> MagicMock:
    """Return a minimal mock CLIContext."""
    ctx = MagicMock()
    ctx.database_name = database_name
    ctx.database_dir = Path("/tmp/fake_db") / database_name
    ctx.data_dir = data_dir or Path("/tmp/fake_data")
    ctx.get_stats.return_value = {
        "database_name": database_name,
        "nodes": 10,
        "edges": 5,
        "templates": 3,
    }
    ctx.settings = MagicMock()
    ctx.settings.mcp = MagicMock()
    ctx.settings.mcp.mode = "read"
    ctx.settings.mcp.auto_extract = False
    ctx._engine = MagicMock()
    return ctx


def _make_import_stats(**kwargs: Any) -> Any:
    """Return a real ImportStats instance."""
    from chaoscypher_core.services.package.importer.models import ImportStats

    return ImportStats(
        templates_imported=kwargs.get("templates_imported", 2),
        nodes_imported=kwargs.get("nodes_imported", 5),
        edges_imported=kwargs.get("edges_imported", 3),
        workflows_imported=kwargs.get("workflows_imported", 1),
        checksum_verified=kwargs.get("checksum_verified", True),
        warnings=kwargs.get("warnings", []),
        errors=kwargs.get("errors", []),
    )


def _consume_coro(return_value: Any) -> Any:
    """asyncio.run stand-in that closes the received coroutine.

    A bare return_value mock leaves the real ``import_from_path(...)``
    coroutine argument unawaited, leaking RuntimeWarnings into unrelated
    tests at GC time.
    """

    def _run(coro: Any) -> Any:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return return_value

    return _run


# ===========================================================================
# serve command
# ===========================================================================
# NOTE on importlib.util.find_spec:
# serve.py uses `import importlib.util` lazily inside the function body, so
# `chaoscypher_cli.commands.runtime.serve.importlib` does NOT exist at module
# level.  We must patch `importlib.util.find_spec` at the standard library level.


class TestServeHappyPathCortex:
    """serve exits 0 when cortex is available and subprocess.run succeeds."""

    def test_serve_calls_subprocess_run_with_host_and_port(self) -> None:
        runner = CliRunner()
        mock_ctx = _make_mock_ctx()

        with (
            patch("chaoscypher_cli.commands.runtime.serve.get_context", return_value=mock_ctx),
            patch(
                "importlib.util.find_spec",
                return_value=MagicMock(),  # truthy → cortex available
            ),
            patch("chaoscypher_cli.commands.runtime.serve.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(serve, ["--port", "8765", "--host", "127.0.0.1"])

        assert result.exit_code == 0, result.output
        mock_run.assert_called_once()
        cmd_args = mock_run.call_args[0][0]
        assert "--host" in cmd_args
        assert "127.0.0.1" in cmd_args
        assert "--port" in cmd_args
        assert "8765" in cmd_args

    def test_serve_reload_flag_appended(self) -> None:
        runner = CliRunner()
        mock_ctx = _make_mock_ctx()

        with (
            patch("chaoscypher_cli.commands.runtime.serve.get_context", return_value=mock_ctx),
            patch(
                "importlib.util.find_spec",
                return_value=MagicMock(),
            ),
            patch("chaoscypher_cli.commands.runtime.serve.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(serve, ["--reload"])

        assert result.exit_code == 0, result.output
        cmd_args = mock_run.call_args[0][0]
        assert "--reload" in cmd_args

    def test_serve_database_flag_forwarded_to_env(self) -> None:
        runner = CliRunner()
        mock_ctx = _make_mock_ctx("my-project")

        with (
            patch("chaoscypher_cli.commands.runtime.serve.get_context", return_value=mock_ctx),
            patch(
                "importlib.util.find_spec",
                return_value=MagicMock(),
            ),
            patch("chaoscypher_cli.commands.runtime.serve.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(serve, ["--database", "my-project"])

        assert result.exit_code == 0, result.output
        env_used = mock_run.call_args[1]["env"]
        assert env_used["CHAOSCYPHER_DATABASE"] == "my-project"

    def test_serve_output_shows_api_url(self) -> None:
        runner = CliRunner()
        mock_ctx = _make_mock_ctx()

        with (
            patch("chaoscypher_cli.commands.runtime.serve.get_context", return_value=mock_ctx),
            patch(
                "importlib.util.find_spec",
                return_value=MagicMock(),
            ),
            patch("chaoscypher_cli.commands.runtime.serve.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(serve, ["--port", "9000", "--host", "0.0.0.0"])

        assert "http://0.0.0.0:9000" in result.output


class TestServeCalledProcessError:
    """serve propagates CalledProcessError as non-zero exit."""

    def test_subprocess_error_calls_sys_exit(self) -> None:
        import subprocess

        runner = CliRunner()
        mock_ctx = _make_mock_ctx()

        with (
            patch("chaoscypher_cli.commands.runtime.serve.get_context", return_value=mock_ctx),
            patch(
                "importlib.util.find_spec",
                return_value=MagicMock(),
            ),
            patch(
                "chaoscypher_cli.commands.runtime.serve.subprocess.run",
                side_effect=subprocess.CalledProcessError(2, "chaoscypher_cortex"),
            ),
            patch("chaoscypher_cli.commands.runtime.serve.sys.exit") as mock_exit,
        ):
            runner.invoke(serve, [])

        # sys.exit was called with the returncode 2
        mock_exit.assert_any_call(2)

    def test_generic_exception_shows_error_message(self) -> None:
        runner = CliRunner()

        with patch(
            "chaoscypher_cli.commands.runtime.serve.get_context",
            side_effect=RuntimeError("DB unavailable"),
        ):
            result = runner.invoke(serve, [])

        # RuntimeError is caught by the generic except; message printed
        assert "DB unavailable" in result.output or "Error" in result.output

    def test_generic_exception_calls_sys_exit_1(self) -> None:
        runner = CliRunner()

        with (
            patch(
                "chaoscypher_cli.commands.runtime.serve.get_context",
                side_effect=RuntimeError("DB unavailable"),
            ),
            patch("chaoscypher_cli.commands.runtime.serve.sys.exit") as mock_exit,
        ):
            runner.invoke(serve, [])

        # The exception handler calls sys.exit(1), then Click may also call
        # sys.exit(0) when the command finishes - check 1 is in the calls.
        called_codes = [call.args[0] for call in mock_exit.call_args_list]
        assert 1 in called_codes


class TestServeFallbackServer:
    """serve uses builtin fallback when cortex is NOT installed."""

    def test_fallback_path_when_cortex_unavailable(self) -> None:
        runner = CliRunner()
        mock_ctx = _make_mock_ctx()

        with (
            patch("chaoscypher_cli.commands.runtime.serve.get_context", return_value=mock_ctx),
            patch(
                "importlib.util.find_spec",
                return_value=None,  # cortex not available
            ),
            patch("chaoscypher_cli.commands.runtime.serve._run_builtin_server") as mock_builtin,
        ):
            result = runner.invoke(serve, ["--port", "8080"])

        mock_builtin.assert_called_once_with("default", "localhost", 8080, False)
        assert result.exit_code == 0, result.output

    def test_fallback_path_shows_install_hint(self) -> None:
        runner = CliRunner()
        mock_ctx = _make_mock_ctx()

        with (
            patch("chaoscypher_cli.commands.runtime.serve.get_context", return_value=mock_ctx),
            patch(
                "importlib.util.find_spec",
                return_value=None,
            ),
            patch("chaoscypher_cli.commands.runtime.serve._run_builtin_server"),
        ):
            result = runner.invoke(serve, [])

        assert "chaoscypher-cortex" in result.output or "cortex" in result.output.lower()

    def test_run_builtin_server_import_error_exits_1(self) -> None:
        """_run_builtin_server exits 1 when uvicorn is not installed.

        The import of uvicorn happens lazily inside _run_builtin_server.
        We patch the module-level `sys.exit` and `uvicorn` in the module's
        namespace (with create=True so the mock attribute appears before the
        real import runs), then make the real `import uvicorn` raise ImportError
        by patching builtins.__import__ only for that name.
        """
        from chaoscypher_cli.commands.runtime.serve import _run_builtin_server

        real_import = __import__

        def _blocking_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name in ("uvicorn", "fastapi", "fastapi.middleware.cors"):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=_blocking_import),
            patch("chaoscypher_cli.commands.runtime.serve.sys.exit") as mock_exit,
        ):
            _run_builtin_server("default", "localhost", 8000, False)

        mock_exit.assert_any_call(1)

    def test_run_builtin_server_happy_path(self) -> None:
        """_run_builtin_server builds a real FastAPI app and calls uvicorn.run."""
        from chaoscypher_cli.commands.runtime.serve import _run_builtin_server

        # fastapi and uvicorn are installed; only mock uvicorn.run to avoid blocking.
        mock_ctx = _make_mock_ctx()

        with (
            patch("uvicorn.run") as mock_run,
            patch("chaoscypher_cli.context.get_context", return_value=mock_ctx),
            patch("chaoscypher_cli.commands.runtime.serve.get_settings") as mock_settings,
        ):
            mock_settings.return_value.cors.dev_fallback_origins = ["http://localhost:3000"]
            _run_builtin_server("default", "localhost", 8000, False)

        # uvicorn.run must be called with correct host/port. reload is never
        # forwarded — uvicorn's reload mode needs an import string, not an
        # app object, so the fallback server doesn't support it.
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["host"] == "localhost"
        assert call_kwargs["port"] == 8000
        assert "reload" not in call_kwargs

    def test_run_builtin_server_routes_registered(self) -> None:
        """Route handlers in _run_builtin_server are callable and return correct shapes.

        We call _run_builtin_server, capture the FastAPI app via a hook on
        uvicorn.run, then exercise each route through the starlette TestClient.
        This covers the endpoint handler bodies that are otherwise unreachable.
        """
        from fastapi.testclient import TestClient

        from chaoscypher_cli.commands.runtime.serve import _run_builtin_server

        captured_app: list[Any] = []

        def _capture_app(app: Any, **kwargs: Any) -> None:
            captured_app.append(app)

        mock_ctx = _make_mock_ctx()
        mock_ctx.node_service.list_nodes.return_value = {"items": [], "total": 0}
        mock_ctx.node_service.get_node.return_value = {"id": "node_1", "name": "Test"}
        mock_ctx.edge_service.list_edges.return_value = {"items": [], "total": 0}
        mock_ctx.template_service.list_templates.return_value = {"items": []}
        mock_ctx.template_service.get_template.return_value = {"id": "t1", "name": "Thing"}

        with (
            patch("uvicorn.run") as mock_run,
            patch("chaoscypher_cli.context.get_context", return_value=mock_ctx),
            patch("chaoscypher_cli.commands.runtime.serve.get_settings") as mock_settings,
        ):
            mock_settings.return_value.cors.dev_fallback_origins = ["http://localhost:3000"]
            mock_run.side_effect = _capture_app
            _run_builtin_server("default", "localhost", 8000, False)

        assert captured_app, "uvicorn.run was not called with an app"
        client = TestClient(captured_app[0], raise_server_exceptions=True)

        # /health
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # /api/v1/stats
        resp = client.get("/api/v1/stats")
        assert resp.status_code == 200

        # /api/v1/nodes
        resp = client.get("/api/v1/nodes")
        assert resp.status_code == 200

        # /api/v1/nodes/{node_id}
        resp = client.get("/api/v1/nodes/node_1")
        assert resp.status_code == 200

        # /api/v1/edges
        resp = client.get("/api/v1/edges")
        assert resp.status_code == 200

        # /api/v1/templates
        resp = client.get("/api/v1/templates")
        assert resp.status_code == 200

        # /api/v1/templates/{template_id}
        resp = client.get("/api/v1/templates/t1")
        assert resp.status_code == 200

    def test_run_builtin_server_node_not_found(self) -> None:
        """GET /api/v1/nodes/{node_id} returns 404 when node doesn't exist."""
        from fastapi.testclient import TestClient

        from chaoscypher_cli.commands.runtime.serve import _run_builtin_server

        captured_app: list[Any] = []

        def _capture_app(app: Any, **kwargs: Any) -> None:
            captured_app.append(app)

        mock_ctx = _make_mock_ctx()
        mock_ctx.node_service.get_node.return_value = None  # not found

        with (
            patch("uvicorn.run") as mock_run,
            patch("chaoscypher_cli.context.get_context", return_value=mock_ctx),
            patch("chaoscypher_cli.commands.runtime.serve.get_settings") as mock_settings,
        ):
            mock_settings.return_value.cors.dev_fallback_origins = ["http://localhost:3000"]
            mock_run.side_effect = _capture_app
            _run_builtin_server("default", "localhost", 8000, False)

        assert captured_app
        client = TestClient(captured_app[0], raise_server_exceptions=False)
        resp = client.get("/api/v1/nodes/nonexistent")
        assert resp.status_code == 404

    def test_run_builtin_server_template_not_found(self) -> None:
        """GET /api/v1/templates/{template_id} returns 404 when template doesn't exist."""
        from fastapi.testclient import TestClient

        from chaoscypher_cli.commands.runtime.serve import _run_builtin_server

        captured_app: list[Any] = []

        def _capture_app(app: Any, **kwargs: Any) -> None:
            captured_app.append(app)

        mock_ctx = _make_mock_ctx()
        mock_ctx.template_service.get_template.return_value = None  # not found

        with (
            patch("uvicorn.run") as mock_run,
            patch("chaoscypher_cli.context.get_context", return_value=mock_ctx),
            patch("chaoscypher_cli.commands.runtime.serve.get_settings") as mock_settings,
        ):
            mock_settings.return_value.cors.dev_fallback_origins = ["http://localhost:3000"]
            mock_run.side_effect = _capture_app
            _run_builtin_server("default", "localhost", 8000, False)

        assert captured_app
        client = TestClient(captured_app[0], raise_server_exceptions=False)
        resp = client.get("/api/v1/templates/nonexistent")
        assert resp.status_code == 404

    def test_serve_help_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(serve, ["--help"])
        assert result.exit_code == 0
        assert "--port" in result.output
        assert "--host" in result.output
        assert "--reload" in result.output


class TestServeKeyboardInterrupt:
    """Ctrl-C during serve should exit cleanly (exit code 0)."""

    def test_keyboard_interrupt_exits_cleanly(self) -> None:
        runner = CliRunner()

        with patch(
            "chaoscypher_cli.commands.runtime.serve.get_context",
            side_effect=KeyboardInterrupt,
        ):
            result = runner.invoke(serve, [])

        # KeyboardInterrupt is caught; no sys.exit(1) should be called
        assert "stopped" in result.output.lower() or result.exit_code == 0


# ===========================================================================
# mcp command
# NOTE: configure_logging, langchain_text_splitters, stdio_server, and
# create_mcp_server are all imported LAZILY inside the mcp() function body,
# so we must patch them at their SOURCE module paths (or use `create=True`
# and patch the module at the source level).
# ===========================================================================


class TestMcpHappyPath:
    """mcp starts the MCP server with asyncio.run mocked out."""

    def _run_mcp(
        self,
        extra_args: list[str] | None = None,
        mock_ctx: MagicMock | None = None,
    ) -> tuple[Any, MagicMock]:
        """Helper: invoke mcp command with everything lazy-imported mocked."""
        runner = CliRunner()
        ctx = mock_ctx or _make_mock_ctx()
        mock_server = MagicMock()
        args = extra_args or []

        with (
            patch("chaoscypher_cli.mcp.command.get_context", return_value=ctx),
            patch("chaoscypher_cli.mcp.command.asyncio.run") as mock_async_run,
            patch("chaoscypher_core.utils.logging.configure_logging"),
            # Mock the startup migration gate so the happy-path (state.ready)
            # branch runs deterministically. Without this the command reads the
            # real dev DB; a migration-blocked DB takes the maintenance branch
            # and never sets mode/auto_extract or calls get_context.
            patch("chaoscypher_core.database.migrations.startup.run_startup_migrations"),
            patch(
                "chaoscypher_core.database.migrations.state.get_upgrade_state",
                return_value=MagicMock(ready=True),
            ),
            patch(
                "chaoscypher_cli.mcp.command.langchain_text_splitters",
                create=True,
            ),
            patch(
                "chaoscypher_core.mcp.server.create_mcp_server",
                return_value=mock_server,
            ),
            patch(
                "mcp.server.stdio.stdio_server",
                create=True,
            ),
        ):
            result = runner.invoke(mcp, args)
            return result, mock_async_run

    def test_mcp_default_invocation(self) -> None:
        result, mock_async_run = self._run_mcp()
        assert result.exit_code == 0, result.output
        mock_async_run.assert_called_once()

    def test_mcp_mode_flag_updates_context(self) -> None:
        mock_ctx = _make_mock_ctx()
        runner = CliRunner()
        mock_server = MagicMock()

        with (
            patch("chaoscypher_cli.mcp.command.get_context", return_value=mock_ctx),
            patch("chaoscypher_cli.mcp.command.asyncio.run"),
            patch("chaoscypher_core.utils.logging.configure_logging"),
            # Mock the startup migration gate so the happy-path (state.ready)
            # branch runs deterministically. Without this the command reads the
            # real dev DB; a migration-blocked DB takes the maintenance branch
            # and never sets mode/auto_extract or calls get_context.
            patch("chaoscypher_core.database.migrations.startup.run_startup_migrations"),
            patch(
                "chaoscypher_core.database.migrations.state.get_upgrade_state",
                return_value=MagicMock(ready=True),
            ),
            patch(
                "chaoscypher_cli.mcp.command.langchain_text_splitters",
                create=True,
            ),
            patch(
                "chaoscypher_core.mcp.server.create_mcp_server",
                return_value=mock_server,
            ),
            patch(
                "mcp.server.stdio.stdio_server",
                create=True,
            ),
        ):
            result = runner.invoke(mcp, ["--mode", "write"])

        assert result.exit_code == 0, result.output
        assert mock_ctx.settings.mcp.mode == "write"

    def test_mcp_server_extraction_flag_enables_auto_extract(self) -> None:
        mock_ctx = _make_mock_ctx()
        mock_ctx.settings.mcp.auto_extract = False
        runner = CliRunner()
        mock_server = MagicMock()

        with (
            patch("chaoscypher_cli.mcp.command.get_context", return_value=mock_ctx),
            patch("chaoscypher_cli.mcp.command.asyncio.run"),
            patch("chaoscypher_core.utils.logging.configure_logging"),
            # Mock the startup migration gate so the happy-path (state.ready)
            # branch runs deterministically. Without this the command reads the
            # real dev DB; a migration-blocked DB takes the maintenance branch
            # and never sets mode/auto_extract or calls get_context.
            patch("chaoscypher_core.database.migrations.startup.run_startup_migrations"),
            patch(
                "chaoscypher_core.database.migrations.state.get_upgrade_state",
                return_value=MagicMock(ready=True),
            ),
            patch(
                "chaoscypher_cli.mcp.command.langchain_text_splitters",
                create=True,
            ),
            patch(
                "chaoscypher_core.mcp.server.create_mcp_server",
                return_value=mock_server,
            ),
            patch(
                "mcp.server.stdio.stdio_server",
                create=True,
            ),
        ):
            result = runner.invoke(mcp, ["--server-extraction"])

        assert result.exit_code == 0, result.output
        assert mock_ctx.settings.mcp.auto_extract is True

    def test_mcp_engine_none_raises_runtime_error(self) -> None:
        """Engine=None must raise RuntimeError before asyncio.run."""
        runner = CliRunner()
        mock_ctx = _make_mock_ctx()
        mock_ctx._engine = None

        with (
            patch("chaoscypher_cli.mcp.command.get_context", return_value=mock_ctx),
            patch("chaoscypher_core.utils.logging.configure_logging"),
            # Mock the startup migration gate so the happy-path (state.ready)
            # branch runs deterministically. Without this the command reads the
            # real dev DB; a migration-blocked DB takes the maintenance branch
            # and never sets mode/auto_extract or calls get_context.
            patch("chaoscypher_core.database.migrations.startup.run_startup_migrations"),
            patch(
                "chaoscypher_core.database.migrations.state.get_upgrade_state",
                return_value=MagicMock(ready=True),
            ),
            patch(
                "chaoscypher_cli.mcp.command.langchain_text_splitters",
                create=True,
            ),
            patch(
                "chaoscypher_core.mcp.server.create_mcp_server",
                create=True,
            ),
            patch(
                "mcp.server.stdio.stdio_server",
                create=True,
            ),
        ):
            result = runner.invoke(mcp, [])

        # RuntimeError is raised and propagated; Click converts it to exit_code=1
        assert result.exit_code != 0 or result.exception is not None

    def test_mcp_database_flag_forwarded(self) -> None:
        runner = CliRunner()
        mock_ctx = _make_mock_ctx("my-db")
        mock_server = MagicMock()

        with (
            patch("chaoscypher_cli.mcp.command.get_context", return_value=mock_ctx) as mock_gc,
            patch("chaoscypher_cli.mcp.command.asyncio.run"),
            patch("chaoscypher_core.utils.logging.configure_logging"),
            # Mock the startup migration gate so the happy-path (state.ready)
            # branch runs deterministically. Without this the command reads the
            # real dev DB; a migration-blocked DB takes the maintenance branch
            # and never sets mode/auto_extract or calls get_context.
            patch("chaoscypher_core.database.migrations.startup.run_startup_migrations"),
            patch(
                "chaoscypher_core.database.migrations.state.get_upgrade_state",
                return_value=MagicMock(ready=True),
            ),
            patch(
                "chaoscypher_cli.mcp.command.langchain_text_splitters",
                create=True,
            ),
            patch(
                "chaoscypher_core.mcp.server.create_mcp_server",
                return_value=mock_server,
            ),
            patch(
                "mcp.server.stdio.stdio_server",
                create=True,
            ),
        ):
            result = runner.invoke(mcp, ["--database", "my-db"])

        assert result.exit_code == 0, result.output
        mock_gc.assert_called_once_with(database_name="my-db")

    def test_mcp_help_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(mcp, ["--help"])
        assert result.exit_code == 0
        assert "--database" in result.output
        assert "--mode" in result.output

    def test_mcp_cmd_name(self) -> None:
        assert mcp.name == "mcp"

    def test_mcp_cmd_params(self) -> None:
        params = {p.name for p in mcp.params}
        assert "database" in params
        assert "mode" in params
        assert "server_extraction" in params


# ===========================================================================
# package load command
# ===========================================================================


class TestPackageLoadHappyPath:
    """load imports archive, calls service, exits 0 on success.

    CcxImporter and ImportOptions are imported LAZILY inside the load()
    function body with ``from chaoscypher_core.services.package.importer
    import ...``; these tests patch ``asyncio.run`` so the importer is never
    actually invoked.
    """

    def test_load_calls_import_service(self, tmp_path: Path) -> None:
        """Load instantiates CcxImporter and calls asyncio.run on exit 0."""
        runner = CliRunner()
        ccx_file = tmp_path / "test.ccx"
        ccx_file.write_bytes(b"FAKE_CCX_DATA")

        mock_ctx = _make_mock_ctx()
        stats = _make_import_stats()

        with (
            patch("chaoscypher_cli.commands.package.load.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_cli.commands.package.load.asyncio.run", return_value=stats
            ) as mock_run,
        ):
            result = runner.invoke(load, [str(ccx_file)])

        assert result.exit_code == 0, result.output
        # asyncio.run was called (wrapping the import coroutine)
        mock_run.assert_called_once()

    def test_load_shows_import_results_table(self, tmp_path: Path) -> None:
        runner = CliRunner()
        ccx_file = tmp_path / "test.ccx"
        ccx_file.write_bytes(b"FAKE_CCX_DATA")

        mock_ctx = _make_mock_ctx()
        stats = _make_import_stats(
            templates_imported=4,
            nodes_imported=12,
            edges_imported=8,
            checksum_verified=True,
        )

        with (
            patch("chaoscypher_cli.commands.package.load.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_cli.commands.package.load.asyncio.run",
                side_effect=_consume_coro(stats),
            ),
        ):
            result = runner.invoke(load, [str(ccx_file)])

        assert result.exit_code == 0, result.output
        assert "Import Results" in result.output or "imported" in result.output.lower()

    def test_load_merge_mode_prints_merge_label(self, tmp_path: Path) -> None:
        runner = CliRunner()
        ccx_file = tmp_path / "test.ccx"
        ccx_file.write_bytes(b"FAKE_CCX_DATA")

        mock_ctx = _make_mock_ctx()
        stats = _make_import_stats()

        with (
            patch("chaoscypher_cli.commands.package.load.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_cli.commands.package.load.asyncio.run",
                side_effect=_consume_coro(stats),
            ),
        ):
            result = runner.invoke(load, [str(ccx_file), "--merge"])

        assert result.exit_code == 0, result.output
        assert "Merge" in result.output

    def test_load_replace_mode_by_default(self, tmp_path: Path) -> None:
        runner = CliRunner()
        ccx_file = tmp_path / "test.ccx"
        ccx_file.write_bytes(b"FAKE_CCX_DATA")

        mock_ctx = _make_mock_ctx()
        stats = _make_import_stats()

        with (
            patch("chaoscypher_cli.commands.package.load.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_cli.commands.package.load.asyncio.run",
                side_effect=_consume_coro(stats),
            ),
        ):
            result = runner.invoke(load, [str(ccx_file)])

        assert result.exit_code == 0, result.output
        assert "Replace" in result.output

    def test_load_no_templates_flag(self, tmp_path: Path) -> None:
        runner = CliRunner()
        ccx_file = tmp_path / "test.ccx"
        ccx_file.write_bytes(b"FAKE_CCX_DATA")

        mock_ctx = _make_mock_ctx()
        stats = _make_import_stats()

        # The load() command does: from chaoscypher_core.services.package.importer
        #   import CcxImporter, ImportOptions
        # asyncio.run is patched so the importer is never invoked.
        with (
            patch("chaoscypher_cli.commands.package.load.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_cli.commands.package.load.asyncio.run",
                side_effect=_consume_coro(stats),
            ),
        ):
            # The real ImportOptions will be called; verify behaviour via stats
            result = runner.invoke(load, [str(ccx_file), "--no-templates"])

        # asyncio.run returns our stats, so exit 0
        assert result.exit_code == 0, result.output

    def test_load_database_flag_passed_to_context(self, tmp_path: Path) -> None:
        runner = CliRunner()
        ccx_file = tmp_path / "test.ccx"
        ccx_file.write_bytes(b"FAKE_CCX_DATA")

        mock_ctx = _make_mock_ctx("research")
        stats = _make_import_stats()

        with (
            patch(
                "chaoscypher_cli.commands.package.load.get_context", return_value=mock_ctx
            ) as mock_gc,
            patch(
                "chaoscypher_cli.commands.package.load.asyncio.run",
                side_effect=_consume_coro(stats),
            ),
        ):
            result = runner.invoke(load, [str(ccx_file), "--database", "research"])

        assert result.exit_code == 0, result.output
        mock_gc.assert_called_once_with(database_name="research")


class TestPackageLoadErrors:
    """load exits 1 on import failures."""

    def test_load_exits_1_on_import_error_stats(self, tmp_path: Path) -> None:
        runner = CliRunner()
        ccx_file = tmp_path / "bad.ccx"
        ccx_file.write_bytes(b"FAKE")

        mock_ctx = _make_mock_ctx()
        stats = _make_import_stats(errors=["checksum mismatch on graph.jsonld"])

        with (
            patch("chaoscypher_cli.commands.package.load.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_cli.commands.package.load.asyncio.run",
                side_effect=_consume_coro(stats),
            ),
            patch("chaoscypher_cli.commands.package.load.sys.exit") as mock_exit,
        ):
            runner.invoke(load, [str(ccx_file)])

        mock_exit.assert_any_call(1)

    def test_load_exits_1_on_exception(self, tmp_path: Path) -> None:
        runner = CliRunner()
        ccx_file = tmp_path / "corrupt.ccx"
        ccx_file.write_bytes(b"CORRUPT")

        with (
            patch(
                "chaoscypher_cli.commands.package.load.get_context",
                side_effect=RuntimeError("Corrupt archive"),
            ),
            patch("chaoscypher_cli.commands.package.load.sys.exit") as mock_exit,
        ):
            result = runner.invoke(load, [str(ccx_file)])

        mock_exit.assert_any_call(1)
        assert "Corrupt archive" in result.output or "Error" in result.output

    def test_load_shows_warnings_in_output(self, tmp_path: Path) -> None:
        runner = CliRunner()
        ccx_file = tmp_path / "warned.ccx"
        ccx_file.write_bytes(b"FAKE")

        mock_ctx = _make_mock_ctx()
        stats = _make_import_stats(warnings=["embedding model mismatch — re-embed recommended"])

        with (
            patch("chaoscypher_cli.commands.package.load.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_cli.commands.package.load.asyncio.run",
                side_effect=_consume_coro(stats),
            ),
        ):
            result = runner.invoke(load, [str(ccx_file)])

        assert result.exit_code == 0, result.output
        assert "embedding model mismatch" in result.output

    def test_load_success_message_includes_total_items(self, tmp_path: Path) -> None:
        runner = CliRunner()
        ccx_file = tmp_path / "success.ccx"
        ccx_file.write_bytes(b"FAKE")

        mock_ctx = _make_mock_ctx()
        stats = _make_import_stats(nodes_imported=7, edges_imported=3)

        with (
            patch("chaoscypher_cli.commands.package.load.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_cli.commands.package.load.asyncio.run",
                side_effect=_consume_coro(stats),
            ),
        ):
            result = runner.invoke(load, [str(ccx_file)])

        assert result.exit_code == 0, result.output
        assert "Imported" in result.output or "imported" in result.output.lower()

    def test_load_shows_errors_in_output(self, tmp_path: Path) -> None:
        runner = CliRunner()
        ccx_file = tmp_path / "errored.ccx"
        ccx_file.write_bytes(b"FAKE")

        mock_ctx = _make_mock_ctx()
        stats = _make_import_stats(errors=["missing required field: id"])

        with (
            patch("chaoscypher_cli.commands.package.load.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_cli.commands.package.load.asyncio.run",
                side_effect=_consume_coro(stats),
            ),
            patch("chaoscypher_cli.commands.package.load.sys.exit"),
        ):
            result = runner.invoke(load, [str(ccx_file)])

        assert "missing required field" in result.output

    def test_load_help_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(load, ["--help"])
        assert result.exit_code == 0
        assert "--merge" in result.output
        assert "--database" in result.output


# ===========================================================================
# package export command
# ===========================================================================


class TestPackageExportHappyPath:
    """export writes a .ccx file to disk and exits 0.

    The export() command lazily imports CcxExporter via
    ``from chaoscypher_core.services.export import CcxExporter`` inside the
    function body, then calls ``service.export(...) -> bytes`` and writes
    those bytes to the .ccx path. These tests patch the CcxExporter class at
    its source module and stub ``export()`` to return raw bytes (no BytesIO —
    the CCX 3.0 exporter returns ``bytes`` directly).
    """

    def test_export_calls_exporter_and_writes_file(self, tmp_path: Path) -> None:
        runner = CliRunner()
        output_file = tmp_path / "my-export.ccx"

        mock_ctx = _make_mock_ctx()
        ccx_bytes = b"PK\x03\x04FAKE_CCX_DATA"

        mock_service = MagicMock()
        mock_service.export.return_value = ccx_bytes
        mock_service.get_export_filename.return_value = "chaoscypher-export.ccx"

        with (
            patch("chaoscypher_cli.commands.package.export.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_core.services.export.CcxExporter",
                return_value=mock_service,
            ),
        ):
            result = runner.invoke(export, ["--output", str(output_file)])

        assert result.exit_code == 0, result.output
        mock_service.export.assert_called_once()
        assert output_file.exists()
        assert output_file.read_bytes() == ccx_bytes

    def test_export_output_contains_success_message(self, tmp_path: Path) -> None:
        runner = CliRunner()
        output_file = tmp_path / "out.ccx"

        mock_ctx = _make_mock_ctx()

        mock_service = MagicMock()
        mock_service.export.return_value = b"CCX"
        mock_service.get_export_filename.return_value = "default.ccx"

        with (
            patch("chaoscypher_cli.commands.package.export.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_core.services.export.CcxExporter",
                return_value=mock_service,
            ),
        ):
            result = runner.invoke(export, ["--output", str(output_file)])

        assert "Export complete" in result.output or "✓" in result.output

    def test_export_auto_filename_when_no_output_given(self, tmp_path: Path) -> None:
        """When --output is omitted, export uses service.get_export_filename()."""
        runner = CliRunner()

        mock_ctx = _make_mock_ctx()

        mock_service = MagicMock()
        mock_service.export.return_value = b"CCX"
        mock_service.get_export_filename.return_value = "chaoscypher-20260531.ccx"

        with (
            patch("chaoscypher_cli.commands.package.export.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_core.services.export.CcxExporter",
                return_value=mock_service,
            ),
        ):
            with runner.isolated_filesystem(temp_dir=tmp_path):
                result = runner.invoke(export, [])

        assert result.exit_code == 0, result.output
        mock_service.get_export_filename.assert_called_once()

    def test_export_adds_ccx_extension_when_missing(self, tmp_path: Path) -> None:
        """Output path without .ccx extension gets .ccx appended."""
        runner = CliRunner()
        output_no_ext = tmp_path / "my-export"

        mock_ctx = _make_mock_ctx()

        mock_service = MagicMock()
        mock_service.export.return_value = b"CCX"
        mock_service.get_export_filename.return_value = "default.ccx"

        with (
            patch("chaoscypher_cli.commands.package.export.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_core.services.export.CcxExporter",
                return_value=mock_service,
            ),
        ):
            result = runner.invoke(export, ["--output", str(output_no_ext)])

        assert result.exit_code == 0, result.output
        assert (tmp_path / "my-export.ccx").exists()

    def test_export_no_embeddings_by_default(self, tmp_path: Path) -> None:
        runner = CliRunner()
        output_file = tmp_path / "out.ccx"

        mock_ctx = _make_mock_ctx()

        mock_service = MagicMock()
        mock_service.export.return_value = b"CCX"

        with (
            patch("chaoscypher_cli.commands.package.export.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_core.services.export.CcxExporter",
                return_value=mock_service,
            ),
        ):
            result = runner.invoke(export, ["--output", str(output_file)])

        assert result.exit_code == 0, result.output
        call_kwargs = mock_service.export.call_args[1]
        assert call_kwargs["include_embeddings"] is False

    def test_export_embeddings_flag(self, tmp_path: Path) -> None:
        runner = CliRunner()
        output_file = tmp_path / "out.ccx"

        mock_ctx = _make_mock_ctx()

        mock_service = MagicMock()
        mock_service.export.return_value = b"CCX"

        with (
            patch("chaoscypher_cli.commands.package.export.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_core.services.export.CcxExporter",
                return_value=mock_service,
            ),
        ):
            result = runner.invoke(export, ["--output", str(output_file), "--embeddings"])

        assert result.exit_code == 0, result.output
        call_kwargs = mock_service.export.call_args[1]
        assert call_kwargs["include_embeddings"] is True

    def test_export_lens_id_forwarded(self, tmp_path: Path) -> None:
        runner = CliRunner()
        output_file = tmp_path / "out.ccx"

        mock_ctx = _make_mock_ctx()

        mock_service = MagicMock()
        mock_service.export.return_value = b"CCX"

        with (
            patch("chaoscypher_cli.commands.package.export.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_core.services.export.CcxExporter",
                return_value=mock_service,
            ),
        ):
            result = runner.invoke(
                export, ["--output", str(output_file), "--lens-id", "lens_abc123"]
            )

        assert result.exit_code == 0, result.output
        call_kwargs = mock_service.export.call_args[1]
        assert call_kwargs["lens_id"] == "lens_abc123"

    def test_export_title_forwarded(self, tmp_path: Path) -> None:
        runner = CliRunner()
        output_file = tmp_path / "out.ccx"

        mock_ctx = _make_mock_ctx()

        mock_service = MagicMock()
        mock_service.export.return_value = b"CCX"

        with (
            patch("chaoscypher_cli.commands.package.export.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_core.services.export.CcxExporter",
                return_value=mock_service,
            ),
        ):
            result = runner.invoke(export, ["--output", str(output_file), "--title", "My Research"])

        assert result.exit_code == 0, result.output
        call_kwargs = mock_service.export.call_args[1]
        assert call_kwargs["title"] == "My Research"

    def test_export_no_workflows_flag(self, tmp_path: Path) -> None:
        runner = CliRunner()
        output_file = tmp_path / "out.ccx"

        mock_ctx = _make_mock_ctx()

        mock_service = MagicMock()
        mock_service.export.return_value = b"CCX"

        with (
            patch("chaoscypher_cli.commands.package.export.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_core.services.export.CcxExporter",
                return_value=mock_service,
            ),
        ):
            result = runner.invoke(export, ["--output", str(output_file), "--no-workflows"])

        assert result.exit_code == 0, result.output
        call_kwargs = mock_service.export.call_args[1]
        assert call_kwargs["include_workflows"] is False

    def test_export_shows_file_size_in_output(self, tmp_path: Path) -> None:
        runner = CliRunner()
        output_file = tmp_path / "out.ccx"

        mock_ctx = _make_mock_ctx()

        mock_service = MagicMock()
        mock_service.export.return_value = b"X" * 2048

        with (
            patch("chaoscypher_cli.commands.package.export.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_core.services.export.CcxExporter",
                return_value=mock_service,
            ),
        ):
            result = runner.invoke(export, ["--output", str(output_file)])

        assert result.exit_code == 0, result.output
        assert "KB" in result.output or "B" in result.output

    def test_export_include_sources_false(self, tmp_path: Path) -> None:
        """include_sources must always be False from CLI."""
        runner = CliRunner()
        output_file = tmp_path / "out.ccx"

        mock_ctx = _make_mock_ctx()

        mock_service = MagicMock()
        mock_service.export.return_value = b"CCX"

        with (
            patch("chaoscypher_cli.commands.package.export.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_core.services.export.CcxExporter",
                return_value=mock_service,
            ),
        ):
            result = runner.invoke(export, ["--output", str(output_file)])

        assert result.exit_code == 0, result.output
        call_kwargs = mock_service.export.call_args[1]
        assert call_kwargs["include_sources"] is False


class TestPackageExportErrors:
    """export exits 1 on runtime errors and shows the error message."""

    def test_export_exits_1_on_exception(self, tmp_path: Path) -> None:
        runner = CliRunner()
        output_file = tmp_path / "out.ccx"

        mock_ctx = _make_mock_ctx()

        with (
            patch("chaoscypher_cli.commands.package.export.get_context", return_value=mock_ctx),
            patch(
                "chaoscypher_core.services.export.CcxExporter",
                side_effect=RuntimeError("Graph DB error"),
            ),
            patch("chaoscypher_cli.commands.package.export.sys.exit") as mock_exit,
        ):
            result = runner.invoke(export, ["--output", str(output_file)])

        mock_exit.assert_any_call(1)
        assert "Graph DB error" in result.output or "Error" in result.output

    def test_export_all_false_guard(self) -> None:
        """All --no-* flags should raise UsageError before get_context."""
        runner = CliRunner()
        result = runner.invoke(
            export,
            [
                "--no-templates",
                "--no-knowledge",
                "--no-lenses",
                "--no-workflows",
            ],
        )
        assert result.exit_code != 0
        assert "at least one content type required" in result.output

    def test_export_help_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(export, ["--help"])
        assert result.exit_code == 0
        assert "--output" in result.output
        assert "--embeddings" in result.output

    def test_export_database_flag(self, tmp_path: Path) -> None:
        runner = CliRunner()
        output_file = tmp_path / "out.ccx"

        mock_ctx = _make_mock_ctx("analytics")

        mock_service = MagicMock()
        mock_service.export.return_value = b"CCX"

        with (
            patch(
                "chaoscypher_cli.commands.package.export.get_context", return_value=mock_ctx
            ) as mock_gc,
            patch(
                "chaoscypher_core.services.export.CcxExporter",
                return_value=mock_service,
            ),
        ):
            result = runner.invoke(
                export, ["--output", str(output_file), "--database", "analytics"]
            )

        assert result.exit_code == 0, result.output
        mock_gc.assert_called_once_with(database_name="analytics")
