# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Base Plugin Protocol and Metadata.

Defines the common interface and metadata model that all Chaos Cypher plugins share.
This provides a consistent foundation for loaders, domains, tools, handlers,
providers, and any future plugin types.

Plugin Types:
    - Loaders: Document format handlers (PDF, CSV, etc.)
    - Domains: Extraction domain analyzers (technical, generic, etc.)
    - Tools: Workflow step tools (ai.prompt, data.extract, etc.)
    - Handlers: Archive format handlers (Sphinx, Markdown, etc.)
    - Providers: LLM providers (Ollama, OpenAI, etc.)
    - Cleaners: Content cleaning processors

Example:
    from chaoscypher_core.plugins import BasePlugin, PluginMetadata

    class MyPlugin:
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(
                plugin_id="my.plugin",
                name="My Plugin",
                description="Does something useful",
                version="1.0.0",
                author="Chaos Cypher, Inc.",
                category="utility",
            )

Security:
    Python plugins discovered from ``{data_dir}/plugins/`` execute with
    the server process's full privileges. See
    ``TRUST_BOUNDARY.md`` (in this package) for the threat model and
    the ``CHAOSCYPHER_ALLOW_USER_PLUGINS`` kill switch.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PluginMetadata(BaseModel):
    """Standard metadata that all plugins provide.

    Unified descriptor for every plugin type: loaders, tools, cleaners,
    archive handlers, LLM providers. Single source of truth for
    plugin-manager UIs, CLI inspection commands, and registry logging.

    Attributes:
        plugin_id: Unique identifier for the plugin (e.g., "pdf", "ai.prompt").
            Defaults to ``name`` if not supplied.
        name: Human-readable display name.
        description: Brief description of what the plugin does (default: empty).
        version: Semantic version string (default: "1.0.0").
        author: Plugin author or team (default: empty).
        category: Plugin category for grouping (default: empty).
        builtin: Whether this is a built-in plugin (default: True).
        tags: Optional tags for filtering/search.
        priority: Higher values run earlier in pipeline-style plugin
            categories (cleaners, archive handlers). Ignored for
            categories that select a single plugin.
        applies_to: Optional predicate deciding whether this plugin
            should run for a given source. None means "always applies".
        origin: Where the plugin was discovered — ``"builtin"`` (in-tree
            default), ``"entry_point"`` (installed via a third-party
            package's ``chaoscypher.*`` entry-point group), or ``"user"``
            (loaded from ``data/plugins/<kind>/``). The registry updates
            this on registration; plugin authors can leave it at default.
    """

    plugin_id: str | None = Field(
        default=None,
        description="Unique plugin identifier; defaults to name if omitted.",
    )
    name: str = Field(
        ...,
        description="Human-readable display name",
    )
    description: str = Field(
        default="",
        description="Brief description of what the plugin does",
    )
    version: str = Field(
        default="1.0.0",
        description="Semantic version string",
    )
    author: str = Field(
        default="",
        description="Plugin author or team",
    )
    category: str = Field(
        default="",
        description="Plugin category for grouping",
    )
    builtin: bool = Field(
        default=True,
        description="Whether this is a built-in plugin",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Optional tags for filtering/search",
    )
    priority: int = Field(
        default=0,
        description="Higher values run earlier in pipeline plugins.",
    )
    applies_to: Callable[[Any], bool] | None = Field(
        default=None,
        description="Optional predicate deciding applicability.",
    )
    origin: Literal["builtin", "entry_point", "user"] = Field(
        default="builtin",
        description="Discovery layer the registry registered this plugin from.",
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @model_validator(mode="after")
    def _default_plugin_id_from_name(self) -> PluginMetadata:
        """Default ``plugin_id`` to ``name`` when omitted."""
        if self.plugin_id is None:
            object.__setattr__(self, "plugin_id", self.name)
        return self


@runtime_checkable
class BasePlugin(Protocol):
    """Base protocol that all plugins implement.

    This protocol defines the minimal interface that every plugin must
    provide. Specific plugin types (loaders, tools, etc.) extend this
    with additional type-specific methods.

    The metadata property provides consistent access to plugin information
    across all plugin types, enabling unified discovery and management.

    Example:
        class MyLoader:
            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    plugin_id="my_format",
                    name="My Format Loader",
                    description="Loads .xyz files",
                )

            # Type-specific methods...
            @property
            def supported_extensions(self) -> list[str]:
                return [".xyz"]

            def load_document(self, filepath: str) -> list[dict]:
                ...
    """

    @property
    def metadata(self) -> PluginMetadata:
        """Get plugin metadata.

        Returns:
            PluginMetadata instance with plugin information.
        """
        ...


def metadata_from_dict(data: dict[str, Any]) -> PluginMetadata:
    """Create PluginMetadata from a dictionary (e.g., JSON-LD config).

    Useful for configuration-based plugins like domains that define
    metadata in JSON-LD files.

    Args:
        data: Dictionary with metadata fields.

    Returns:
        PluginMetadata instance.

    Example:
        config = {"name": "technical", "description": "...", "version": "1.0.0"}
        metadata = metadata_from_dict(config)
    """
    return PluginMetadata(
        plugin_id=data.get("name", data.get("plugin_id", "unknown")),
        name=data.get("name", "Unknown"),
        description=data.get("description", ""),
        version=data.get("version", "1.0.0"),
        author=data.get("author", ""),
        category=data.get("category", ""),
        builtin=data.get("builtin", True),
        tags=data.get("tags", []),
    )


__all__ = [
    "BasePlugin",
    "PluginMetadata",
    "metadata_from_dict",
]
