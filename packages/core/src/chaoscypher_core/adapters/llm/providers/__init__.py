# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM Provider Registry.

Providers follow the unified plugin architecture (:class:`BaseRegistry`)
shared with loaders, tools, cleaners, and archive handlers.

``ProviderRegistry`` stores provider **classes** keyed by
``_METADATA.plugin_id`` — instances are built on demand by
``get_provider(config)`` with a config dict. Built-in providers are
seeded from ``_BUILTIN_PROVIDERS``; third-party providers can register
via the ``chaoscypher.providers`` entry-point group.

Usage:
    from chaoscypher_core.adapters.llm.providers import get_provider

    config = {"chat_provider": "ollama", ...}
    provider = get_provider(config)
    response = await provider.chat(messages)
"""

from __future__ import annotations

import importlib
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, ClassVar

import structlog

from chaoscypher_core.adapters.llm.providers.base import BaseLLMProvider
from chaoscypher_core.plugins import BaseRegistry


if TYPE_CHECKING:
    from chaoscypher_core.plugins.base import PluginMetadata


logger = structlog.get_logger(__name__)

PROVIDERS_ENTRY_POINT_GROUP = "chaoscypher.providers"


class ProviderRegistry(BaseRegistry[type[BaseLLMProvider]]):
    """Registry for LLM provider classes.

    Unlike typical plugin registries (which store instances), this one
    stores classes because providers are parametrized by per-request
    config. Discovery seeds the four built-in providers from
    ``_BUILTIN_PROVIDERS``. The ``chaoscypher.providers`` entry-point
    group wires in third-party providers without editing Core (see
    :meth:`_discover_entry_points`, Phase 7 Task E).
    """

    _BUILTIN_PROVIDERS: ClassVar[dict[str, str]] = {
        "ollama": "chaoscypher_core.adapters.llm.providers.ollama_provider:OllamaProvider",
        "openai": "chaoscypher_core.adapters.llm.providers.openai_provider:OpenAIProvider",
        "anthropic": (
            "chaoscypher_core.adapters.llm.providers.anthropic_provider:AnthropicProvider"
        ),
        "gemini": "chaoscypher_core.adapters.llm.providers.gemini_provider:GeminiProvider",
    }

    @property
    def plugin_entry_point_group(self) -> str | None:
        """Group name scanned by :meth:`_discover_entry_points`."""
        return PROVIDERS_ENTRY_POINT_GROUP

    def _discover(self) -> None:
        """Seed the registry with the built-in provider classes."""
        for name, target in self._BUILTIN_PROVIDERS.items():
            module_path, _, class_name = target.partition(":")
            try:
                module = importlib.import_module(module_path)
                cls = getattr(module, class_name)
            except (ImportError, AttributeError) as exc:
                logger.warning(
                    "builtin_provider_load_failed",
                    name=name,
                    target=target,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                continue
            self._register_by_id(name, cls)

    def _discover_entry_points(self) -> None:
        """Scan the ``chaoscypher.providers`` entry-point group.

        Overrides :meth:`BaseRegistry._discover_entry_points` because the
        base calls ``ep.load()()`` expecting an instance factory, while
        providers are classes that need a config dict at instantiation.
        Failures are logged + skipped so a misbehaving third-party
        provider's ``__init__.py`` can't crash registry discovery.
        """
        group = self.plugin_entry_point_group
        if group is None:
            return

        for ep in entry_points(group=group):
            if ep.name in self._BUILTIN_PROVIDERS and ep.value == self._BUILTIN_PROVIDERS[ep.name]:
                continue  # already registered by _discover()
            try:
                cls = ep.load()
            except Exception as exc:
                logger.warning(
                    "provider_entry_point_load_failed",
                    name=ep.name,
                    value=ep.value,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                continue
            if not (isinstance(cls, type) and issubclass(cls, BaseLLMProvider)):
                logger.warning(
                    "provider_entry_point_invalid_type",
                    name=ep.name,
                    value=ep.value,
                    resolved_type=type(cls).__name__,
                )
                continue
            if getattr(cls, "_METADATA", None) is None:
                logger.warning(
                    "provider_entry_point_missing_metadata",
                    name=ep.name,
                    value=ep.value,
                )
                continue
            self._register_by_id(ep.name, cls, origin="entry_point")
            logger.info(
                "provider_entry_point_registered",
                name=ep.name,
                value=ep.value,
            )

    def _get_plugin_id(self, plugin: type[BaseLLMProvider]) -> str:
        """Look up the plugin id from the class's ``_METADATA`` class var."""
        metadata = getattr(plugin, "_METADATA", None)
        if metadata is None:
            return plugin.__name__
        plugin_id = metadata.plugin_id
        return plugin_id if plugin_id is not None else plugin.__name__

    def _get_plugin_metadata(self, plugin: type[BaseLLMProvider]) -> PluginMetadata:
        """Read metadata from the class's ``_METADATA`` class var."""
        return plugin._METADATA  # noqa: SLF001

    def get_class(self, plugin_id: str) -> type[BaseLLMProvider]:
        """Return the provider class registered under ``plugin_id``.

        Raises:
            ValueError: If no provider is registered under that id.
        """
        cls = self.get(plugin_id)
        if cls is None:
            available = ", ".join(sorted(self._plugins))
            msg = f"Unknown LLM provider: {plugin_id!r}. Available providers: {available}"
            raise ValueError(msg)
        return cls


# Module-level singleton. Lazy-initialized on first access so importing
# this module doesn't trigger every provider's SDK import.
_REGISTRY: ProviderRegistry | None = None


def get_provider_registry() -> ProviderRegistry:
    """Return the module-level :class:`ProviderRegistry` singleton."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ProviderRegistry()
    return _REGISTRY


def get_provider(config: dict[str, Any]) -> BaseLLMProvider:
    """Instantiate the provider named by ``config['chat_provider']``.

    Args:
        config: Configuration dictionary. Must include ``chat_provider``.

    Returns:
        Initialized :class:`BaseLLMProvider` instance.

    Raises:
        ValueError: If the named provider is not registered.
    """
    provider_name = config["chat_provider"].lower()
    provider_class = get_provider_registry().get_class(provider_name)
    return provider_class(config)


def list_available_providers() -> list[str]:
    """List the plugin ids of every currently registered provider.

    Returns:
        Sorted list of registered provider names.
    """
    return sorted(get_provider_registry().list_all())


__all__ = [
    "BaseLLMProvider",
    "ProviderRegistry",
    "get_provider",
    "get_provider_registry",
    "list_available_providers",
]
