# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for CLI database management commands."""

from collections.abc import Callable


class TestDatabaseLifecycle:
    """Test chaoscypher db create/list/current commands."""

    def test_create_database(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Creating a database succeeds."""
        result = run_cli(["db", "create", "e2e-create-test"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert "e2e-create-test" in result.output.lower()

    def test_current_database(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Current database command reports the active database."""
        result = run_cli(["db", "current"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert len(result.output.strip()) > 0

    def test_list_databases_json(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Listing databases in JSON format returns valid output.

        A fresh data dir has no databases ("[]" is correct), so create one
        first and assert it's listed — the previous arrangement asserted a
        "default" database that nothing in the fixture ever created.
        """
        create = run_cli(["db", "create", "e2e-json-test"], env=cli_env)
        assert create.exit_code == 0, f"Failed: {create.output}"
        result = run_cli(["db", "list", "--json"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert "e2e-json-test" in result.output
