# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Console Utilities - Rich console helpers.

Provides reusable console formatting and output utilities.

Example:
    from chaoscypher_cli.utils.console import get_console, print_error

    console = get_console()
    console.print("[green]Success![/green]")

    print_error("something went wrong")
"""

from rich.console import Console


_console: Console | None = None


def get_console() -> Console:
    """Get singleton console instance."""
    global _console
    if _console is None:
        _console = Console()
    return _console


def print_json(payload: str) -> None:
    """Print a pre-serialized JSON string verbatim — no wrapping, no markup.

    Machine-readable output must never be hard-wrapped to the terminal width.
    When stdout is not a TTY (pipes, CI runners) Rich defaults to an 80-column
    console and would otherwise insert newlines mid-token, corrupting the JSON
    so it can no longer be parsed downstream. ``soft_wrap=True`` emits the
    string verbatim regardless of console width.

    ``markup=False`` and ``highlight=False`` keep user data intact: a value
    containing ``[bold]``-like substrings would otherwise be swallowed as Rich
    markup (or crash with ``MarkupError`` on an unbalanced ``[/red]``), and
    syntax highlighting would inject ANSI codes into piped output.

    Args:
        payload: An already-serialized JSON document (e.g. ``json.dumps(...)``).
    """
    get_console().print(payload, soft_wrap=True, markup=False, highlight=False)


def print_unwrapped(message: str) -> None:
    """Print one line of markup output without hard-wrapping it to the width.

    Used for output that must stay on a single logical line even on a narrow,
    non-TTY console (e.g. filesystem paths in ``Location:`` lines, which break
    downstream tooling and look broken to users if split mid-path). Rich style
    markup is still honored; only width-based wrapping is disabled.

    Args:
        message: The (optionally markup-tagged) line to print verbatim.
    """
    get_console().print(message, soft_wrap=True)


def print_error(message: str) -> None:
    """Print error message in red."""
    console = get_console()
    console.print(f"[red]Error:[/red] {message}")


def print_success(message: str) -> None:
    """Print success message in green."""
    console = get_console()
    console.print(f"[green]Success:[/green] {message}")
