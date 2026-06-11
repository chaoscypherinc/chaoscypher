# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
# ruff: noqa: D301  -- Click \x08 paragraph escape, intentional non-raw docstring.

"""Setup Wizard - Configure LLM provider for Chaos Cypher CLI.

Interactive wizard that guides users through LLM configuration:
- Provider selection (Ollama, OpenAI, Anthropic, Gemini)
- VRAM-based quick setup for Ollama
- API key collection for cloud providers
- Connection testing
- Configuration persistence

Example:
    chaoscypher setup                        # Interactive wizard
    chaoscypher setup --provider ollama      # Skip provider selection
    chaoscypher setup --vram 24              # Ollama with VRAM preset
    chaoscypher setup --non-interactive      # CI mode (uses env vars)
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from typing import Any

import click
from pydantic import BaseModel, Field, SecretStr
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from chaoscypher_core.app_config import Settings, get_config_manager, get_settings
from chaoscypher_core.settings import EmbeddingSettings
from chaoscypher_core.settings import LLMSettings as _CoreLLMSettings


console = Console()


# Canonical Ollama model defaults — single source of truth in core's LLMSettings.
# Reading via .model_fields[...].default keeps the CLI in sync when operators bump
# the central defaults; nothing here should re-declare these literals.
_LLM_DEFAULTS = _CoreLLMSettings.model_fields
_DEFAULT_OLLAMA_CHAT_MODEL: str = _LLM_DEFAULTS["ollama_chat_model"].default
_DEFAULT_OLLAMA_EXTRACTION_MODEL: str = (
    _LLM_DEFAULTS["ollama_extraction_model"].default or _DEFAULT_OLLAMA_CHAT_MODEL
)
_DEFAULT_OLLAMA_NUM_CTX: int = _LLM_DEFAULTS["ollama_num_ctx"].default


class WizardLLMState(BaseModel):
    """In-memory holder for the wizard's LLM answers.

    Field names match the prompts (and the pre-unification cli.yaml shape)
    for helper-code simplicity; this object is NEVER persisted —
    ``_wizard_updates`` translates it to core schema names for settings.yaml.
    """

    provider: str = ""
    ollama_url: str = "http://localhost:11434"
    ollama_chat_model: str = _DEFAULT_OLLAMA_CHAT_MODEL
    ollama_extraction_model: str | None = _DEFAULT_OLLAMA_EXTRACTION_MODEL
    ollama_vision_model: str | None = None
    ollama_num_ctx: int = _DEFAULT_OLLAMA_NUM_CTX
    openai_api_key: SecretStr | None = None
    openai_chat_model: str = _LLM_DEFAULTS["openai_chat_model"].default
    openai_extraction_model: str | None = None
    openai_vision_model: str | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_chat_model: str = _LLM_DEFAULTS["anthropic_chat_model"].default
    anthropic_extraction_model: str | None = None
    anthropic_vision_model: str | None = None
    gemini_api_key: SecretStr | None = None
    gemini_chat_model: str = _LLM_DEFAULTS["gemini_chat_model"].default
    gemini_extraction_model: str | None = None
    gemini_vision_model: str | None = None


class WizardState(BaseModel):
    """Everything the setup wizard collects before persisting."""

    llm: WizardLLMState = Field(default_factory=WizardLLMState)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)


def _seed_wizard_state(settings: Settings) -> WizardState:
    """Pre-fill wizard defaults from current settings.yaml values.

    Re-running setup shows the operator their existing configuration as
    prompt defaults instead of factory defaults.
    """
    state = WizardState()
    llm = settings.llm
    s = state.llm
    if llm.chat_provider:
        s.provider = llm.chat_provider
    # The factory-default instance may point somewhere environment-specific
    # (CHAOSCYPHER_OLLAMA_URL — e.g. host.docker.internal inside the Docker
    # images) — only an operator-configured instance list should carry
    # through to the prompt default. Otherwise seed plain localhost.
    if "ollama_instances" in llm.model_fields_set:
        s.ollama_url = llm.primary_ollama_url
    else:
        s.ollama_url = "http://localhost:11434"
    s.ollama_chat_model = llm.ollama_chat_model
    s.ollama_extraction_model = llm.ollama_extraction_model or _DEFAULT_OLLAMA_EXTRACTION_MODEL
    s.ollama_vision_model = llm.ollama_vision_model
    s.ollama_num_ctx = llm.ollama_num_ctx or _DEFAULT_OLLAMA_NUM_CTX
    for provider in ("openai", "anthropic", "gemini"):
        for suffix in ("api_key", "chat_model", "extraction_model", "vision_model"):
            value = getattr(llm, f"{provider}_{suffix}")
            if value is not None:
                setattr(s, f"{provider}_{suffix}", value)
    state.embedding = settings.embedding.model_copy(deep=True)
    return state


def _wizard_updates(state: WizardState) -> dict[str, Any]:
    """Translate wizard state into a core-schema settings.yaml update dict."""
    llm_updates: dict[str, Any] = {"chat_provider": state.llm.provider}

    if state.llm.provider == "ollama":
        llm_updates["ollama_instances"] = [
            {
                "id": "default",
                "name": "Default",
                "base_url": state.llm.ollama_url,
                "enabled": True,
                "healthy": True,
            }
        ]
        llm_updates["ollama_chat_model"] = state.llm.ollama_chat_model
        llm_updates["ollama_extraction_model"] = state.llm.ollama_extraction_model
        llm_updates["ollama_vision_model"] = state.llm.ollama_vision_model
        llm_updates["ollama_num_ctx"] = state.llm.ollama_num_ctx
    else:
        prefix = state.llm.provider
        for suffix in ("api_key", "chat_model", "extraction_model", "vision_model"):
            value = getattr(state.llm, f"{prefix}_{suffix}")
            if value is not None:
                llm_updates[f"{prefix}_{suffix}"] = value

    embedding_updates: dict[str, Any] = {
        "provider": state.embedding.provider,
        "model": state.embedding.model,
        "is_configured": state.embedding.is_configured,
    }
    if state.embedding.api_key is not None:
        embedding_updates["api_key"] = state.embedding.api_key
    if state.embedding.api_base:
        embedding_updates["api_base"] = state.embedding.api_base

    return {
        "llm": llm_updates,
        "embedding": embedding_updates,
        "setup_completed": True,
    }


def _persist_wizard_state(state: WizardState) -> None:
    """Validate + write the wizard answers into data_dir/settings.yaml."""
    get_config_manager().update_settings(_wizard_updates(state))


# Provider information
PROVIDERS = {
    "ollama": {
        "name": "Ollama",
        "description": "Local LLM - Free, private, no API key required",
        "requires_api_key": False,
    },
    "openai": {
        "name": "OpenAI",
        "description": "GPT-4o - Cloud-based, requires API key",
        "requires_api_key": True,
        "env_var": "OPENAI_API_KEY",
    },
    "anthropic": {
        "name": "Anthropic",
        "description": "Claude - Cloud-based, requires API key",
        "requires_api_key": True,
        "env_var": "ANTHROPIC_API_KEY",
    },
    "gemini": {
        "name": "Google Gemini",
        "description": "Gemini Pro - Cloud-based, requires API key",
        "requires_api_key": True,
        "env_var": "GEMINI_API_KEY",
    },
}


# VRAM preset mappings (GPU examples and models must match the preset JSONs
# in core/services/presets/plugins/ — pinned by TestVramPresetTableAccuracy)
VRAM_PRESETS = [
    {"vram": 16, "preset": "vram_16gb", "gpus": "RTX 4080, 5080", "model": "phi4:14b"},
    {"vram": 20, "preset": "vram_20gb", "gpus": "RTX A4000, A4500", "model": "phi4:14b"},
    {"vram": 24, "preset": "vram_24gb", "gpus": "RTX 4090, 3090", "model": "qwen3:30b"},
    {"vram": 32, "preset": "vram_32gb", "gpus": "RTX 5090", "model": "qwen3:30b"},
    {"vram": 48, "preset": "vram_48gb", "gpus": "A6000, 2x 4090", "model": "qwen3:30b"},
    {"vram": 96, "preset": "vram_96gb", "gpus": "RTX 6000 Pro", "model": "gpt-oss:120b"},
    {
        "vram": 128,
        "preset": "vram_128gb",
        "gpus": "DGX Spark, Ryzen AI Max+ 395",
        "model": "gpt-oss:120b",
    },
]


def _test_ollama_connection(url: str) -> tuple[bool, str]:
    """Test Ollama connectivity.

    Args:
        url: Ollama API URL

    Returns:
        Tuple of (success, message)
    """
    try:
        api_url = f"{url.rstrip('/')}/api/tags"
        req = urllib.request.Request(api_url, method="GET")  # noqa: S310
        timeout_seconds = get_settings().cli.setup_ollama_test_timeout_seconds
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            if response.status == 200:
                return True, "Connected successfully"
            return False, f"Unexpected status: {response.status}"
    except urllib.error.URLError as e:
        return False, f"Connection failed: {e.reason}"
    except TimeoutError:
        return False, "Connection timed out"
    except Exception as e:
        return False, f"Error: {e}"


def _test_openai_connection(api_key: str) -> tuple[bool, str]:
    """Test OpenAI API key validity.

    Args:
        api_key: OpenAI API key

    Returns:
        Tuple of (success, message)
    """
    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/models",
            headers={
                "Authorization": f"Bearer {api_key}",
            },
            method="GET",
        )
        timeout_seconds = get_settings().cli.api_test_timeout_seconds
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            if response.status == 200:
                return True, "API key valid"
            return False, f"Unexpected status: {response.status}"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "Invalid API key"
        return False, f"HTTP error: {e.code}"
    except Exception as e:
        return False, f"Error: {e}"


def _test_anthropic_connection(api_key: str) -> tuple[bool, str]:
    """Test Anthropic API key validity.

    Args:
        api_key: Anthropic API key

    Returns:
        Tuple of (success, message)
    """
    try:
        import json

        data = json.dumps(
            {
                "model": "claude-3-haiku-20240307",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "Hi"}],
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=data,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        timeout_seconds = get_settings().cli.api_test_timeout_seconds
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            if response.status == 200:
                return True, "API key valid"
            return False, f"Unexpected status: {response.status}"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "Invalid API key"
        return False, f"HTTP error: {e.code}"
    except Exception as e:
        return False, f"Error: {e}"


def _test_gemini_connection(api_key: str) -> tuple[bool, str]:
    """Test Gemini API key validity.

    Args:
        api_key: Gemini API key

    Returns:
        Tuple of (success, message)
    """
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        req = urllib.request.Request(url, method="GET")  # noqa: S310
        timeout_seconds = get_settings().cli.api_test_timeout_seconds
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            if response.status == 200:
                return True, "API key valid"
            return False, f"Unexpected status: {response.status}"
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            return False, "Invalid API key"
        return False, f"HTTP error: {e.code}"
    except Exception as e:
        return False, f"Error: {e}"


def _get_vram_preset_settings(preset_name: str) -> dict:
    """Load settings from a VRAM preset.

    Preset registry discovery logs noisily, so stdlib logging and structlog
    are silenced for the duration of the load. Both are process-global, so
    the prior state (``logging.disable`` level and structlog configuration)
    is saved up front and restored in a ``finally`` — even a failed load
    must not leave logging disabled for the rest of the process.

    Args:
        preset_name: Name of the preset (e.g., 'vram_24gb')

    Returns:
        Dict with ollama settings
    """
    try:
        # Suppress structlog output during preset loading (registry discovery logs)
        import logging

        import structlog

        previous_disable_level = logging.root.manager.disable
        previous_structlog_config = structlog.get_config()

        logging.disable(logging.CRITICAL)
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
        )

        try:
            from chaoscypher_core.services.presets import get_preset_registry

            registry = get_preset_registry()
            preset = registry.get(preset_name)
        finally:
            # Restore the caller's global logging state exactly as it was.
            logging.disable(previous_disable_level)
            structlog.configure(**previous_structlog_config)

        if preset:
            return preset.get_ollama_settings()
    except (ImportError, AttributeError):  # fmt: skip
        pass

    # Fallback defaults if preset not found — sourced from core's LLMSettings.
    return {
        "ollama_chat_model": _DEFAULT_OLLAMA_CHAT_MODEL,
        "ollama_num_ctx": _DEFAULT_OLLAMA_NUM_CTX,
    }


def _select_provider_interactive() -> str | None:
    """Show interactive provider selection menu.

    Returns:
        Selected provider name or None if cancelled
    """
    console.print("\n[bold cyan]Choose LLM Provider:[/bold cyan]\n")

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("#", style="dim", width=4)
    table.add_column("Provider", style="cyan")
    table.add_column("Description")

    provider_list = list(PROVIDERS.items())
    for i, (_key, info) in enumerate(provider_list, 1):
        table.add_row(f"[{i}]", str(info["name"]), str(info["description"]))

    console.print(table)
    console.print()

    try:
        choice = Prompt.ask(
            "Select provider",
            choices=[str(i) for i in range(1, len(provider_list) + 1)] + ["q"],
            default="1",
        )

        if choice.lower() == "q":
            return None

        idx = int(choice) - 1
        return provider_list[idx][0]

    except (ValueError, IndexError, KeyboardInterrupt):  # fmt: skip
        return None


def _select_vram_interactive() -> tuple[str | None, dict | None]:
    """Show interactive VRAM selection menu for Ollama.

    Returns:
        Tuple of (preset_name, settings) or (None, None) for custom
    """
    console.print("\n[bold cyan]How much GPU VRAM do you have?[/bold cyan]\n")

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("#", style="dim", width=4)
    table.add_column("VRAM", width=8)
    table.add_column("GPUs", style="dim")
    table.add_column("Model", style="green")

    for i, preset in enumerate(VRAM_PRESETS, 1):
        table.add_row(
            f"[{i}]",
            f"{preset['vram']}GB",
            f"({preset['gpus']})",
            f"→ {preset['model']}",
        )

    table.add_row(f"[{len(VRAM_PRESETS) + 1}]", "Custom", "", "I'll specify models manually")

    console.print(table)
    console.print()

    try:
        choice = Prompt.ask(
            "Select VRAM tier",
            choices=[str(i) for i in range(1, len(VRAM_PRESETS) + 2)],
            default="3",  # Default to 24GB (most common enthusiast)
        )

        idx = int(choice) - 1
        if idx >= len(VRAM_PRESETS):
            return None, None  # Custom

        preset = VRAM_PRESETS[idx]
        preset_name = str(preset["preset"])
        settings = _get_vram_preset_settings(preset_name)
        return preset_name, settings

    except (ValueError, IndexError, KeyboardInterrupt):  # fmt: skip
        return None, None


def _configure_ollama_interactive(
    state: WizardState,
    vram: int | None = None,
    test: bool = True,
) -> bool:
    """Configure Ollama interactively.

    Args:
        state: Wizard state to update in place
        vram: VRAM size if pre-specified
        test: Whether to test connection

    Returns:
        True if configured successfully
    """
    # Get Ollama URL
    default_url = state.llm.ollama_url or "http://localhost:11434"
    ollama_url = Prompt.ask("Ollama URL", default=default_url)

    # Test connection first
    if test:
        console.print("\n[dim]Testing connection...[/dim]", end=" ")
        success, message = _test_ollama_connection(ollama_url)
        if success:
            console.print(f"[green]{message}[/green]")
        else:
            console.print(f"[red]{message}[/red]")
            if not Confirm.ask("Continue anyway?", default=False):
                return False

    # VRAM selection
    if vram:
        # Find matching preset
        preset_name = None
        for p in VRAM_PRESETS:
            if p["vram"] == vram:
                preset_name = str(p["preset"])
                break
        if preset_name:
            settings = _get_vram_preset_settings(preset_name)
            console.print(f"\n[green]Applying {vram}GB VRAM preset...[/green]")
        else:
            settings = None
    else:
        preset_name, settings = _select_vram_interactive()

    if settings:
        # Apply preset
        state.llm.ollama_url = ollama_url
        state.llm.ollama_chat_model = settings.get("ollama_chat_model", _DEFAULT_OLLAMA_CHAT_MODEL)
        state.llm.ollama_extraction_model = settings.get(
            "ollama_extraction_model", _DEFAULT_OLLAMA_EXTRACTION_MODEL
        )
        state.llm.ollama_vision_model = settings.get("ollama_vision_model")
        state.llm.ollama_num_ctx = settings.get("ollama_num_ctx", _DEFAULT_OLLAMA_NUM_CTX)

        console.print(f"  Chat model: [cyan]{state.llm.ollama_chat_model}[/cyan]")
        console.print(f"  Extraction model: [cyan]{state.llm.ollama_extraction_model}[/cyan]")
        if state.llm.ollama_vision_model:
            console.print(f"  Vision model: [cyan]{state.llm.ollama_vision_model}[/cyan]")
        console.print(f"  Context window: [cyan]{state.llm.ollama_num_ctx}[/cyan]")
    else:
        # Custom configuration
        state.llm.ollama_url = ollama_url
        state.llm.ollama_chat_model = Prompt.ask(
            "Chat model",
            default=state.llm.ollama_chat_model or _DEFAULT_OLLAMA_CHAT_MODEL,
        )
        state.llm.ollama_extraction_model = Prompt.ask(
            "Extraction model (instruct-tuned)",
            default=state.llm.ollama_extraction_model or _DEFAULT_OLLAMA_EXTRACTION_MODEL,
        )
        state.llm.ollama_num_ctx = IntPrompt.ask(
            "Context window",
            default=state.llm.ollama_num_ctx or _DEFAULT_OLLAMA_NUM_CTX,
        )

    # Vision model selection (optional)
    vision_default = state.llm.ollama_vision_model or "disabled"
    vision_choice = Prompt.ask(
        "Vision model [dim](disabled = no vision)[/dim]", default=vision_default
    )
    state.llm.ollama_vision_model = None if vision_choice == "disabled" else vision_choice

    state.llm.provider = "ollama"
    return True


def _configure_cloud_provider_interactive(
    state: WizardState,
    provider: str,
    test: bool = True,
) -> bool:
    """Configure a cloud provider (OpenAI, Anthropic, Gemini) interactively.

    Args:
        state: Wizard state to update in place
        provider: Provider name
        test: Whether to test API key

    Returns:
        True if configured successfully
    """
    provider_info = PROVIDERS[provider]

    # Check for existing API key from env
    env_var = str(provider_info.get("env_var", ""))
    existing_key = None

    # Unwrap SecretStr for prompts and connection tests; assignments below
    # re-wrap on write so the stored state keeps SecretStr semantics.
    if provider == "openai":
        existing_key = (
            state.llm.openai_api_key.get_secret_value() if state.llm.openai_api_key else None
        )
    elif provider == "anthropic":
        existing_key = (
            state.llm.anthropic_api_key.get_secret_value() if state.llm.anthropic_api_key else None
        )
    elif provider == "gemini":
        existing_key = (
            state.llm.gemini_api_key.get_secret_value() if state.llm.gemini_api_key else None
        )

    # API key input
    console.print(f"\n[bold cyan]{provider_info['name']} Configuration[/bold cyan]\n")

    if existing_key:
        masked = f"{existing_key[:8]}...{existing_key[-4:]}"
        console.print(f"[dim]Found existing API key: {masked}[/dim]")
        if not Confirm.ask("Use existing key?", default=True):
            existing_key = None

    if not existing_key:
        # Offer storage options
        console.print("\nHow would you like to provide your API key?")
        console.print(f"  [1] Environment variable [dim]({env_var})[/dim] - recommended")
        console.print("  [2] Enter here [dim](saved to config file)[/dim]")

        choice = Prompt.ask("Select", choices=["1", "2"], default="1")

        if choice == "1":
            import os

            api_key = os.getenv(env_var)
            if not api_key:
                console.print(f"\n[yellow]Set {env_var} in your shell:[/yellow]")
                console.print(f"  export {env_var}='your-api-key-here'")
                console.print("\nThen run 'chaoscypher setup' again.")
                return False
            existing_key = api_key
        else:
            existing_key = Prompt.ask("API key", password=True)
            if not existing_key:
                console.print("[red]API key is required[/red]")
                return False

    # Test connection
    if test:
        console.print("\n[dim]Validating API key...[/dim]", end=" ")
        if provider == "openai":
            success, message = _test_openai_connection(existing_key)
        elif provider == "anthropic":
            success, message = _test_anthropic_connection(existing_key)
        elif provider == "gemini":
            success, message = _test_gemini_connection(existing_key)
        else:
            success, message = False, "Unknown provider"

        if success:
            console.print(f"[green]{message}[/green]")
        else:
            console.print(f"[red]{message}[/red]")
            if not Confirm.ask("Continue anyway?", default=False):
                return False

    # Save configuration
    state.llm.provider = provider

    if provider == "openai":
        state.llm.openai_api_key = SecretStr(existing_key) if existing_key else None
        state.llm.openai_chat_model = Prompt.ask("Chat model", default=state.llm.openai_chat_model)
        extraction_default = state.llm.openai_extraction_model or state.llm.openai_chat_model
        state.llm.openai_extraction_model = Prompt.ask(
            "Extraction model", default=extraction_default
        )
        vision_default = state.llm.openai_vision_model or "disabled"
        vision_choice = Prompt.ask(
            "Vision model [dim](disabled = no vision)[/dim]", default=vision_default
        )
        state.llm.openai_vision_model = None if vision_choice == "disabled" else vision_choice
    elif provider == "anthropic":
        state.llm.anthropic_api_key = SecretStr(existing_key) if existing_key else None
        state.llm.anthropic_chat_model = Prompt.ask(
            "Chat model", default=state.llm.anthropic_chat_model
        )
        extraction_default = state.llm.anthropic_extraction_model or state.llm.anthropic_chat_model
        state.llm.anthropic_extraction_model = Prompt.ask(
            "Extraction model", default=extraction_default
        )
        vision_default = state.llm.anthropic_vision_model or "disabled"
        vision_choice = Prompt.ask(
            "Vision model [dim](disabled = no vision)[/dim]", default=vision_default
        )
        state.llm.anthropic_vision_model = None if vision_choice == "disabled" else vision_choice
    elif provider == "gemini":
        state.llm.gemini_api_key = SecretStr(existing_key) if existing_key else None
        state.llm.gemini_chat_model = Prompt.ask("Chat model", default=state.llm.gemini_chat_model)
        extraction_default = state.llm.gemini_extraction_model or state.llm.gemini_chat_model
        state.llm.gemini_extraction_model = Prompt.ask(
            "Extraction model", default=extraction_default
        )
        vision_default = state.llm.gemini_vision_model or "disabled"
        vision_choice = Prompt.ask(
            "Vision model [dim](disabled = no vision)[/dim]", default=vision_default
        )
        state.llm.gemini_vision_model = None if vision_choice == "disabled" else vision_choice

    return True


EMBEDDING_PROVIDERS = {
    "local": {
        "name": "Local CPU",
        "description": "Local CPU inference via sentence-transformers (default)",
    },
    "ollama": {
        "name": "Ollama",
        "description": "GPU-accelerated embedding via Ollama server",
    },
    "openai": {
        "name": "OpenAI",
        "description": "Cloud embedding via OpenAI Embeddings API",
    },
    "gemini": {
        "name": "Google Gemini",
        "description": "Cloud embedding via Google Gemini API",
    },
}


def _configure_embedding_interactive(state: WizardState) -> None:
    """Configure embedding provider interactively.

    Args:
        state: Wizard state to update in place.
    """
    from chaoscypher_core.adapters.embedding.registry import (
        CLOUD_EMBEDDING_MODELS,
        CURATED_EMBEDDING_MODELS,
    )

    console.print("\n[bold cyan]Embedding Provider[/bold cyan]\n")

    # Provider selection
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("#", style="dim", width=4)
    table.add_column("Provider", style="cyan")
    table.add_column("Description")

    provider_list = list(EMBEDDING_PROVIDERS.items())
    for i, (_key, info) in enumerate(provider_list, 1):
        recommended = (
            " [dim](recommended)[/dim]"
            if _key == "ollama" and state.llm.provider == "ollama"
            else ""
        )
        table.add_row(f"[{i}]", f"{info['name']}{recommended}", info["description"])

    console.print(table)
    console.print()

    # Default to Ollama when LLM is already configured with Ollama
    default_idx = "2" if state.llm.provider == "ollama" else "1"

    try:
        choice = Prompt.ask(
            "Select embedding provider",
            choices=[str(i) for i in range(1, len(provider_list) + 1)],
            default=default_idx,
        )
        idx = int(choice) - 1
        emb_provider = provider_list[idx][0]
    except (ValueError, IndexError, KeyboardInterrupt):  # fmt: skip
        return

    # Snapshot so a Ctrl+C in a later prompt rolls back instead of
    # persisting a half-configured section (e.g. provider switched to
    # openai while model still names a local one and no API key was set).
    saved_provider = state.embedding.provider
    saved_model = state.embedding.model

    def _rollback() -> None:
        state.embedding.provider = saved_provider
        state.embedding.model = saved_model

    state.embedding.provider = emb_provider

    # Model selection
    if emb_provider in ("local", "ollama"):
        console.print("\n[bold cyan]Select Embedding Model:[/bold cyan]\n")
        model_table = Table(show_header=False, box=None, padding=(0, 2))
        model_table.add_column("#", style="dim", width=4)
        model_table.add_column("Model", style="cyan")
        model_table.add_column("Dimensions", style="green")

        for i, model in enumerate(CURATED_EMBEDDING_MODELS, 1):
            default_tag = " [dim](default)[/dim]" if model.default else ""
            model_table.add_row(f"[{i}]", f"{model.name}{default_tag}", str(model.dimensions))

        custom_idx = len(CURATED_EMBEDDING_MODELS) + 1
        model_table.add_row(f"[{custom_idx}]", "Custom", "Specify model manually")
        console.print(model_table)
        console.print()

        try:
            model_choice = Prompt.ask(
                "Select model",
                choices=[str(i) for i in range(1, custom_idx + 1)],
                default="1",
            )
            model_idx = int(model_choice) - 1
            if model_idx < len(CURATED_EMBEDDING_MODELS):
                selected = CURATED_EMBEDDING_MODELS[model_idx]
                state.embedding.model = (
                    selected.local if emb_provider == "local" else selected.ollama
                )
            else:
                state.embedding.model = Prompt.ask("Model name", default=state.embedding.model)
        except (ValueError, IndexError, KeyboardInterrupt):  # fmt: skip
            _rollback()
            return

    elif emb_provider in ("openai", "gemini"):
        cloud_models = CLOUD_EMBEDDING_MODELS.get(emb_provider, [])
        if cloud_models:
            console.print("\n[bold cyan]Select Embedding Model:[/bold cyan]\n")
            model_table = Table(show_header=False, box=None, padding=(0, 2))
            model_table.add_column("#", style="dim", width=4)
            model_table.add_column("Model", style="cyan")
            model_table.add_column("Dimensions", style="green")

            for i, cloud_model in enumerate(cloud_models, 1):
                current_tag = " [dim](recommended)[/dim]" if cloud_model.current else ""
                model_table.add_row(
                    f"[{i}]", f"{cloud_model.name}{current_tag}", str(cloud_model.dimensions)
                )

            custom_idx = len(cloud_models) + 1
            model_table.add_row(f"[{custom_idx}]", "Custom", "Specify model manually")
            console.print(model_table)
            console.print()

            try:
                model_choice = Prompt.ask(
                    "Select model",
                    choices=[str(i) for i in range(1, custom_idx + 1)],
                    default="1",
                )
                model_idx = int(model_choice) - 1
                if model_idx < len(cloud_models):
                    state.embedding.model = cloud_models[model_idx].model
                else:
                    state.embedding.model = Prompt.ask("Model name", default=state.embedding.model)
            except (ValueError, IndexError, KeyboardInterrupt):  # fmt: skip
                _rollback()
                return

        # API key for cloud providers
        if not state.embedding.api_key:
            # Check if the LLM provider matches and has a key we can reuse
            llm_key: SecretStr | None = None
            if emb_provider == "openai":
                llm_key = state.llm.openai_api_key
            elif emb_provider == "gemini":
                llm_key = state.llm.gemini_api_key

            if llm_key:
                reuse = Confirm.ask(f"Reuse {emb_provider} API key from LLM config?", default=True)
                if reuse:
                    state.embedding.api_key = llm_key

            if not state.embedding.api_key:
                entered = Prompt.ask("Embedding API key", password=True)
                state.embedding.api_key = SecretStr(entered) if entered else None

    state.embedding.is_configured = True
    console.print(f"\n  Embedding provider: [cyan]{state.embedding.provider}[/cyan]")
    console.print(f"  Embedding model: [cyan]{state.embedding.model}[/cyan]")


def _show_summary(state: WizardState) -> None:
    """Show configuration summary."""
    console.print("\n")

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim")
    table.add_column("Value")

    table.add_row("Provider", f"[cyan]{state.llm.provider}[/cyan]")

    if state.llm.provider == "ollama":
        table.add_row("URL", state.llm.ollama_url)
        table.add_row("Chat Model", state.llm.ollama_chat_model)
        table.add_row("Extraction Model", state.llm.ollama_extraction_model)
        table.add_row("Context Window", str(state.llm.ollama_num_ctx))
    elif state.llm.provider == "openai":
        table.add_row("Chat Model", state.llm.openai_chat_model)
        table.add_row(
            "Extraction Model", state.llm.openai_extraction_model or "[dim]same as chat[/dim]"
        )
        key = state.llm.openai_api_key.get_secret_value() if state.llm.openai_api_key else ""
        table.add_row("API Key", f"{key[:8]}...{key[-4:]}" if len(key) > 12 else "[dim]set[/dim]")
    elif state.llm.provider == "anthropic":
        table.add_row("Chat Model", state.llm.anthropic_chat_model)
        table.add_row(
            "Extraction Model", state.llm.anthropic_extraction_model or "[dim]same as chat[/dim]"
        )
        key = state.llm.anthropic_api_key.get_secret_value() if state.llm.anthropic_api_key else ""
        table.add_row("API Key", f"{key[:8]}...{key[-4:]}" if len(key) > 12 else "[dim]set[/dim]")
    elif state.llm.provider == "gemini":
        table.add_row("Chat Model", state.llm.gemini_chat_model)
        table.add_row(
            "Extraction Model", state.llm.gemini_extraction_model or "[dim]same as chat[/dim]"
        )
        key = state.llm.gemini_api_key.get_secret_value() if state.llm.gemini_api_key else ""
        table.add_row("API Key", f"{key[:8]}...{key[-4:]}" if len(key) > 12 else "[dim]set[/dim]")

    # Embedding provider info
    table.add_row("", "")  # Separator
    table.add_row("Embedding Provider", f"[cyan]{state.embedding.provider}[/cyan]")
    table.add_row("Embedding Model", state.embedding.model)

    table.add_row("Config File", str(get_config_manager().settings_path))

    console.print(
        Panel(
            table,
            title="[green]Configuration Complete[/green]",
            border_style="green",
        )
    )

    console.print("\n[cyan]Next steps:[/cyan]")
    console.print("  chaoscypher source add document.pdf  # Process a document")
    console.print("  chaoscypher chat                     # Start interactive chat")


@click.command()
@click.option(
    "--provider",
    "-p",
    type=click.Choice(["ollama", "openai", "anthropic", "gemini"]),
    help="LLM provider (skip selection prompt)",
)
@click.option(
    "--vram",
    type=int,
    help="VRAM size in GB for Ollama (e.g., 16, 24, 32)",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    help="Non-interactive mode (for CI/scripts)",
)
@click.option(
    "--test/--no-test",
    default=True,
    help="Test provider connectivity",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Reconfigure even if already configured",
)
def setup(
    provider: str | None,
    vram: int | None,
    non_interactive: bool,
    test: bool,
    force: bool,
) -> None:
    """Configure LLM provider for Chaos Cypher.

    Interactive wizard that guides through LLM configuration for
    entity extraction and AI-powered features.

    \x08
    Examples:
        chaoscypher setup                              # Interactive wizard
        chaoscypher setup --provider ollama            # Skip provider selection
        chaoscypher setup --provider ollama --vram 24  # Ollama with 24GB preset
        chaoscypher setup --non-interactive            # CI mode (uses env vars)
        chaoscypher setup --force                      # Reconfigure

    \x08
    Supported Providers:
        ollama     - Local LLM, free, private, no API key
        openai     - GPT-4o, requires OPENAI_API_KEY
        anthropic  - Claude, requires ANTHROPIC_API_KEY
        gemini     - Gemini Pro, requires GEMINI_API_KEY
    """
    try:
        from chaoscypher_core.app_config import reload_settings

        backend = reload_settings()  # fresh read; mirrors old get_config(reload=True)

        # Check if already configured
        if backend.setup_completed and backend.llm.chat_provider and not force:
            console.print(f"[dim]LLM already configured: {backend.llm.chat_provider}[/dim]")
            if non_interactive or not Confirm.ask("Reconfigure?", default=False):
                console.print("[dim]Use --force to reconfigure.[/dim]")
                return

        state = _seed_wizard_state(backend)

        # Show header
        if not non_interactive:
            console.print(
                Panel(
                    "[bold]Configure LLM for entity extraction[/bold]",
                    title="ChaosCypher Setup Wizard",
                    border_style="cyan",
                )
            )

        # Non-interactive mode
        if non_interactive:
            if not provider:
                # Try to detect from environment
                import os

                if os.getenv("OPENAI_API_KEY"):
                    provider = "openai"
                elif os.getenv("ANTHROPIC_API_KEY"):
                    provider = "anthropic"
                elif os.getenv("GEMINI_API_KEY"):
                    provider = "gemini"
                else:
                    provider = "ollama"

            console.print(f"[dim]Configuring {provider} (non-interactive)...[/dim]")

            if provider == "ollama":
                state.llm.provider = "ollama"
                if vram:
                    settings = None
                    for p in VRAM_PRESETS:
                        if p["vram"] == vram:
                            settings = _get_vram_preset_settings(str(p["preset"]))
                            break
                    if settings:
                        state.llm.ollama_chat_model = settings.get(
                            "ollama_chat_model", state.llm.ollama_chat_model
                        )
                        state.llm.ollama_extraction_model = settings.get(
                            "ollama_extraction_model", state.llm.ollama_extraction_model
                        )
                        state.llm.ollama_num_ctx = settings.get(
                            "ollama_num_ctx", state.llm.ollama_num_ctx
                        )
            else:
                import os

                state.llm.provider = provider
                # Seed the cloud key from env so it persists to settings.yaml.
                env_key = os.getenv(f"{provider.upper()}_API_KEY")
                if env_key:
                    setattr(state.llm, f"{provider}_api_key", SecretStr(env_key))

            _persist_wizard_state(state)
            console.print(f"[green]Configured {provider}[/green]")
            return

        # Interactive mode - provider selection
        if not provider:
            provider = _select_provider_interactive()
            if not provider:
                console.print("[dim]Cancelled.[/dim]")
                return

        # Configure selected provider
        if provider == "ollama":
            success = _configure_ollama_interactive(state, vram=vram, test=test)
        else:
            success = _configure_cloud_provider_interactive(state, provider, test=test)

        if not success:
            console.print("[red]Configuration cancelled.[/red]")
            sys.exit(1)

        # Embedding provider configuration
        if Confirm.ask("\nConfigure embedding provider?", default=False):
            _configure_embedding_interactive(state)
        elif not state.embedding.is_configured and state.llm.provider == "ollama":
            # Auto-configure Ollama embeddings when LLM is Ollama
            from chaoscypher_core.adapters.embedding.registry import (
                CURATED_EMBEDDING_MODELS,
            )

            default_model = next(
                (m for m in CURATED_EMBEDDING_MODELS if m.default), CURATED_EMBEDDING_MODELS[0]
            )
            state.embedding.provider = "ollama"
            state.embedding.model = default_model.ollama
            state.embedding.is_configured = True
            console.print(
                f"\n  Embedding auto-configured: "
                f"[cyan]ollama[/cyan] / [cyan]{default_model.ollama}[/cyan]"
            )

        # Save configuration
        _persist_wizard_state(state)

        # Show summary
        _show_summary(state)

    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled[/yellow]")
        sys.exit(130)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
