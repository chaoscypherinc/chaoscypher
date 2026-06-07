# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Plugin Discovery Utilities.

Provides reusable functions for auto-discovering plugins from the filesystem.
Supports both Python-based plugins (via importlib) and configuration-based
plugins (via JSON/JSON-LD files).

Two Discovery Patterns:
    1. Python Plugins: Scan for *_plugin.py, *_loader.py, etc.
    2. Config Plugins: Scan for plugin.jsonld, domain.jsonld, etc.

Example:
    from chaoscypher_core.plugins.discovery import (
        discover_python_plugins,
        discover_config_plugins,
    )

    # Discover Python plugins
    plugins = discover_python_plugins(
        directory=Path("plugins/"),
        pattern="*_plugin.py",
        required_attrs=["tool_id", "name", "execute"],
    )

    # Discover config plugins
    configs = discover_config_plugins(
        directory=Path("domains/"),
        config_filename="domain.jsonld",
    )
"""

from __future__ import annotations

import importlib
import inspect
import json
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


def discover_python_plugins(
    directory: Path,
    pattern: str = "*_plugin.py",
    required_attrs: list[str] | None = None,
    module_prefix: str | None = None,
    exclude_files: list[str] | None = None,
    settings: EngineSettings | None = None,
) -> dict[str, Any]:
    """Discover Python plugin classes from a directory.

    Scans a directory for Python files matching a pattern, imports them,
    and finds classes that have the required attributes (duck typing).

    Args:
        directory: Path to scan for plugin files.
        pattern: Glob pattern for plugin files (default: "*_plugin.py").
        required_attrs: List of attributes/methods the class must have.
        module_prefix: Module path prefix for imports (e.g., "mypackage.plugins").
        exclude_files: Files to skip (default: common infrastructure files).
        settings: Optional settings to pass to plugin constructors.

    Returns:
        Dictionary mapping plugin identifier to plugin instance.
        The identifier is extracted from the first required_attr if possible,
        otherwise uses the class name.

    Example:
        plugins = discover_python_plugins(
            directory=Path("plugins/"),
            pattern="*_plugin.py",
            required_attrs=["tool_id", "name", "execute"],
            module_prefix="mypackage.plugins",
        )
        # plugins = {"ai.prompt": PromptPlugin(), "data.extract": ExtractPlugin()}
    """
    if required_attrs is None:
        required_attrs = []

    if exclude_files is None:
        exclude_files = [
            "__init__.py",
            "base.py",
            "registry.py",
            "factory.py",
            "context.py",
            "validators.py",
            "protocol.py",
        ]

    plugins: dict[str, Any] = {}
    plugin_files = list(directory.glob(pattern))

    logger.info(
        "plugin_discovery_started",
        directory=str(directory),
        pattern=pattern,
        file_count=len(plugin_files),
    )

    for plugin_file in plugin_files:
        if plugin_file.name in exclude_files:
            continue

        try:
            plugin_instances = _load_plugins_from_file(
                plugin_file=plugin_file,
                required_attrs=required_attrs,
                module_prefix=module_prefix,
                settings=settings,
            )
            plugins.update(plugin_instances)
        except Exception as e:
            logger.warning(
                "plugin_file_load_failed",
                file=plugin_file.name,
                error_type=type(e).__name__,
                error=str(e),
            )

    logger.info(
        "plugin_discovery_complete",
        total_plugins=len(plugins),
        plugin_ids=list(plugins.keys()),
    )

    return plugins


def _load_plugins_from_file(
    plugin_file: Path,
    required_attrs: list[str],
    module_prefix: str | None = None,
    settings: EngineSettings | None = None,
) -> dict[str, Any]:
    """Load plugin classes from a single Python file.

    Args:
        plugin_file: Path to the plugin .py file.
        required_attrs: Required attributes for plugin classes.
        module_prefix: Module path prefix for imports.
        settings: Optional settings for plugin constructor.

    Returns:
        Dictionary mapping plugin ID to instance.
    """
    # Build module name
    module_name = f"{module_prefix}.{plugin_file.stem}" if module_prefix else plugin_file.stem

    logger.debug("loading_plugin_module", file=plugin_file.name, module=module_name)

    # Import the module
    module = importlib.import_module(module_name)

    plugins: dict[str, Any] = {}

    # Find plugin classes
    for name, obj in inspect.getmembers(module, inspect.isclass):
        # Skip imported classes
        if obj.__module__ != module_name:
            continue

        # Check required attributes
        if not _has_required_attrs(obj, required_attrs):
            continue

        # Instantiate
        try:
            instance = obj(settings) if settings is not None else obj()
        except TypeError:
            # Try without settings
            try:
                instance = obj()
            except Exception as e:
                logger.warning(
                    "plugin_instantiation_failed",
                    class_name=name,
                    error=str(e),
                )
                continue

        # Get plugin ID
        plugin_id = _extract_plugin_id(instance, required_attrs)

        plugins[plugin_id] = instance
        logger.info(
            "plugin_loaded",
            plugin_id=plugin_id,
            class_name=name,
            file=plugin_file.name,
        )

    return plugins


def _has_required_attrs(obj: Any, required_attrs: list[str]) -> bool:
    """Check if an object has all required attributes.

    Args:
        obj: Object to check.
        required_attrs: List of required attribute names.

    Returns:
        True if all attributes present.
    """
    return all(hasattr(obj, attr) for attr in required_attrs)


def _extract_plugin_id(instance: Any, required_attrs: list[str]) -> str:
    """Extract plugin ID from an instance.

    Tries common ID attributes in order:
    1. plugin_id
    2. tool_id
    3. name
    4. First required attribute that's a string

    Args:
        instance: Plugin instance.
        required_attrs: List of required attributes.

    Returns:
        Plugin identifier string.
    """
    # Try common ID attributes
    for attr in ["plugin_id", "tool_id", "name"]:
        if hasattr(instance, attr):
            value = getattr(instance, attr)
            if isinstance(value, str):
                return value

    # Fall back to first string attribute
    for attr in required_attrs:
        if hasattr(instance, attr):
            value = getattr(instance, attr)
            if isinstance(value, str):
                return value

    # Last resort: class name
    return str(instance.__class__.__name__)


def discover_config_plugins(
    directory: Path,
    config_filename: str = "plugin.jsonld",
    alternative_filenames: list[str] | None = None,
    recursive: bool = True,
) -> dict[str, dict[str, Any]]:
    """Discover configuration-based plugins from a directory.

    Scans a directory for subdirectories containing a config file,
    loads the JSON/JSON-LD configuration, and returns the configs.

    Args:
        directory: Root directory to scan.
        config_filename: Primary config filename to look for.
        alternative_filenames: Alternative config filenames (fallbacks).
        recursive: Whether to scan subdirectories (default: True).

    Returns:
        Dictionary mapping plugin name to config dict.
        The name is extracted from the config or folder name.

    Example:
        configs = discover_config_plugins(
            directory=Path("domains/"),
            config_filename="domain.jsonld",
            alternative_filenames=["domain.json"],
        )
        # configs = {"technical": {...}, "generic": {...}}
    """
    if alternative_filenames is None:
        alternative_filenames = []

    configs: dict[str, dict[str, Any]] = {}

    if not directory.exists():
        logger.debug("config_directory_not_found", directory=str(directory))
        return configs

    logger.info(
        "config_discovery_started",
        directory=str(directory),
        config_filename=config_filename,
    )

    # Determine which items to scan
    items = [d for d in directory.iterdir() if d.is_dir()] if recursive else [directory]

    for item in items:
        if item.name.startswith("_") or item.name.startswith("."):
            continue

        # Look for config file
        config_path = _find_config_file(
            item,
            config_filename,
            alternative_filenames,
        )

        if config_path is None:
            continue

        try:
            config = _load_config_file(config_path)

            # Extract name from config or folder
            plugin_name = config.get("name", item.name)
            configs[plugin_name] = config

            logger.info(
                "config_plugin_loaded",
                plugin_name=plugin_name,
                config_path=str(config_path),
            )
        except Exception as e:
            logger.warning(
                "config_file_load_failed",
                path=str(config_path),
                error_type=type(e).__name__,
                error=str(e),
            )

    logger.info(
        "config_discovery_complete",
        total_plugins=len(configs),
        plugin_names=list(configs.keys()),
    )

    return configs


def _find_config_file(
    directory: Path,
    primary_filename: str,
    alternatives: list[str],
) -> Path | None:
    """Find a config file in a directory.

    Args:
        directory: Directory to search.
        primary_filename: Primary filename to look for.
        alternatives: Alternative filenames to try.

    Returns:
        Path to config file, or None if not found.
    """
    # Try primary filename
    config_path = directory / primary_filename
    if config_path.exists():
        return config_path

    # Try alternatives
    for alt in alternatives:
        config_path = directory / alt
        if config_path.exists():
            return config_path

    return None


def _load_config_file(config_path: Path) -> dict[str, Any]:
    """Load a JSON or JSON-LD config file.

    Args:
        config_path: Path to config file.

    Returns:
        Parsed configuration dictionary.

    Raises:
        json.JSONDecodeError: If file is not valid JSON.
    """
    from typing import cast

    content = config_path.read_text(encoding="utf-8")
    return cast("dict[str, Any]", json.loads(content))


__all__ = [
    "discover_config_plugins",
    "discover_python_plugins",
]
