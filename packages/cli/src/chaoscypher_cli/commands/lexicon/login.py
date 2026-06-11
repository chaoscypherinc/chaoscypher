# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Login/Logout commands - Authenticate with Lexicon Hub.

Handles authentication flow using OAuth Device Authorization (RFC 8628).
This allows users to authenticate via browser without entering credentials
in the terminal.

Example:
    chaoscypher lexicon login              # Device flow (opens browser)
    chaoscypher lexicon login --token xxx  # Direct token auth (for CI/automation)
    chaoscypher lexicon logout
    chaoscypher lexicon whoami
"""

from __future__ import annotations

import asyncio
import sys
import webbrowser
from typing import TYPE_CHECKING

import click
from pydantic import SecretStr
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.spinner import Spinner
from rich.text import Text

from chaoscypher_cli.utils.console import get_console, print_error, print_success
from chaoscypher_cli.utils.paths import get_config_dir
from chaoscypher_core.exceptions import ExternalServiceError
from chaoscypher_core.services.lexicon import (
    AuthConfig,
    DeviceCodeResponse,
    FileLexiconStorage,
    LexiconAuthConfig,
    LexiconClient,
    LexiconClientError,
)


if TYPE_CHECKING:
    from pathlib import Path


# Pre-unification leftover file. Lexicon login state moved to auth.json
# (PathSettings.auth_file); a lingering credentials.json is never read — it
# only triggers a one-line re-login notice (D3: no compat shims).
_STALE_CREDENTIALS_FILE = "credentials.json"


def _get_storage() -> FileLexiconStorage:
    """Return the canonical file-backed lexicon credential storage (auth.json)."""
    return FileLexiconStorage(get_config_dir())


def _to_storage_config(auth: AuthConfig) -> LexiconAuthConfig:
    """Adapt the client AuthConfig dataclass to the storage's Pydantic config."""
    return LexiconAuthConfig(
        token=SecretStr(auth.token) if auth.token else None,
        refresh_token=SecretStr(auth.refresh_token) if auth.refresh_token else None,
        expires_at=auth.expires_at,
        username=auth.username,
    )


def get_auth_config() -> AuthConfig | None:
    """Get the low-level client AuthConfig from stored login state.

    Reads ``auth.json`` via the core ``FileLexiconStorage`` and adapts the
    Pydantic ``LexiconAuthConfig`` to the client-side ``AuthConfig`` dataclass
    that ``LexiconClient`` consumes.

    Returns:
        AuthConfig or None if not logged in.
    """
    creds: LexiconAuthConfig | None = _get_storage().load_credentials()
    if creds is not None and creds.token is not None:
        return AuthConfig(
            token=creds.token.get_secret_value(),
            refresh_token=(creds.refresh_token.get_secret_value() if creds.refresh_token else None),
            expires_at=creds.expires_at,
            username=creds.username,
        )
    return None


def get_lexicon_url() -> str:
    """Get the Lexicon URL from stored login state or settings default.

    Priority:
    1. Stored login state (auth.json)
    2. settings.lexicon.url (env LEXICON_URL feeds this)

    Returns:
        Lexicon URL.
    """
    return _get_storage().get_lexicon_url()


def _stale_credentials_path() -> Path:
    """Return the pre-unification credentials.json path in the config dir."""
    return get_config_dir() / _STALE_CREDENTIALS_FILE


def _warn_if_stale_credentials() -> None:
    """Print a one-line re-login notice if a pre-unification credentials.json lingers.

    Fires only when there is no live ``auth.json`` session but the old
    ``credentials.json`` still exists. Stateless: deleting the old file
    silences it.
    """
    if _get_storage().load_credentials() is None and _stale_credentials_path().exists():
        get_console().print(
            f"[yellow]Note:[/yellow] stale {_STALE_CREDENTIALS_FILE} found "
            "(pre-unification); run [cyan]chaoscypher lexicon login[/cyan] again, "
            "then delete the old file.",
            soft_wrap=True,
        )


async def _device_auth_flow(hub: str, no_browser: bool) -> AuthConfig:
    """Execute device authorization flow.

    Args:
        hub: Hub URL.
        no_browser: If True, don't auto-open browser.

    Returns:
        AuthConfig with tokens.

    Raises:
        LexiconClientError: On authentication failure.
    """
    console = get_console()

    async with LexiconClient(base_url=hub) as client:
        # Step 1: Request device code
        device: DeviceCodeResponse = await client.request_device_code()

        # Step 2: Display verification info
        verification_url = device.verification_uri_complete or device.verification_uri

        panel_content = Text()
        panel_content.append("\n")
        panel_content.append("To authenticate, visit:\n\n", style="dim")
        panel_content.append(f"  {verification_url}\n\n", style="bold cyan underline")

        if not device.verification_uri_complete:
            panel_content.append("And enter this code:\n\n", style="dim")
            panel_content.append(f"  {device.user_code}\n\n", style="bold yellow")

        panel_content.append(f"Code expires in {device.expires_in // 60} minutes.\n", style="dim")

        console.print(
            Panel(
                panel_content,
                title="[bold]Browser Authentication[/bold]",
                border_style="cyan",
            )
        )

        # Step 3: Optionally open browser
        if not no_browser:
            try:
                if Confirm.ask("\nOpen browser automatically?", default=True):
                    webbrowser.open(verification_url)
                    console.print("[dim]Browser opened. Complete authentication there.[/dim]\n")
            except Exception:
                console.print("[dim]Could not open browser. Please visit the URL manually.[/dim]\n")

        # Step 4: Poll for token with spinner
        console.print()

        with Live(
            Spinner("dots", text="Waiting for browser authentication..."),
            console=console,
            refresh_per_second=4,
        ):
            return await client.poll_device_token(
                device.device_code,
                timeout=float(device.expires_in),
                interval=float(device.interval),
            )


@click.command()
@click.option(
    "--url",
    "-u",
    default=None,
    help="Lexicon URL (default: read from settings.lexicon.url)",
)
@click.option("--token", "-t", help="API token (skips interactive auth, for CI/automation)")
@click.option("--no-browser", is_flag=True, help="Don't auto-open browser")
def login(url: str | None, token: str | None, no_browser: bool) -> None:
    """Authenticate with the Lexicon Hub.

    Uses OAuth Device Authorization flow by default - opens a browser
    for secure authentication without entering credentials in terminal.

    For CI/automation, use --token to provide an API token directly.

    Example:
        chaoscypher lexicon login                    # Browser auth (recommended)
        chaoscypher lexicon login --no-browser       # Manual URL copy
        chaoscypher lexicon login --token ghp_xxxxx  # Token auth (CI/automation)
    """
    console = get_console()
    console.print("[bold cyan]Lexicon Login[/bold cyan]\n")

    storage = _get_storage()

    # Use provided URL or get from environment/default
    lexicon_url = url or get_lexicon_url()

    # Token auth (for CI/automation)
    if token:
        # Get username if token provided
        username = Prompt.ask("Username (for display)")
        auth = AuthConfig(token=token, username=username)
        storage.save_credentials(lexicon_url, _to_storage_config(auth))
        print_success(f"Logged in as {username}")
        console.print(f"  [dim]Lexicon:[/dim] {lexicon_url}")
        console.print(f"  [dim]Credentials saved to:[/dim] {storage.auth_file}")
        return

    # Device authorization flow (default, recommended)
    try:
        auth = asyncio.run(_device_auth_flow(lexicon_url, no_browser))
        storage.save_credentials(lexicon_url, _to_storage_config(auth))

        print_success(f"Logged in as {auth.username}")
        console.print(f"  [dim]Lexicon:[/dim] {lexicon_url}")
        console.print(f"  [dim]Credentials saved to:[/dim] {storage.auth_file}")

        # Show available commands
        console.print("\n[dim]You can now use:[/dim]")
        console.print("  chaoscypher pull <package>           - Download packages")
        console.print("  chaoscypher push                     - Upload packages")
        console.print("  chaoscypher lexicon search <query>   - Search packages")

    except LexiconClientError as e:
        if e.status_code == 408:
            print_error("Authentication timed out. Please try again.")
        elif e.status_code == 403:
            print_error("Authentication was denied.")
        elif e.status_code == 410:
            print_error("Device code expired. Please try again.")
        else:
            print_error(f"Authentication failed: {e}")
        sys.exit(1)
    except ExternalServiceError as e:
        # LexiconClient wraps httpx.ConnectError into ExternalServiceError when
        # the hub isn't reachable — turn it into a one-line operator hint
        # instead of a raw traceback.
        print_error(f"Cannot reach Lexicon Hub at {lexicon_url}: {e}")
        console.print(
            "  [dim]Set LEXICON_URL or run a local hub. "
            "Check connectivity with [cyan]curl -I "
            f"{lexicon_url}[/cyan].[/dim]",
        )
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Authentication cancelled.[/yellow]")
        sys.exit(1)


@click.command()
def logout() -> None:
    """Clear authentication credentials.

    Removes stored login state (auth.json) from the local system.

    Example:
        chaoscypher lexicon logout
    """
    console = get_console()
    storage = _get_storage()
    creds = storage.load_credentials()

    if creds is not None:
        username = creds.username or "unknown"
        storage.clear_credentials()
        print_success(f"Logged out ({username})")
        console.print(f"  [dim]Credentials removed from:[/dim] {storage.auth_file}")
    else:
        console.print("[dim]Not logged in.[/dim]")
        _warn_if_stale_credentials()


@click.command()
def whoami() -> None:
    """Show current authentication status.

    Example:
        chaoscypher lexicon whoami
    """
    console = get_console()
    storage = _get_storage()
    creds = storage.load_credentials()

    if creds is not None:
        console.print(f"[green]Logged in as:[/green] {creds.username}")
        console.print(f"  [dim]Lexicon:[/dim] {storage.get_lexicon_url()}")
    else:
        console.print("[dim]Not logged in.[/dim]")
        console.print("\nUse 'chaoscypher lexicon login' to authenticate.")
        _warn_if_stale_credentials()
