# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM Configuration Check Utility.

Provides functions to verify LLM is configured before operations
that require it (like entity extraction).

Example:
    from chaoscypher_cli.utils.llm_check import check_llm_or_skip

    proceed, skip_llm = check_llm_or_skip("entity extraction")
    if not proceed:
        return  # User cancelled
"""

from __future__ import annotations

from rich.console import Console


console = Console()


def is_llm_configured() -> bool:
    """Check if an LLM provider is configured (settings.yaml / env).

    Engine config lives in data_dir/settings.yaml as of the 2026-06 config
    unification; cli.yaml no longer carries LLM settings.

    Returns:
        True if LLM is configured and ready to use
    """
    try:
        from chaoscypher_cli.engine_config import is_setup_completed
        from chaoscypher_core.app_config import get_settings

        if not is_setup_completed():
            return False

        settings = get_settings()
        provider = settings.llm.chat_provider
        if not provider:
            return False

        # Map providers to their API key check (Ollama doesn't need one)
        api_key_checks = {
            "openai": bool(settings.llm.openai_api_key),
            "anthropic": bool(settings.llm.anthropic_api_key),
            "gemini": bool(settings.llm.gemini_api_key),
            "ollama": True,
        }
        return api_key_checks.get(provider, False)

    except Exception:
        return False


def check_llm_or_skip(operation: str = "entity extraction") -> tuple[bool, bool]:
    """Check LLM config and determine if operation should proceed or skip.

    Returns a tuple indicating:
    - Whether to proceed with the operation
    - Whether to skip the LLM-dependent part

    Args:
        operation: Description of the operation requiring LLM

    Returns:
        Tuple of (proceed, skip_llm)
        - (True, False): LLM configured, proceed normally
        - (True, True): Continue without LLM (user chose to skip)
        - (False, False): User cancelled
    """
    if is_llm_configured():
        return True, False

    # Show warning
    console.print("\n[yellow]Warning:[/yellow] LLM not configured.")
    console.print(f"[dim]{operation.capitalize()} requires an LLM provider.[/dim]\n")

    # Offer options
    console.print("What would you like to do?")
    console.print("  [1] Run setup wizard (recommended)")
    console.print(f"  [2] Continue without {operation}")
    console.print("  [3] Cancel")

    from rich.prompt import Prompt

    choice = Prompt.ask("Select", choices=["1", "2", "3"], default="1")

    if choice == "1":
        # Run setup
        try:
            from chaoscypher_cli.commands.setup import setup as setup_cmd

            ctx = setup_cmd.make_context("setup", [])
            setup_cmd.invoke(ctx)

            from chaoscypher_cli.context import refresh_llm_state
            from chaoscypher_core.app_config import reload_settings

            reload_settings()  # pick up what the wizard just wrote
            # The live CLIContext (and its cached has_llm probe) predates
            # the wizard — refresh it, or the very operation that triggered
            # the wizard still sees "no LLM configured".
            refresh_llm_state()
            if is_llm_configured():
                return True, False
            # Setup completed but still not configured
            return True, True

        except Exception as e:
            console.print(f"[red]Setup failed:[/red] {e}")
            return False, False

    if choice == "2":
        return True, True

    return False, False


__all__ = [
    "check_llm_or_skip",
    "is_llm_configured",
]
