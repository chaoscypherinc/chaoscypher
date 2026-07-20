# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Config command - View and manage Chaos Cypher configuration.

Operates on the unified ``settings.yaml`` (engine + client config) via the
shared ``ConfigManager`` — the same validated write path the web settings
PATCH endpoint uses. The legacy client-only ``cli.yaml`` was retired in the
2026-06 config unification.

Example:
    chaoscypher config show
    chaoscypher config get llm.chat_provider
    chaoscypher config set lexicon.timeout 60
    chaoscypher config edit
    chaoscypher config path
"""

import json
import os
import subprocess
import sys
from typing import Any

import click
from rich.console import Console
from rich.tree import Tree

from chaoscypher_cli import engine_config


console = Console()


@click.group()
def config() -> None:
    """View and manage Chaos Cypher configuration.

    Configuration is stored as YAML in the data directory:
    <data_dir>/settings.yaml

    The location follows CHAOSCYPHER_DATA_DIR (default: the platform user
    data directory). Individual values can also be overridden at runtime via
    CHAOSCYPHER_* environment variables, which take precedence over the file.

    The active database is managed separately — use
    `chaoscypher db switch <name>` to change it.
    """


def _masked_settings_dict() -> dict[str, Any]:
    """Return the current engine settings as a JSON-safe, secret-masked dict.

    ``mask_settings_dict`` mutates in place, so we feed it a fresh
    ``model_dump(mode="json")`` copy (which also unwraps Paths/SecretStr to
    plain strings) rather than the live Settings model.
    """
    from chaoscypher_core.app_config import get_settings, mask_settings_dict

    return mask_settings_dict(get_settings().model_dump(mode="json"))


def _settings_file_footer() -> None:
    """Print the settings.yaml location + exists/defaults status."""
    settings_path = engine_config.settings_yaml_path()
    console.print(f"\n[dim]Config file: {settings_path}[/dim]")
    if settings_path.exists():
        console.print("[dim]Status: exists[/dim]")
    else:
        console.print("[dim]Status: using defaults (no file)[/dim]")


@config.command("show")
@click.option(
    "--format",
    "-f",
    "output_format",
    default="tree",
    type=click.Choice(["tree", "json", "yaml"]),
    help="Output format",
)
def show_config(output_format: str) -> None:
    """Show current configuration.

    Displays the effective settings (code defaults + settings.yaml +
    environment overrides). Secret-bearing fields are masked.

    Example:
        chaoscypher config show
        chaoscypher config show --format json
    """
    try:
        masked = _masked_settings_dict()

        # JSON/YAML go through click.echo (plain): Rich would interpret the
        # ``[...]`` brackets as console markup and soft-wrap long lines,
        # corrupting machine-readable output.
        if output_format == "json":
            click.echo(json.dumps(masked, indent=2))

        elif output_format == "yaml":
            try:
                import yaml

                click.echo(yaml.dump(masked, default_flow_style=False, sort_keys=False))
            except ImportError:
                console.print("[yellow]YAML output requires PyYAML. Using JSON.[/yellow]")
                click.echo(json.dumps(masked, indent=2))

        else:  # tree format
            tree = Tree("[bold cyan]Configuration[/bold cyan]")

            # Lexicon settings (client + Cortex-shared)
            lexicon = masked.get("lexicon", {})
            lex_node = tree.add("[bold]lexicon[/bold]")
            lex_node.add(f"url: {lexicon.get('url')}")
            lex_node.add(f"timeout: {lexicon.get('timeout')}")
            lex_node.add(f"max_retries: {lexicon.get('max_retries')}")
            lex_node.add(f"token: {lexicon.get('token') or 'not set'}")

            # LLM and embedding engine config.
            llm = masked.get("llm", {})
            llm_node = tree.add("[bold]llm[/bold]")
            llm_node.add(f"chat_provider: {llm.get('chat_provider')}")

            embedding = masked.get("embedding", {})
            emb_node = tree.add("[bold]embedding[/bold]")
            emb_node.add(f"provider: {embedding.get('provider')}")
            emb_node.add(f"model: {embedding.get('model')}")

            # Resolved filesystem paths.
            paths = masked.get("paths", {})
            paths_node = tree.add("[bold]paths[/bold]")
            paths_node.add(f"data_dir: {paths.get('data_dir')}")
            paths_node.add(f"config_dir: {paths.get('config_dir')}")
            paths_node.add(f"cache_dir: {paths.get('cache_dir')}")

            # Active database (managed via `db switch`)
            tree.add(f"[bold]current_database[/bold]: {masked.get('current_database')}")

            console.print(tree)
            _settings_file_footer()

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@config.command("get")
@click.argument("key")
def get_value(key: str) -> None:
    """Get a specific configuration value.

    KEY is a dot-separated path like 'llm.chat_provider' or 'lexicon.timeout'.
    Secret-bearing fields are masked ('configured' / 'not set').

    Example:
        chaoscypher config get llm.chat_provider
        chaoscypher config get paths.data_dir
    """
    try:
        value = _get_nested_value(_masked_settings_dict(), key)

        if value is None:
            # Distinguish "unset secret" from "unknown key": a masked secret
            # path resolves to None too, but those keys are real fields.
            if _is_known_secret_path(key):
                console.print("not set")
                return
            console.print(f"[red]Key not found:[/red] {key}")
            sys.exit(1)

        if isinstance(value, (dict, list)):
            click.echo(json.dumps(value, indent=2))
        else:
            click.echo(str(value))

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@config.command("set")
@click.argument("key")
@click.argument("value")
def set_value(key: str, value: str) -> None:
    """Set a configuration value.

    KEY is a dot-separated path like 'lexicon.timeout' or 'llm.chat_provider'.
    VALUE is the new value to set. The change is validated against the
    settings schema and written atomically to settings.yaml.

    The active database is managed separately — use
    `chaoscypher db switch <name>` instead of setting current_database.

    Example:
        chaoscypher config set lexicon.timeout 60
        chaoscypher config set llm.chat_provider ollama
    """
    if key == "current_database":
        raise click.ClickException(
            "current_database is managed by `chaoscypher db switch <name>` "
            "(which validates the database exists)."
        )

    from pydantic import ValidationError

    from chaoscypher_core.app_config import get_config_manager
    from chaoscypher_core.exceptions import ConfigError

    parsed = _parse_value(value)
    nested: dict[str, Any] = {}
    _set_nested_value(nested, key, parsed)

    try:
        get_config_manager().update_settings(nested)
    except (ValidationError, ConfigError) as exc:
        message = f"Invalid setting '{key}':\n{exc}"
        raise click.ClickException(message) from exc

    # Never echo a secret back to the terminal/scrollback/CI logs — `config get`
    # masks these same paths, so the write path must not defeat that masking.
    shown = "configured" if _is_known_secret_path(key) else parsed
    console.print(f"[green]Set {key} = {shown}[/green]")
    console.print(f"[dim]Saved to: {engine_config.settings_yaml_path()}[/dim]")


@config.command("edit")
def edit_config() -> None:
    """Open the configuration file in an editor.

    Uses $EDITOR / $VISUAL, falling back to 'notepad' (Windows) or 'nano'.
    Creates settings.yaml from defaults if it doesn't exist yet. After the
    editor closes, the file is re-validated so syntax/value errors surface
    immediately.

    Example:
        chaoscypher config edit
    """
    from chaoscypher_core.app_config import get_config_manager

    settings_path = engine_config.settings_yaml_path()

    try:
        # ConfigManager() creates a minimal default settings.yaml when the
        # file is missing — use it instead of hand-rolling a template that
        # could drift from the real schema.
        manager = get_config_manager()
        if not settings_path.exists():
            manager.load_settings()
            console.print(f"[green]Created config file:[/green] {settings_path}")

        editor = os.environ.get("EDITOR", os.environ.get("VISUAL"))
        if not editor:
            editor = "notepad" if sys.platform == "win32" else "nano"

        console.print(f"[dim]Opening {settings_path} with {editor}...[/dim]")
        subprocess.run([editor, str(settings_path)], check=True)

    except subprocess.CalledProcessError:
        console.print("[yellow]Editor closed without saving[/yellow]")
        return
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    # Surface parse/validation errors introduced by the edit right away.
    try:
        manager.invalidate_cache()
        manager.load_settings()
    except Exception as e:
        console.print(f"[red]Saved, but the file no longer parses:[/red] {e}")
        sys.exit(1)

    console.print("[green]Configuration saved.[/green]")


@config.command("path")
def show_path() -> None:
    """Show the configuration file path.

    Example:
        chaoscypher config path
    """
    settings_path = engine_config.settings_yaml_path()
    # Plain echo: a long tmp/data path would otherwise be soft-wrapped by Rich.
    click.echo(str(settings_path))

    if settings_path.exists():
        console.print("[dim]Status: exists[/dim]")
    else:
        console.print("[dim]Status: not created yet[/dim]")


@config.command("reset")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
def reset_config(force: bool) -> None:
    """Reset configuration to defaults.

    Removes operator overrides from settings.yaml, restoring code defaults.

    Example:
        chaoscypher config reset
        chaoscypher config reset --force
    """
    settings_path = engine_config.settings_yaml_path()

    if not force:
        from rich.prompt import Confirm

        if not Confirm.ask(f"Reset {settings_path} to defaults?", default=False):
            console.print("[yellow]Cancelled.[/yellow]")
            return

    try:
        from chaoscypher_core.app_config import get_config_manager, reload_settings

        get_config_manager().reset_to_defaults()
        # Resync the module-global singleton (reset_to_defaults only updates the
        # manager's own cache) so a later read in the same process isn't stale.
        reload_settings()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    console.print("[green]Configuration reset to defaults.[/green]")
    console.print(f"[dim]Settings file: {settings_path}[/dim]")


# =============================================================================
# Helper Functions
# =============================================================================


def _is_known_secret_path(key: str) -> bool:
    """True if ``key`` is a masked secret field path (resolves to None when unset)."""
    from chaoscypher_core.app_config import _SECRET_FIELD_PATHS

    return key in _SECRET_FIELD_PATHS


def _get_nested_value(data: dict[str, Any], key: str) -> Any:
    """Get a nested value from a dictionary using dot notation."""
    keys = key.split(".")
    value: Any = data

    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        else:
            return None

    return value


def _set_nested_value(data: dict[str, Any], key: str, value: Any) -> None:
    """Set a nested value in a dictionary using dot notation."""
    keys = key.split(".")

    for k in keys[:-1]:
        if k not in data:
            data[k] = {}
        data = data[k]

    data[keys[-1]] = value


def _parse_value(value: str) -> bool | int | float | str:
    """Parse a string value to appropriate type."""
    # Boolean
    if value.lower() in ("true", "yes", "on"):
        return True
    if value.lower() in ("false", "no", "off"):
        return False

    # Integer
    try:
        return int(value)
    except ValueError:
        pass

    # Float
    try:
        return float(value)
    except ValueError:
        pass

    # String
    return value
