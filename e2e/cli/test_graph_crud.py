# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for CLI graph node and link commands."""

from collections.abc import Callable


def _extract_id(output: str, prefix: str = "ID:") -> str:
    """Extract an ID value from CLI output."""
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if prefix in line:
            return line.split(prefix)[-1].strip()
    msg = f"Could not find '{prefix}' in output:\n{output}"
    raise ValueError(msg)


class TestGraphNodeCrud:
    """Test chaoscypher graph node create/list/get/delete commands."""

    def _create_template(self, run_cli: Callable, cli_env: dict[str, str], name: str) -> str:
        """Create a template and return its ID."""
        result = run_cli(
            [
                "graph",
                "template",
                "create",
                "--name",
                name,
                "--property",
                "name:string",
            ],
            env=cli_env,
        )
        assert result.exit_code == 0, f"Template create failed: {result.output}"
        return _extract_id(result.output)

    def test_create_node(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Creating a node with a template succeeds."""
        template_id = self._create_template(run_cli, cli_env, "NodeTestPerson")
        result = run_cli(
            [
                "graph",
                "node",
                "create",
                "--template",
                template_id,
                "--label",
                "Alice Test",
                "--property",
                "name=Alice Test",
            ],
            env=cli_env,
        )
        assert result.exit_code == 0, f"Failed: {result.output}"

    def test_list_nodes(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Listing nodes shows created nodes."""
        template_id = self._create_template(run_cli, cli_env, "ListNodePerson")
        run_cli(
            [
                "graph",
                "node",
                "create",
                "--template",
                template_id,
                "--label",
                "Bob ListTest",
                "--property",
                "name=Bob ListTest",
            ],
            env=cli_env,
        )

        result = run_cli(["graph", "node", "list"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert "Bob ListTest" in result.output


class TestGraphLinkCrud:
    """Test chaoscypher graph link create/list commands."""

    def _setup_two_nodes(self, run_cli: Callable, cli_env: dict[str, str]) -> tuple[str, str, str]:
        """Create templates and two nodes, return (node_a_id, node_b_id, edge_template_id)."""
        tmpl_result = run_cli(
            [
                "graph",
                "template",
                "create",
                "--name",
                "LinkTestPerson",
                "--property",
                "name:string",
            ],
            env=cli_env,
        )
        template_id = _extract_id(tmpl_result.output)

        # Create edge template for linking
        edge_tmpl_result = run_cli(
            [
                "graph",
                "template",
                "create",
                "--name",
                "knows",
                "--type",
                "edge",
                "--property",
                "since:string",
            ],
            env=cli_env,
        )
        edge_template_id = _extract_id(edge_tmpl_result.output)

        node_a_result = run_cli(
            [
                "graph",
                "node",
                "create",
                "--template",
                template_id,
                "--label",
                "Link Node A",
                "--property",
                "name=Link Node A",
            ],
            env=cli_env,
        )
        assert node_a_result.exit_code == 0
        node_a_id = _extract_id(node_a_result.output)

        node_b_result = run_cli(
            [
                "graph",
                "node",
                "create",
                "--template",
                template_id,
                "--label",
                "Link Node B",
                "--property",
                "name=Link Node B",
            ],
            env=cli_env,
        )
        assert node_b_result.exit_code == 0
        node_b_id = _extract_id(node_b_result.output)

        return node_a_id, node_b_id, edge_template_id

    def test_create_link(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Creating a link between two nodes succeeds."""
        src_id, tgt_id, edge_tmpl_id = self._setup_two_nodes(run_cli, cli_env)
        result = run_cli(
            ["graph", "link", "create", src_id, tgt_id, "--type", edge_tmpl_id],
            env=cli_env,
        )
        assert result.exit_code == 0, f"Failed: {result.output}"

    def test_list_links(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Listing links shows created relationships."""
        src_id, tgt_id, edge_tmpl_id = self._setup_two_nodes(run_cli, cli_env)
        run_cli(
            ["graph", "link", "create", src_id, tgt_id, "--type", edge_tmpl_id],
            env=cli_env,
        )

        result = run_cli(["graph", "link", "list"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
