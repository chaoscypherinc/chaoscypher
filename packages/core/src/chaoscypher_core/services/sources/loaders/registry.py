# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Loader Registry with Auto-Discovery.

Automatically discovers and registers document loaders from the loaders/ directory.
Users can add custom loaders by creating a new file with a loader class that has
a 'supported_extensions' property.

This registry extends the shared plugin infrastructure while maintaining
backward compatibility with existing loader discovery patterns.

Example Custom Loader:
    ```python
    # loaders/excel_loader.py
    from chaoscypher_core.plugins import PluginMetadata

    class ExcelLoader:
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(
                plugin_id="excel",
                name="Excel Loader",
                description="Loads Excel spreadsheets",
                category="loader",
            )

        @property
        def supported_extensions(self):
            return ['.xlsx', '.xls']

        def __init__(self, settings=None):
            self.settings = settings

        def load_document(self, filepath: str) -> list[dict]:
            # Your loading logic here
            return [{"content": "...", "metadata": {}}]

        def supports_ocr(self):
            return False
    ```

The loader will be automatically discovered and registered on import.
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import NotFoundError, ValidationError
from chaoscypher_core.plugins import BaseRegistry, PluginMetadata


if TYPE_CHECKING:
    from types import ModuleType

    from chaoscypher_core.services.sources.loaders.base import BaseLoader
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class LoaderRegistry(BaseRegistry["BaseLoader"]):
    """Registry for auto-discovering and managing document loaders.

    Extends BaseRegistry to provide standardized plugin management while
    maintaining backward compatibility with the existing loader pattern.

    Loaders are keyed by file extension (e.g., ".pdf", ".txt") rather than
    a single plugin_id, allowing multiple extensions per loader.

    Attributes:
        loaders: Dictionary mapping file extensions to loader classes.
        settings: Application settings passed to loader instances.

    Example:
        registry = LoaderRegistry(settings)
        loader = registry.get_loader('/path/to/file.pdf')
        chunks = loader.load_document('/path/to/file.pdf')
    """

    def __init__(
        self, settings: EngineSettings | None = None, database_name: str = "default"
    ) -> None:
        """Initialize loader registry with auto-discovery.

        Args:
            settings: Application settings (required for engine usage).
            database_name: Database name (unused for loaders, kept for interface).

        Raises:
            ValidationError: If settings is None.
        """
        if settings is None:
            msg = "LoaderRegistry requires settings parameter"
            raise ValidationError(msg, field="settings")

        # Loader-specific state (before super().__init__ which calls _discover)
        self.loaders: dict[str, Any] = {}  # ext -> loader class
        self._loader_instances: dict[str, Any] = {}  # ext -> loader instance (cached)
        # Classes that have failed instantiation at least once -- skip on rediscover.
        # Keyed by (module_name, class_name) so it survives re-imports that
        # produce new class objects with the same identifier.
        self._failed_classes: set[tuple[str, str]] = set()
        # Maps extension -> instantiation failure reason so load_document can
        # surface the cause in user-facing errors (audit fix #13).
        self._failed_loaders_by_ext: dict[str, str] = {}

        # Set settings before super().__init__ (which calls _discover)
        self.settings = settings

        # Call parent init (triggers _discover)
        super().__init__(settings=settings, database_name=database_name)

    def _get_user_plugins_path(self) -> Path | None:
        """Get user plugins path from settings.

        Returns:
            Path to data/plugins/loaders/ directory, or None.
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

        return Path(data_dir) / "plugins" / "loaders"

    def _discover(self) -> None:
        """Auto-discover loader classes from built-in and user directories.

        Scans both the built-in loaders directory and user plugins directory
        for classes that have a 'supported_extensions' property.

        Implements BaseRegistry._discover().
        """
        # Collect all loader files from built-in and user directories
        search_paths: list[tuple[str, Path]] = [
            ("builtin", Path(__file__).parent),
        ]

        # Add user plugins path if settings available
        user_plugins_path = self._get_user_plugins_path()
        if user_plugins_path and user_plugins_path.exists():
            search_paths.append(("user", user_plugins_path))

        for path_type, loaders_dir in search_paths:
            loader_files = sorted(loaders_dir.glob("*_loader.py"))

            logger.info(
                "loader_discovery_started",
                loaders_directory=str(loaders_dir),
                path_type=path_type,
                loader_files=[f.name for f in loader_files],
            )

            for loader_file in loader_files:
                if loader_file.stem in ["__init__", "base", "registry", "factory"]:
                    continue

                self._load_loader_from_file(loader_file, path_type)

        logger.info(
            "loader_discovery_complete",
            total_loaders=len(set(self.loaders.values())),
            total_extensions=len(self.loaders),
            extensions=list(self.loaders.keys()),
        )

    def _record_loader_failure(
        self,
        name: str,
        obj: type,
        instance: Any,
        exc: Exception,
        loader_file: Path,
    ) -> None:
        """Record a loader-class failure so future load_document calls can surface the cause.

        Adds the (module, class) tuple to ``_failed_classes`` (suppresses re-instantiation
        on the next discovery pass) and indexes the failure reason by lowercase extension
        in ``_failed_loaders_by_ext`` so ``load_document``'s no-loader-available error
        names the broken loader instead of just the supported-extensions list (audit fix #13).

        Falls back from ``instance.supported_extensions`` to the class-level
        ``supported_extensions`` attribute when instantiation itself failed.

        Args:
            name: Class name of the failing loader.
            obj: The loader class object.
            instance: Partially-constructed instance, or None if instantiation raised.
            exc: The exception that was caught.
            loader_file: Source file path (used for the log field ``module``).
        """
        self._failed_classes.add((getattr(obj, "__module__", ""), name))

        ext_source: Any = None
        if instance is not None:
            with contextlib.suppress(Exception):
                ext_source = instance.supported_extensions
        if ext_source is None:
            ext_source = getattr(obj, "supported_extensions", None)

        if ext_source:
            with contextlib.suppress(TypeError):
                for failed_ext in ext_source:
                    if isinstance(failed_ext, str) and failed_ext.strip():
                        self._failed_loaders_by_ext[failed_ext.lower()] = (
                            f"{name}: {type(exc).__name__}: {exc}"
                        )

        logger.warning(
            "loader_instantiation_failed",
            loader_class=name,
            module=loader_file.stem,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    def _load_loader_from_file(self, loader_file: Path, path_type: str) -> None:
        """Load a loader from a Python file.

        Args:
            loader_file: Path to the loader .py file.
            path_type: "builtin" or "user".
        """
        try:
            module: ModuleType | None
            if path_type == "builtin":
                # Import using standard module path
                module_name = f"chaoscypher_core.services.sources.loaders.{loader_file.stem}"
                module = importlib.import_module(module_name)
            else:
                from chaoscypher_core.plugins.user_plugin_loader import (
                    load_user_python_plugin,
                )

                module_name = f"user_loader_{loader_file.stem}"
                module = load_user_python_plugin(
                    loader_file,
                    module_name=module_name,
                    registry="LoaderRegistry",
                )
                if module is None:
                    return

            # Find loader classes in the module
            for name, obj in inspect.getmembers(module, inspect.isclass):
                # Skip quarantined classes silently (one DEBUG log per pass).
                quarantine_key = (getattr(obj, "__module__", ""), name)
                if quarantine_key in self._failed_classes:
                    logger.debug(
                        "loader_class_quarantined",
                        loader_class=name,
                        module=loader_file.stem,
                    )
                    continue

                # Check if class has supported_extensions property
                if hasattr(obj, "supported_extensions"):
                    instance = None  # may stay None if instantiation fails
                    try:
                        # Instantiate to get supported_extensions
                        instance = obj(self.settings)
                        extensions = instance.supported_extensions

                        # Validate extensions -- reject empty lists and blank strings.
                        valid_extensions: list[str] = []
                        if extensions is not None:
                            try:
                                iter(extensions)
                            except TypeError:
                                extensions = []
                            valid_extensions.extend(
                                ext for ext in extensions if isinstance(ext, str) and ext.strip()
                            )

                        if not valid_extensions:
                            logger.warning(
                                "loader_empty_extensions",
                                loader_class=name,
                                module=loader_file.stem,
                                path_type=path_type,
                                raw_value=repr(extensions),
                            )
                            continue

                        # Register for each extension (loaders use extension as ID).
                        # Dedupe by lowercased extension — loaders commonly declare
                        # case variants (``.zip`` and ``.ZIP``) in supported_extensions
                        # for documentation, but they collapse to the same registry
                        # key once lowercased. Without the dedupe each case-variant
                        # triggered ``plugin_already_registered`` on the second pass.
                        seen_exts: set[str] = set()
                        for ext in valid_extensions:
                            ext_lower = ext.lower()
                            if ext_lower in seen_exts:
                                continue
                            seen_exts.add(ext_lower)
                            self._failed_loaders_by_ext.pop(
                                ext_lower, None
                            )  # success clears any prior quarantine
                            # Audit fix #12: warn operators when a user plugin
                            # overrides a built-in loader (override is documented
                            # behavior, but should not be silent).
                            if ext_lower in self.loaders and path_type == "user":
                                existing_class = self.loaders[ext_lower]
                                logger.warning(
                                    "user_plugin_overrides_builtin_loader",
                                    extension=ext_lower,
                                    user_class=name,
                                    builtin_class=existing_class.__name__,
                                    user_module=loader_file.stem,
                                )
                            self.loaders[ext_lower] = obj
                            self._loader_instances[ext_lower] = instance
                            # Also register in parent's _plugins dict
                            self._register_by_id(
                                ext_lower,
                                instance,
                                source_path=loader_file,
                                is_user=(path_type == "user"),
                            )

                        logger.info(
                            "loader_registered",
                            loader_class=name,
                            extensions=valid_extensions,
                            module=loader_file.stem,
                            path_type=path_type,
                        )

                    except Exception as e:
                        self._record_loader_failure(name, obj, instance, e, loader_file)

        except Exception as e:
            logger.warning(
                "loader_module_import_failed",
                module=loader_file.stem,
                error_type=type(e).__name__,
                error_message=str(e),
            )

    def _get_plugin_id(self, plugin: BaseLoader) -> str:
        """Extract plugin ID from a loader instance.

        For loaders, uses the first supported extension as ID.

        Args:
            plugin: Loader instance.

        Returns:
            First supported extension (e.g., ".pdf").
        """
        if hasattr(plugin, "supported_extensions") and plugin.supported_extensions:
            return plugin.supported_extensions[0].lower()
        return plugin.__class__.__name__.lower()

    def _get_plugin_metadata(self, plugin: BaseLoader) -> PluginMetadata:
        """Extract metadata from a loader instance.

        Falls back to generating metadata from class info if loader
        doesn't implement the metadata property.

        Args:
            plugin: Loader instance.

        Returns:
            PluginMetadata for the loader.
        """
        # Try to get metadata from plugin
        if hasattr(plugin, "metadata"):
            try:
                return plugin.metadata
            except (AttributeError, NotImplementedError):  # fmt: skip
                pass

        # Generate metadata from class info
        class_name = plugin.__class__.__name__
        extensions = getattr(plugin, "supported_extensions", [])
        ext_str = ", ".join(extensions) if extensions else "unknown"

        return PluginMetadata(
            plugin_id=self._get_plugin_id(plugin),
            name=class_name,
            description=f"Loads {ext_str} files",
            category="loader",
        )

    # --- Backward-compatible API ---

    def get_loader(self, filepath: str) -> Any | None:
        """Get appropriate loader for a file.

        Args:
            filepath: Path to the file.

        Returns:
            Loader instance or None if no loader found.

        Example:
            >>> loader = registry.get_loader('/path/to/file.pdf')
            >>> chunks = loader.load_document('/path/to/file.pdf')
        """
        path = Path(filepath)
        # Match compound extensions (".tar.gz") before the single suffix.
        # ``Path.suffix`` is only the last component (".gz" for "x.tar.gz"),
        # so a loader registered under ".tar.gz" would otherwise be
        # unreachable. Try progressively shorter suffix runs, longest first,
        # so the most specific registered extension wins.
        suffixes = path.suffixes
        for start in range(len(suffixes)):
            compound = "".join(suffixes[start:]).lower()
            loader = self.get(compound)
            if loader is not None:
                return loader
        return self.get(path.suffix.lower())

    def load_document(self, filepath: str) -> list[dict[str, Any]]:
        """Load a document via the registered loader for its extension.

        The registry no longer chunks output. Chunking is the
        ChunkingService's responsibility (canonical chunking layer for
        the indexing pipeline). This method always returns the loader's
        raw documents: one or more dicts with 'content' and 'metadata'.

        Args:
            filepath: Path to the file.

        Returns:
            List of raw document dicts from the loader.

        Raises:
            NotFoundError: If filepath does not exist.
            ValidationError: If no loader available for file type.

        Example:
            >>> registry = LoaderRegistry(settings)
            >>> docs = registry.load_document('/path/to/file.pdf')
            >>> print(docs[0].keys())
            dict_keys(['content', 'metadata'])
        """
        file_path = Path(filepath)

        if not file_path.exists():
            logger.error("file_not_found", filepath=filepath)
            raise NotFoundError("File", filepath)

        logger.info("document_loading_started", filepath=filepath)

        loader = self.get_loader(filepath)

        if loader is None:
            logger.error("no_loader_available", filepath=filepath, extension=file_path.suffix)
            quarantine_reason = self._failed_loaders_by_ext.get(file_path.suffix.lower())
            if quarantine_reason:
                msg = (
                    f"No loader available for file type: {file_path.suffix}."
                    f" A loader for this extension exists but failed to"
                    f" initialize: {quarantine_reason}"
                )
            else:
                msg = (
                    f"No loader available for file type: {file_path.suffix}."
                    f" Supported extensions: {list(self.loaders.keys())}"
                )
            raise ValidationError(msg, field="extension")

        try:
            documents: list[dict[str, Any]] = loader.load_document(filepath)

            if not documents:
                logger.warning("document_no_content_loaded", filepath=filepath)
                return []

            logger.info("document_loaded", chunk_count=len(documents), filepath=filepath)
            return documents

        except Exception as e:
            logger.exception(
                "document_load_failed",
                filepath=filepath,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

    def list_supported_extensions(self) -> list[str]:
        """Get list of all supported file extensions.

        Returns:
            List of supported extensions (e.g., ['.pdf', '.txt', '.csv']).

        Example:
            >>> registry.list_supported_extensions()
            ['.pdf', '.txt', '.csv', '.json', '.html', '.docx']
        """
        return sorted(set(self.loaders.keys()))

    def list_loaders(self) -> dict[str, list[str]]:
        """Get dictionary of loader classes and their supported extensions.

        Returns:
            Dictionary mapping loader class names to extension lists.

        Example:
            >>> registry.list_loaders()
            {
                'PdfLoader': ['.pdf'],
                'TextLoader': ['.txt', '.md'],
                'CSVLoader': ['.csv']
            }
        """
        loader_map: dict[str, list[str]] = {}
        for ext, loader_class in self.loaders.items():
            class_name = loader_class.__name__
            if class_name not in loader_map:
                loader_map[class_name] = []
            loader_map[class_name].append(ext)

        return {k: sorted(v) for k, v in loader_map.items()}


__all__ = ["LoaderRegistry"]
