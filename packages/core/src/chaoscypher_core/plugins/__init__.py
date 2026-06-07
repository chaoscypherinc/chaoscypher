# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Plugin Infrastructure for Chaos Cypher.

This module provides a unified plugin architecture for all Chaos Cypher
extensibility points. It defines common patterns for plugin discovery,
registration, and management that are shared across all plugin types.

Plugin Types:
    - Loaders: Document format handlers (PDF, CSV, HTML, etc.)
    - Domains: Extraction domain analyzers (technical, generic, etc.)
    - Tools: Workflow step tools (ai.prompt, data.extract, etc.)
    - Handlers: Archive format handlers (Sphinx, Markdown, OpenAPI)
    - Providers: LLM providers (Ollama, OpenAI, Anthropic, Gemini)
    - Cleaners: Content cleaning processors

Architecture:
    Each plugin type follows a consistent pattern:
    - Protocol: Defines the plugin interface (what methods/properties are required)
    - Registry: Discovers and manages plugin instances
    - Factory: Creates cached registry instances

    Two discovery patterns are supported:
    1. Python Plugins: Auto-discovered via file naming (*_plugin.py, *_loader.py)
    2. Config Plugins: JSON-LD configuration files (domain.jsonld, plugin.jsonld)

Quick Start:
    # Create a custom loader plugin
    from chaoscypher_core.plugins import PluginMetadata

    class MyLoader:
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(
                plugin_id="my_format",
                name="My Format Loader",
                description="Loads .xyz files",
                category="loader",
            )

        @property
        def supported_extensions(self) -> list[str]:
            return [".xyz"]

        def load_document(self, filepath: str) -> list[dict]:
            # Implementation here
            ...

Usage:
    from chaoscypher_core.plugins import (
        BasePlugin,
        BaseRegistry,
        PluginMetadata,
        create_registry_factory,
        discover_python_plugins,
        discover_config_plugins,
    )

    # Create a custom registry
    class MyRegistry(BaseRegistry[MyPlugin]):
        def _discover(self) -> None:
            plugins = discover_python_plugins(
                directory=self.plugins_dir,
                pattern="*_plugin.py",
                required_attrs=["plugin_id", "execute"],
            )
            for plugin_id, plugin in plugins.items():
                self._register_by_id(plugin_id, plugin)

        def _get_plugin_id(self, plugin: MyPlugin) -> str:
            return plugin.plugin_id

        def _get_plugin_metadata(self, plugin: MyPlugin) -> PluginMetadata:
            return plugin.metadata

    # Create cached factory
    get_my_registry = create_registry_factory(MyRegistry)

See Also:
    - chaoscypher_core.services.sources.loaders: Document loaders
    - chaoscypher_core.services.sources.engine.extraction.domains: Domains
    - chaoscypher_core.services.workflows.tools: Workflow tools
    - chaoscypher_core.adapters.llm.providers: LLM providers
"""

# Base classes and protocols
from chaoscypher_core.plugins.base import (
    BasePlugin,
    PluginMetadata,
    metadata_from_dict,
)

# Discovery utilities
from chaoscypher_core.plugins.discovery import (
    discover_config_plugins,
    discover_python_plugins,
)

# Factory utilities
from chaoscypher_core.plugins.factory import (
    create_registry_factory,
    default_cache_key,
)

# Registry base class
from chaoscypher_core.plugins.registry import BaseRegistry, DuplicatePluginError

# User-plugin audit / kill-switch helper
from chaoscypher_core.plugins.user_plugin_loader import (
    audit_log_user_plugin_file,
    load_user_python_plugin,
    user_plugins_allowed,
)


__all__ = [
    "BasePlugin",
    "BaseRegistry",
    "DuplicatePluginError",
    "PluginMetadata",
    "audit_log_user_plugin_file",
    "create_registry_factory",
    "default_cache_key",
    "discover_config_plugins",
    "discover_python_plugins",
    "load_user_python_plugin",
    "metadata_from_dict",
    "user_plugins_allowed",
]
