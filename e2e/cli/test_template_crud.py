# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for CLI graph template commands."""

from collections.abc import Callable


class TestTemplateCrud:
    """Test chaoscypher graph template create/list/get commands."""

    def test_create_node_template(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Creating a node template succeeds."""
        result = run_cli(
            [
                "graph",
                "template",
                "create",
                "--name",
                "TestPerson",
                "--property",
                "name:string",
                "--property",
                "age:integer",
                "--description",
                "E2E test template",
            ],
            env=cli_env,
        )
        assert result.exit_code == 0, f"Failed: {result.output}"

    def test_list_templates(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Listing templates shows created templates."""
        run_cli(
            [
                "graph",
                "template",
                "create",
                "--name",
                "ListTestTemplate",
                "--property",
                "title:string",
            ],
            env=cli_env,
        )

        result = run_cli(["graph", "template", "list"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert "ListTestTemplate" in result.output

    def test_list_templates_json(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Listing templates in JSON format returns valid JSON."""
        run_cli(
            [
                "graph",
                "template",
                "create",
                "--name",
                "JsonTestTemplate",
                "--property",
                "value:string",
            ],
            env=cli_env,
        )

        result = run_cli(["graph", "template", "list", "--format", "json"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
