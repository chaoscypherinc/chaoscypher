# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Node Commands - ChaosCypher CLI.

Commands for manipulating nodes in the knowledge graph:
- list: Show all nodes with filtering
- create: Add a new node (interactive wizard or flags)
- get: Show details of a node
- update: Modify an existing node
- delete: Remove a node

Example:
    chaoscypher graph node list
    chaoscypher graph node create
    chaoscypher graph node get node-123
    chaoscypher graph node update node-123 --label "New Name"
    chaoscypher graph node delete node-123
"""

import click

from chaoscypher_cli.commands.node.create import create
from chaoscypher_cli.commands.node.delete import delete
from chaoscypher_cli.commands.node.get import get
from chaoscypher_cli.commands.node.list import list_nodes
from chaoscypher_cli.commands.node.update import update


@click.group()
def node() -> None:
    """Manipulate nodes in the knowledge graph.

    Nodes are the fundamental units of knowledge in ChaosCypher.
    Use these commands to create, read, update, and delete nodes.
    """


node.add_command(list_nodes, name="list")
node.add_command(create)
node.add_command(get)
node.add_command(update)
node.add_command(delete)

__all__ = ["node"]
