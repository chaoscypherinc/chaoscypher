# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Lexicon Hub commands - Authentication and package management.

Manage your Lexicon Hub account and browse/install packages.

Example:
    chaoscypher lexicon login
    chaoscypher lexicon search "ontology"
    chaoscypher lexicon list
"""

import click

from chaoscypher_cli.lazy import LazyGroup


LAZY_SUBCOMMANDS = {
    "login": ("chaoscypher_cli.commands.lexicon.login:login", "Authenticate with Lexicon Hub"),
    "logout": ("chaoscypher_cli.commands.lexicon.login:logout", "Clear authentication"),
    "whoami": ("chaoscypher_cli.commands.lexicon.login:whoami", "Show current user"),
    "search": ("chaoscypher_cli.commands.lexicon.search:search", "Search the hub"),
    "list": ("chaoscypher_cli.commands.lexicon.list:list_packages", "List installed packages"),
    "info": ("chaoscypher_cli.commands.lexicon.info:info", "Show package details"),
    "remove": ("chaoscypher_cli.commands.lexicon.remove:remove", "Remove a package"),
    "pull": ("chaoscypher_cli.commands.lexicon.pull:pull", "Download a package"),
    "push": ("chaoscypher_cli.commands.lexicon.push:push", "Upload a package"),
}


@click.group(cls=LazyGroup, lazy_subcommands=LAZY_SUBCOMMANDS)
def lexicon() -> None:
    """Lexicon Hub - Authentication and package management."""


__all__ = ["lexicon"]
