# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Commands - ChaosCypher CLI.

Commands for viewing workflows:
- list: Show available workflows
- get: Show workflow details and steps

Workflow creation and execution requires the web UI or API.
"""

import click

from chaoscypher_cli.commands.workflow.get import get
from chaoscypher_cli.commands.workflow.list import list_workflows


@click.group()
def workflow() -> None:
    """View knowledge workflows.

    Workflows automate complex operations on knowledge graphs.
    Use the web UI to create and execute workflows.
    """


workflow.add_command(list_workflows, name="list")
workflow.add_command(get)

__all__ = ["workflow"]
