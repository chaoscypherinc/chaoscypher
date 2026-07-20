# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Link Commands - Chaos Cypher CLI.

Commands for managing links (edges) between nodes:
- list: Show all links with filtering
- create: Link two nodes together
- get: Show link details
- update: Modify a link
- delete: Remove a link between nodes

Links represent relationships between nodes in the knowledge graph.
They are directed and can have relationship types.

Example:
    chaoscypher graph link list
    chaoscypher graph link create node-1 node-2 --type "works_for"
    chaoscypher graph link get edge-123
    chaoscypher graph link update edge-123 --label "reports_to"
    chaoscypher graph link delete edge-123
"""

import click

from chaoscypher_cli.commands.link.create import create
from chaoscypher_cli.commands.link.delete import delete
from chaoscypher_cli.commands.link.get import get
from chaoscypher_cli.commands.link.list import list_links
from chaoscypher_cli.commands.link.update import update


@click.group()
def link() -> None:
    """Manage links between nodes in the knowledge graph.

    Links (edges) represent relationships between nodes.
    Use these commands to create, view, update, and remove connections.
    """


link.add_command(list_links, name="list")
link.add_command(create)
link.add_command(get)
link.add_command(update)
link.add_command(delete)

__all__ = ["link"]
