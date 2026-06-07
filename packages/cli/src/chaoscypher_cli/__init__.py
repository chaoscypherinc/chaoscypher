# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chaos Cypher CLI - Command-line tools for Chaos Cypher knowledge graph library.

This package provides a user-friendly command-line interface for:
- Managing knowledge graphs and databases
- Importing and exporting data
- Searching and querying graphs
- Viewing statistics and analytics

Example:
    chaoscypher db create my-graph
    chaoscypher source add documents/
    chaoscypher source search "artificial intelligence"
"""

from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("chaoscypher-cli")
except PackageNotFoundError:
    # Source-tree run without an installed dist (e.g. `python -m chaoscypher_cli`
    # from a checkout that wasn't `uv sync`'d). Fall back to a sentinel so the
    # CLI still launches.
    __version__ = "0.0.0+unknown"

__author__ = "Denis MacPherson"
__license__ = "AGPL-3.0-only"

__all__ = ["__version__"]
