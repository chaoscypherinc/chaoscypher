# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph commands - Build and manage knowledge graphs.

Create and manipulate nodes, links, templates, workflows, and packages.

Example:
    chaoscypher graph node list
    chaoscypher graph link create node1 node2 --label "knows"
    chaoscypher graph template list
"""

import click

from chaoscypher_cli.lazy import LazyGroup


LAZY_SUBCOMMANDS = {
    "node": ("chaoscypher_cli.commands.node:node", "Manipulate nodes in the graph"),
    "link": ("chaoscypher_cli.commands.link:link", "Manage links between nodes"),
    "template": ("chaoscypher_cli.commands.template:template", "Manage knowledge templates"),
    "workflow": ("chaoscypher_cli.commands.workflow:workflow", "View knowledge workflows"),
    "package": ("chaoscypher_cli.commands.package:package", "Manage .ccx knowledge packages"),
}


@click.group(cls=LazyGroup, lazy_subcommands=LAZY_SUBCOMMANDS)
def graph() -> None:
    """Build and manage knowledge graphs."""


__all__ = ["graph"]
