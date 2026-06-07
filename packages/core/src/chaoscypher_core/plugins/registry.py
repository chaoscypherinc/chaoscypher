# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Base Registry for Plugin Management.

Provides a generic registry class that all plugin registries extend.
Handles common functionality like plugin storage, lookup, listing,
and metadata aggregation.

Specific registries (LoaderRegistry, DomainRegistry, etc.) extend this
base class and implement type-specific discovery and lookup methods.

Example:
    from chaoscypher_core.plugins import BaseRegistry, PluginMetadata

    class MyRegistry(BaseRegistry[MyPlugin]):
        def _discover(self) -> None:
            # Custom discovery logic
            pass

        def _get_plugin_id(self, plugin: MyPlugin) -> str:
            return plugin.metadata.plugin_id

    registry = MyRegistry(settings)
    plugin = registry.get("my_plugin_id")
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Literal

import structlog

from chaoscypher_core.exceptions import ValidationError


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.plugins.base import PluginMetadata
    from chaoscypher_core.settings import EngineSettings


PluginOrigin = Literal["builtin", "entry_point", "user"]


class DuplicatePluginError(ValidationError, ValueError):
    """Raised when a user-space plugin registers a plugin_id already present.

    User plugins are allowed to override built-ins (that's the documented
    override semantics), but two user-space plugins claiming the same ID
    is a misconfiguration because discovery order is non-deterministic
    across operating systems.

    Multiply inherits from ValidationError (so the HTTP error mapper
    produces a structured envelope) and ValueError (so legacy
    ``except ValueError`` handlers keep catching it).

    Attributes:
        plugin_id: The conflicting plugin identifier.
        existing_path: Path of the plugin registered first.
        new_path: Path of the plugin that tried to register next.
    """

    def __init__(
        self,
        plugin_id: str,
        existing_path: str,
        new_path: str,
    ) -> None:
        """Record the conflicting plugin id and both source paths.

        Args:
            plugin_id: The conflicting plugin identifier.
            existing_path: Path of the plugin registered first.
            new_path: Path of the plugin that tried to register next.
        """
        self.plugin_id = plugin_id
        self.existing_path = existing_path
        self.new_path = new_path
        message = (
            f"Duplicate user plugin id {plugin_id!r}: "
            f"already registered from {existing_path!r}, "
            f"cannot also register from {new_path!r}"
        )
        ValidationError.__init__(
            self,
            message,
            details={
                "plugin_id": plugin_id,
                "existing_path": existing_path,
                "new_path": new_path,
            },
        )


logger = structlog.get_logger(__name__)


class BaseRegistry[T](ABC):
    """Abstract base class for plugin registries.

    Provides common functionality for all plugin registries:
    - Plugin storage and caching
    - Lookup by ID
    - Listing all plugins
    - Metadata aggregation

    Subclasses must implement:
    - _discover(): Auto-discovery logic

    Subclasses may override (defaults use ``plugin.metadata``):
    - _get_plugin_id(): Extract ID from plugin instance
    - _get_plugin_metadata(): Extract metadata from plugin instance

    Attributes:
        settings: Application settings (optional).
        database_name: Database context for per-database plugins.
        _plugins: Internal plugin storage (id -> instance).
        _discovered: Whether discovery has been run.

    Example:
        class LoaderRegistry(BaseRegistry[BaseLoader]):
            def _discover(self) -> None:
                # Scan *_loader.py files
                ...

            def _get_plugin_id(self, plugin: BaseLoader) -> str:
                # Loaders use extensions as IDs
                return plugin.supported_extensions[0]
    """

    def __init__(
        self,
        settings: EngineSettings | None = None,
        database_name: str = "default",
    ) -> None:
        """Initialize the registry.

        Args:
            settings: Application settings (optional, depends on plugin type).
            database_name: Database name for per-database plugin discovery.
        """
        self.settings = settings
        self.database_name = database_name
        self._plugins: dict[str, T] = {}
        # Tracks (source_path, is_user) for every registered plugin so we
        # can detect user-space duplicate-ID collisions.
        self._plugin_sources: dict[str, tuple[str, bool]] = {}
        # Parallel provenance map: ``builtin`` / ``entry_point`` / ``user``.
        # Always set by ``_register`` / ``_register_by_id``. Safer than
        # reading ``plugin.metadata.origin`` because some plugins expose
        # metadata via a ``@property`` whose mutation doesn't persist.
        self._plugin_origins: dict[str, PluginOrigin] = {}
        self._discovered = False

        # Run discovery
        self._discover()
        self._discover_entry_points()
        self._discovered = True

    @abstractmethod
    def _discover(self) -> None:
        """Discover and register plugins.

        Subclasses implement this to perform type-specific discovery:
        - Scan directories for plugin files
        - Import and instantiate plugins
        - Register each plugin via _register()

        This method is called automatically during __init__.
        """
        ...

    @property
    def plugin_entry_point_group(self) -> str | None:
        """Entry-point group name for external plugin discovery.

        Override in subclasses to enable entry-point scanning. Return the
        group name (e.g., ``"chaoscypher.plugins.providers"``) or ``None``
        to skip entry-point discovery.

        Returns:
            Entry-point group string, or None to disable.
        """
        return None

    def _discover_entry_points(self) -> None:
        """Discover and register plugins from Python entry points.

        Scans the entry-point group returned by ``plugin_entry_point_group``.
        Each entry point must expose a callable that returns a plugin instance.
        Skipped if ``plugin_entry_point_group`` returns None.
        """
        group = self.plugin_entry_point_group
        if group is None:
            return

        eps = entry_points(group=group)
        for ep in eps:
            try:
                factory = ep.load()
                plugin = factory()
                self._register(plugin, origin="entry_point")
                logger.info(
                    "entry_point_plugin_registered",
                    name=ep.name,
                    group=group,
                    registry=self.__class__.__name__,
                )
            except Exception:
                logger.warning(
                    "entry_point_plugin_failed",
                    name=ep.name,
                    group=group,
                    registry=self.__class__.__name__,
                    exc_info=True,
                )

    def _get_plugin_id(self, plugin: T) -> str:
        """Extract the plugin ID from a plugin instance.

        Default implementation returns ``plugin.metadata.plugin_id``.
        Override for plugins that use a different identifier (e.g., loaders
        that key by file extension).

        Args:
            plugin: Plugin instance.

        Returns:
            Plugin identifier string.
        """
        return plugin.metadata.plugin_id  # type: ignore[attr-defined, no-any-return]

    def _get_plugin_metadata(self, plugin: T) -> PluginMetadata:
        """Extract metadata from a plugin instance.

        Default implementation returns ``plugin.metadata``.
        Override for plugins that need fallback metadata generation.

        Args:
            plugin: Plugin instance.

        Returns:
            PluginMetadata for the plugin.
        """
        return plugin.metadata  # type: ignore[attr-defined, no-any-return]

    def _tag_origin(self, plugin: T, origin: PluginOrigin) -> None:
        """Stamp ``origin`` onto the plugin's metadata when possible.

        Mutates ``plugin.metadata.origin`` so downstream introspection
        (plugin-manager UIs, CLI, API surfaces) sees provenance without
        having to consult the registry's parallel bookkeeping. Silently
        no-ops when the plugin's metadata can't be mutated (e.g., a
        ``@property`` that returns a fresh instance per call) — the
        registry's internal ``_plugin_origins`` still records the value.
        """
        metadata = getattr(plugin, "metadata", None)
        if metadata is None:
            metadata = getattr(plugin, "_METADATA", None)
        if metadata is None:
            return
        try:
            metadata.origin = origin
        except (AttributeError, TypeError, ValueError):  # fmt: skip
            # Frozen / computed metadata — origin stays on the registry side.
            return

    def _register(
        self,
        plugin: T,
        *,
        source_path: Path | None = None,
        is_user: bool = False,
        origin: PluginOrigin | None = None,
    ) -> None:
        """Register a plugin instance.

        Args:
            plugin: Plugin instance to register.
            source_path: Absolute path the plugin was loaded from. When
                supplied, enables duplicate-detection.
            is_user: True when the plugin came from a user-space
                directory. Duplicate IDs among user plugins raise
                :class:`DuplicatePluginError`.
            origin: Where discovery found this plugin. Defaults to
                ``"user"`` when ``is_user`` is True, otherwise ``"builtin"``.
                Callers registering entry-point plugins should pass
                ``origin="entry_point"`` explicitly.

        Raises:
            DuplicatePluginError: Two user-space plugins claim the same
                plugin_id.
        """
        plugin_id = self._get_plugin_id(plugin)
        effective_origin: PluginOrigin = origin or ("user" if is_user else "builtin")

        if plugin_id in self._plugins:
            prior_source, prior_is_user = self._plugin_sources.get(plugin_id, ("", False))
            prior_origin = self._plugin_origins.get(plugin_id, "builtin")
            new_source = str(source_path) if source_path is not None else ""
            if is_user and prior_is_user:
                raise DuplicatePluginError(
                    plugin_id=plugin_id,
                    existing_path=prior_source,
                    new_path=new_source,
                )
            logger.warning(
                "plugin_already_registered",
                plugin_id=plugin_id,
                registry=self.__class__.__name__,
                prior_source=prior_source,
                new_source=new_source,
                previous_origin=prior_origin,
                new_origin=effective_origin,
            )
            # Allow overwrite (user + entry-point plugins override builtins)

        self._tag_origin(plugin, effective_origin)
        self._plugins[plugin_id] = plugin
        self._plugin_origins[plugin_id] = effective_origin
        if source_path is not None:
            self._plugin_sources[plugin_id] = (str(source_path), is_user)
        logger.debug(
            "plugin_registered",
            plugin_id=plugin_id,
            registry=self.__class__.__name__,
            origin=effective_origin,
        )

    def _register_by_id(
        self,
        plugin_id: str,
        plugin: T,
        *,
        source_path: Path | None = None,
        is_user: bool = False,
        origin: PluginOrigin | None = None,
    ) -> None:
        """Register a plugin with an explicit ID.

        Useful when a plugin has multiple IDs (e.g., loaders with multiple extensions).

        Args:
            plugin_id: Explicit ID to register under.
            plugin: Plugin instance.
            source_path: Absolute path the plugin was loaded from.
            is_user: True when the plugin came from a user-space directory.
            origin: Where discovery found this plugin (see :meth:`_register`).

        Raises:
            DuplicatePluginError: Two user-space plugins claim the same
                plugin_id.
        """
        effective_origin: PluginOrigin = origin or ("user" if is_user else "builtin")

        if plugin_id in self._plugins:
            prior_source, prior_is_user = self._plugin_sources.get(plugin_id, ("", False))
            prior_origin = self._plugin_origins.get(plugin_id, "builtin")
            new_source = str(source_path) if source_path is not None else ""
            if is_user and prior_is_user:
                raise DuplicatePluginError(
                    plugin_id=plugin_id,
                    existing_path=prior_source,
                    new_path=new_source,
                )
            logger.warning(
                "plugin_already_registered",
                plugin_id=plugin_id,
                registry=self.__class__.__name__,
                prior_source=prior_source,
                new_source=new_source,
                previous_origin=prior_origin,
                new_origin=effective_origin,
            )

        self._tag_origin(plugin, effective_origin)
        self._plugins[plugin_id] = plugin
        self._plugin_origins[plugin_id] = effective_origin
        if source_path is not None:
            self._plugin_sources[plugin_id] = (str(source_path), is_user)
        logger.debug(
            "plugin_registered",
            plugin_id=plugin_id,
            registry=self.__class__.__name__,
            origin=effective_origin,
        )

    def get(self, plugin_id: str) -> T | None:
        """Get a plugin by ID.

        Args:
            plugin_id: Plugin identifier.

        Returns:
            Plugin instance if found, None otherwise.

        Example:
            loader = registry.get(".pdf")
            if loader:
                chunks = loader.load_document(filepath)
        """
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            logger.debug(
                "plugin_not_found",
                plugin_id=plugin_id,
                available=list(self._plugins.keys()),
                registry=self.__class__.__name__,
            )
        return plugin

    def get_required(self, plugin_id: str) -> T:
        """Get a plugin by ID, raising if not found.

        Args:
            plugin_id: Plugin identifier.

        Returns:
            Plugin instance.

        Raises:
            KeyError: If plugin not found.

        Example:
            loader = registry.get_required(".pdf")
            chunks = loader.load_document(filepath)
        """
        plugin = self.get(plugin_id)
        if plugin is None:
            msg = f"Plugin not found: {plugin_id}. Available: {list(self._plugins.keys())}"
            raise KeyError(msg)
        return plugin

    def list_all(self) -> dict[str, T]:
        """Get all registered plugins.

        Returns:
            Dictionary mapping plugin ID to instance.

        Example:
            for plugin_id, plugin in registry.list_all().items():
                print(f"{plugin_id}: {plugin}")
        """
        return self._plugins.copy()

    def count(self) -> int:
        """Get the number of registered plugins.

        Returns:
            Plugin count.
        """
        return len(self._plugins)

    def origin_of(self, plugin_id: str) -> PluginOrigin | None:
        """Return where discovery found the plugin with ``plugin_id``.

        Returns:
            ``"builtin"`` / ``"entry_point"`` / ``"user"`` when the
            plugin is registered, ``None`` otherwise.
        """
        return self._plugin_origins.get(plugin_id)

    def reload(self) -> None:
        """Re-run discovery on this registry instance.

        Clears all currently registered plugins and re-invokes
        ``_discover()`` + entry-point scanning. Useful for
        administratively picking up freshly-added plugin files without
        restarting the process.
        """
        logger.info(
            "registry_reload_started",
            registry=self.__class__.__name__,
            prior_count=len(self._plugins),
        )
        self._plugins.clear()
        self._plugin_sources.clear()
        self._plugin_origins.clear()
        self._discovered = False
        self._discover()
        self._discover_entry_points()
        self._discovered = True
        logger.info(
            "registry_reload_complete",
            registry=self.__class__.__name__,
            new_count=len(self._plugins),
        )


__all__ = ["BaseRegistry", "DuplicatePluginError", "PluginOrigin"]
