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

from collections.abc import Sequence

import structlog
from rich.console import Console


_logger = structlog.get_logger(__name__)

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


def print_error(message: str, *, console: Console | None = None) -> None:
    """Print error message in red, on one line however narrow the console.

    ``soft_wrap`` keeps a long message (a database path, a package name) on one
    logical line: a caller that keeps only the last line of a failed command's
    output must get the whole sentence, not its tail.

    Args:
        message: The message; Rich markup in it is honored.
        console: Where to print (a command's own console, a stderr one);
            the shared console when None.
    """
    (console or get_console()).print(f"[red]Error:[/red] {message}", soft_wrap=True)


def print_caught_error(
    exc: BaseException,
    *,
    label: str | None = "Error",
    console: Console | None = None,
    style: str = "red",
    suffix: str = "",
) -> None:
    """Print an exception a command caught as one line: ``<label>: <error_text(exc)><suffix>``.

    The one way a command prints a caught exception. A database error reads as
    its plain sentence (``error_text``), the line is never hard-wrapped (piped
    output is 80 columns wide, and a caller keeping only the last line must get
    all of it), and only the label is styled: the error text is escaped, so
    brackets in it print as written instead of being read as markup.

    Args:
        exc: The exception.
        label: What failed (``"Error"``, ``"Migration failed"``, ...); None
            prints the error text alone, in ``style``.
        console: Where to print; the shared console when None.
        style: The label's Rich style.
        suffix: Plain text after the error (e.g. what happens next).
    """
    from rich.markup import escape

    text = escape(error_text(exc) + suffix)
    line = (
        f"[{style}]{text}[/{style}]"
        if label is None
        else f"[{style}]{escape(label)}:[/{style}] {text}"
    )
    (console or get_console()).print(line, soft_wrap=True)


def error_text(exc: BaseException) -> str:
    """The text to print for an exception a command caught.

    A database error becomes one plain sentence (through core's
    ``describe_database_error``, the one translator), never SQLAlchemy's
    multi-line message with the SQL statement and its link; anything else
    prints as it always has, ``str(exc)``.
    """
    from chaoscypher_core.database.errors import describe_database_error

    described = describe_database_error(exc)
    if described is None:
        return str(exc)
    # The printed line drops the SQL text and SQLAlchemy's link; the debug log keeps
    # the whole exception so LOG_LEVEL=DEBUG can still recover the failing statement.
    _logger.debug("cli_caught_database_error", error=described, exc_info=exc)
    return described


def print_failure(headline: str, errors: Sequence[str]) -> None:
    """Print a failure, its errors, and end with one plain ``<headline>: <first error>`` line.

    The bullet list is for a person reading the whole output; the closing line is
    for a caller that keeps only the last line (Tsukumo runs ``compose build``
    and keeps just that), so it says what failed on its own, unwrapped and with
    no markup interpretation of the error text.
    """
    from rich.markup import escape

    print_error(headline)
    console = get_console()
    for error in errors:
        console.print(f"  [red]✗[/red] {escape(error)}")
    if errors:
        console.print(f"{headline}: {errors[0]}", soft_wrap=True, markup=False, highlight=False)


def print_success(message: str) -> None:
    """Print success message in green."""
    console = get_console()
    console.print(f"[green]Success:[/green] {message}")
