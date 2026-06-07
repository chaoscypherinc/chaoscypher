# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool Plugin Registry with Auto-Discovery.

Automatically discovers and registers tool plugins from the plugins directory.
This registry extends the shared plugin infrastructure while maintaining
backward compatibility with the existing ToolRegistry pattern.

Architecture:
    1. Scan plugins/ directory for *_plugin.py files
    2. Import each module and inspect for plugin classes
    3. Instantiate and register by tool_id
    4. Provide O(1) lookup by tool_id

Example Usage:
    ```python
    from chaoscypher_core.services.workflows.tools.engine import ToolRegistry

    # Create registry (auto-discovers plugins)
    registry = ToolRegistry()

    # Get plugin
    plugin = registry.get("ai.prompt")
    if plugin:
        result = await plugin.execute(inputs, context)

    # List all plugins
    for tool_id, plugin in registry.list_all().items():
        print(f"{tool_id}: {plugin.description}")

    # List by category
    ai_tools = registry.list_by_category("ai")
    ```

Auto-Discovery Rules:
    - Scans *_plugin.py files in plugins/ directory
    - Class must have: tool_id, category, name, description, input_schema, execute()
    - Plugin class name can be anything (e.g., PromptPlugin, ExtractPlugin)
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.plugins import BaseRegistry, PluginMetadata


if TYPE_CHECKING:
    from types import ModuleType

    from chaoscypher_core.services.workflows.tools.engine.base import BaseToolPlugin
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class ToolRegistry(BaseRegistry["BaseToolPlugin"]):
    """Registry for tool plugins with auto-discovery.

    Extends BaseRegistry to provide standardized plugin management while
    maintaining backward compatibility with the existing ToolRegistry pattern.

    Attributes:
        plugins_dir: Path to plugins directory.

    Example:
        registry = ToolRegistry()
        plugin = registry.get("ai.prompt")
        result = await plugin.execute(inputs, context)
    """

    def __init__(
        self,
        settings: EngineSettings | None = None,
        database_name: str = "default",
        plugins_dir: Path | None = None,
    ) -> None:
        """Initialize tool registry.

        Args:
            settings: Application settings (optional).
            database_name: Database name (unused for tools, kept for interface).
            plugins_dir: Path to built-in plugins directory (defaults to ../plugins).
        """
        # Default to ../plugins directory (engine/ -> plugins/)
        self.plugins_dir = plugins_dir or Path(__file__).parent.parent / "plugins"

        # Call parent init (triggers _discover)
        super().__init__(settings=settings, database_name=database_name)

    def _get_user_plugins_path(self) -> Path | None:
        """Get user plugins path from settings.

        Returns:
            Path to data/plugins/tools/ directory, or None.
        """
        if self.settings is None:
            return None

        # Try to get data_dir from settings
        data_dir = getattr(self.settings, "data_dir", None)
        if data_dir is None:
            # Try paths.data_dir pattern
            paths = getattr(self.settings, "paths", None)
            if paths:
                data_dir = getattr(paths, "data_dir", None)

        if data_dir is None:
            return None

        return Path(data_dir) / "plugins" / "tools"

    def _discover(self) -> None:
        """Auto-discover and register all plugins from built-in and user directories.

        Scans for *_plugin.py files in both the built-in plugins directory
        and the user plugins directory (data/plugins/tools/).

        Implements BaseRegistry._discover().
        """
        # Build search paths: built-in first, then user plugins (user overrides built-in)
        search_paths: list[tuple[str, Path]] = [
            ("builtin", self.plugins_dir),
        ]

        # Add user plugins path if settings available
        user_plugins_path = self._get_user_plugins_path()
        if user_plugins_path and user_plugins_path.exists():
            search_paths.append(("user", user_plugins_path))

        for path_type, plugins_dir in search_paths:
            plugin_files = sorted(plugins_dir.glob("*_plugin.py"))

            logger.info(
                "tool_discovery_started",
                plugins_dir=str(plugins_dir),
                path_type=path_type,
                file_count=len(plugin_files),
            )

            for plugin_file in plugin_files:
                try:
                    self._load_plugin_from_file(plugin_file, path_type)
                except Exception as e:
                    logger.warning(
                        "plugin_load_failed",
                        file=plugin_file.name,
                        path_type=path_type,
                        error_type=type(e).__name__,
                        error_message=str(e),
                    )

        logger.info(
            "tool_discovery_complete",
            plugin_count=len(self._plugins),
            tool_ids=list(self._plugins.keys()),
        )

    def _load_plugin_from_file(self, plugin_file: Path, path_type: str) -> None:
        """Load plugin class from file and register it.

        Args:
            plugin_file: Path to plugin .py file.
            path_type: "builtin" or "user".
        """
        module: ModuleType | None
        if path_type == "builtin":
            # Import using standard module path
            module_name = f"chaoscypher_core.services.workflows.tools.plugins.{plugin_file.stem}"
            logger.debug("loading_plugin_module", file=plugin_file.name, module=module_name)
            module = importlib.import_module(module_name)
        else:
            from chaoscypher_core.plugins.user_plugin_loader import (
                load_user_python_plugin,
            )

            module_name = f"user_tool_{plugin_file.stem}"
            logger.debug(
                "loading_user_plugin_module",
                file=plugin_file.name,
                module=module_name,
            )
            module = load_user_python_plugin(
                plugin_file,
                module_name=module_name,
                registry="ToolRegistry",
            )
            if module is None:
                return

        # Find plugin class in module
        plugin_class = None
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            # Skip imported classes (check if defined in this module)
            if obj.__module__ != module_name:
                continue

            # Check if class has required plugin properties
            if self._is_plugin_class(obj):
                plugin_class = obj
                break

        if not plugin_class:
            logger.debug("no_plugin_class_found", file=plugin_file.name, module=module_name)
            return

        # Instantiate plugin
        plugin_instance = plugin_class()

        # Register by tool_id
        tool_id = plugin_instance.tool_id
        self._register_by_id(
            tool_id,
            plugin_instance,
            source_path=plugin_file,
            is_user=(path_type == "user"),
        )

        logger.info(
            "tool_registered",
            tool_id=tool_id,
            category=plugin_instance.category,
            name=plugin_instance.name,
            file=plugin_file.name,
            path_type=path_type,
        )

    def _is_plugin_class(self, obj: Any) -> bool:
        """Check if class implements BaseToolPlugin interface.

        Uses duck typing - checks for required properties and methods.

        Args:
            obj: Class object to check.

        Returns:
            True if class has required plugin interface.
        """
        required_attrs = ["tool_id", "category", "name", "description", "input_schema", "execute"]
        return all(hasattr(obj, attr) for attr in required_attrs)

    def _get_plugin_id(self, plugin: BaseToolPlugin) -> str:
        """Extract plugin ID from a tool instance.

        Args:
            plugin: Tool plugin instance.

        Returns:
            Tool ID (e.g., "ai.prompt").
        """
        return plugin.tool_id

    def _get_plugin_metadata(self, plugin: BaseToolPlugin) -> PluginMetadata:
        """Extract metadata from a tool instance.

        Falls back to generating metadata from tool properties if plugin
        doesn't implement the metadata property.

        Args:
            plugin: Tool plugin instance.

        Returns:
            PluginMetadata for the tool.
        """
        # Try to get metadata from plugin
        if hasattr(plugin, "metadata"):
            try:
                return plugin.metadata
            except (AttributeError, NotImplementedError):  # fmt: skip
                pass

        # Generate metadata from tool properties
        return PluginMetadata(
            plugin_id=plugin.tool_id,
            name=plugin.name,
            description=plugin.description,
            category=plugin.category,
        )

    def list_by_category(self, category: str) -> dict[str, Any]:
        """Get all plugins in a specific category.

        Args:
            category: Category name (e.g., "ai", "data", "logic").

        Returns:
            Dictionary of plugins in category.

        Example:
            ai_tools = registry.list_by_category("ai")
            for tool_id, plugin in ai_tools.items():
                print(f"{tool_id}: {plugin.name}")
        """
        return {
            tool_id: plugin
            for tool_id, plugin in self._plugins.items()
            if plugin.category == category
        }


__all__ = ["ToolRegistry"]
