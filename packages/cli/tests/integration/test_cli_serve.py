# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for CLI serve command.

Verifies that the CLI serve command is properly configured to launch
Cortex for local knowledge graph access.

The tests import the ``serve`` Click command directly (rather than going
through the top-level ``main`` LazyGroup) because LazyGroup's
``_is_being_invoked`` check reads ``sys.argv``, which is pytest's argv
under CliRunner — so the real command never replaces the stub during
``serve --help`` invocations under test. Importing the command directly
sidesteps that lazy-loading quirk.
"""

from unittest.mock import patch

import pytest
from click.testing import CliRunner


# (Click parameter name, the flags it must expose). Asserted against
# ``serve.params`` rather than ``--help`` text: the help output cannot fail
# these checks, because the command's own docstring Example block already
# contains "--port", "--database" and "--reload", and Click's always-present
# "--help  Show this message and exit." line contains the substring "-h".
# Rebuilding serve with EVERY option deleted still satisfied all four of the
# old text assertions.
_EXPECTED_SERVE_OPTIONS = [
    ("port", ("--port", "-p")),
    ("host_addr", ("--host", "-h")),
    ("database", ("--database", "-d")),
    ("reload", ("--reload",)),
]


@pytest.mark.integration
class TestCliServeCommand:
    """Test CLI serve command configuration."""

    def test_serve_command_exists(self):
        """Serve command is registered in CLI."""
        from chaoscypher_cli.commands.runtime.serve import serve

        runner = CliRunner()
        result = runner.invoke(serve, ["--help"])

        assert result.exit_code == 0
        assert "Start the local API server" in result.output

    @pytest.mark.parametrize(
        ("param_name", "flags"),
        _EXPECTED_SERVE_OPTIONS,
        ids=[flags[0] for _, flags in _EXPECTED_SERVE_OPTIONS],
    )
    def test_serve_declares_option(self, param_name, flags):
        """Serve declares each documented option as a real Click parameter."""
        from chaoscypher_cli.commands.runtime.serve import serve

        params = {p.name: p for p in serve.params}
        assert param_name in params, (
            f"serve does not declare {flags[0]}; declared params: {sorted(params)}"
        )
        assert set(params[param_name].opts) == set(flags)

    def test_serve_options_are_usable(self):
        """Each option carries the type / default that makes it work.

        A declared-but-mistyped option is as broken as a missing one, so
        the shapes the docstring's examples rely on are pinned here.
        """
        from chaoscypher_cli.commands.runtime.serve import serve

        params = {p.name: p for p in serve.params}

        assert params["port"].type.name == "integer"
        assert params["host_addr"].default == "localhost"
        assert params["database"].default == "default"
        assert params["reload"].is_flag is True

    def test_serve_help_mentions_local_server(self):
        """Serve command help mentions a local API server."""
        from chaoscypher_cli.commands.runtime.serve import serve

        runner = CliRunner()
        result = runner.invoke(serve, ["--help"])

        assert result.exit_code == 0
        # The serve command's purpose is to launch a local API server.
        assert "local" in result.output.lower() or "API server" in result.output


@pytest.mark.integration
class TestCliServeIntegration:
    """Test CLI serve command integration with context."""

    def test_serve_with_new_database_auto_creates(self, tmp_path, monkeypatch):
        """Serve with a new database name auto-creates the database.

        The CLI auto-initializes new databases — this is by design for ease
        of use. We mock ``subprocess.run`` and the Cortex availability check
        so the test never actually launches a server; we just confirm the
        command renders the database panel before it would hand off to
        Cortex.
        """
        from chaoscypher_cli.commands.runtime.serve import serve

        # Isolate the CLI's data dir to tmp_path so it doesn't touch the
        # operator's real databases.
        monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))

        runner = CliRunner()

        # Patch both the cortex-available probe and the builtin server so
        # the command renders the database stats panel and then exits
        # cleanly instead of spawning uvicorn. ``importlib.util.find_spec``
        # is imported lazily inside the function, so we patch the global
        # symbol.
        with (
            patch("importlib.util.find_spec", return_value=None),
            patch("chaoscypher_cli.commands.runtime.serve._run_builtin_server"),
        ):
            result = runner.invoke(
                serve,
                ["--database", "test_auto_create_db"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        assert "test_auto_create_db" in result.output
