# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Lazy loading support for Click commands.

Provides a LazyGroup class that defers command module imports until
the command is actually executed. This significantly improves CLI
startup time for large applications with expensive imports.

Usage:
    from chaoscypher_cli.lazy import LazyGroup

    # Simple format (loads command for help text)
    LAZY_COMMANDS = {
        "serve": "chaoscypher_cli.commands.runtime.serve:serve",
    }

    # Tuple format with help text (no loading needed for --help)
    LAZY_COMMANDS = {
        "serve": ("chaoscypher_cli.commands.runtime.serve:serve", "Start the server"),
    }

    @click.group(cls=LazyGroup, lazy_subcommands=LAZY_COMMANDS)
    def main():
        '''My CLI application.'''

Example:
    # Commands are imported only when executed
    $ mycli --help        # Fast - uses cached help text
    $ mycli serve         # Loads serve module on demand
"""

from __future__ import annotations

import importlib
from typing import Any

import click


class LazyGroup(click.Group):
    """A Click group that lazily loads commands on demand.

    This class extends Click's Group to support lazy loading of subcommands.
    Commands are specified as import path strings and only imported when
    the command is actually invoked.

    During shell completion or help rendering, returns stub commands with
    cached help text to avoid heavy imports.

    Attributes:
        _lazy_subcommands: Mapping of command names to import paths.
            Format: {"cmd": "module.path:attr"} or {"cmd": ("module.path:attr", "help")}

    Example:
        lazy_cmds = {
            "serve": ("myapp.commands.serve:serve_cmd", "Start the server"),
            "build": ("myapp.commands.build:build_cmd", "Build the project"),
        }

        @click.group(cls=LazyGroup, lazy_subcommands=lazy_cmds)
        def cli():
            pass

    """

    def __init__(
        self,
        *args: Any,
        lazy_subcommands: dict[str, str | tuple[str, str]] | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the lazy group.

        Args:
            *args: Positional arguments passed to click.Group.
            lazy_subcommands: Mapping of command names to import paths.
                Format: {"cmd": "path:attr"} or {"cmd": ("path:attr", "help text")}
            **kwargs: Keyword arguments passed to click.Group.

        """
        super().__init__(*args, **kwargs)
        # Normalize to dict of (import_path, help_text) tuples
        self._lazy_subcommands: dict[str, tuple[str, str | None]] = {}
        for name, value in (lazy_subcommands or {}).items():
            if isinstance(value, tuple):
                self._lazy_subcommands[name] = value
            else:
                self._lazy_subcommands[name] = (value, None)

    def list_commands(self, ctx: click.Context) -> list[str]:
        """List all available commands including lazy ones.

        Args:
            ctx: The Click context.

        Returns:
            Sorted list of all command names (eager + lazy).

        """
        base = super().list_commands(ctx)
        lazy = list(self._lazy_subcommands.keys())
        return sorted(set(base + lazy))

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        """Get a command by name, loading lazily if needed.

        Returns a stub command (with cached help text) whenever the command
        is not being directly invoked. This avoids expensive imports during
        --help rendering and shell completion.

        Args:
            ctx: The Click context.
            cmd_name: The name of the command to retrieve.

        Returns:
            The Click command, or None if not found.

        """
        if cmd_name in self._lazy_subcommands:
            import_path, help_text = self._lazy_subcommands[cmd_name]

            # Only load the real module when the command is being invoked.
            # During --help, format_commands() calls get_command() for every
            # subcommand to build the help listing — return stubs for those.
            if self._is_being_invoked(cmd_name):
                return self._load_command(cmd_name, import_path)

            # Not being invoked — return stub with cached help text
            if help_text is not None:
                return _StubCommand(cmd_name, help_text)

            # No cached help text — must load to get it
            return self._load_command(cmd_name, import_path)

        return super().get_command(ctx, cmd_name)

    @staticmethod
    def _is_being_invoked(cmd_name: str) -> bool:
        """Check if a command is being invoked (not just listed for help).

        Compares the command name against sys.argv to determine if the user
        is actually running this command vs Click listing it for --help.

        Args:
            cmd_name: The command name to check.

        Returns:
            True if the command appears in sys.argv (being invoked).

        """
        import sys

        return cmd_name in sys.argv[1:]

    def _load_command(self, cmd_name: str, import_path: str) -> click.Command:
        """Load a lazy command by importing its module.

        Args:
            cmd_name: The name of the command to load.
            import_path: The import path (module.path:attr).

        Returns:
            The loaded Click command.

        Raises:
            ImportError: If the module cannot be imported.
            AttributeError: If the attribute doesn't exist in the module.

        """
        module_path, attr_name = import_path.rsplit(":", 1)
        module = importlib.import_module(module_path)
        cmd: click.Command = getattr(module, attr_name)
        return cmd


class _StubCommand(click.Command):
    """Minimal stub command for shell completion and help rendering.

    Returns cached help text to avoid loading heavy modules during
    tab completion or --help. The stub is replaced with the real
    command when actually executed.
    """

    def __init__(self, name: str, help_text: str | None = None) -> None:
        """Initialize stub command with name and optional help text."""
        super().__init__(name=name, callback=lambda: None)
        self._help_text = help_text or ""

    def get_short_help_str(self, limit: int = 45) -> str:
        """Return cached help text."""
        return self._help_text


__all__ = ["LazyGroup"]
