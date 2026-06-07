# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ArchiveHandlerRegistry — plugin discovery for archive format handlers.

Scans the built-in handlers directory plus the user plugin directory at
``{settings.paths.data_dir}/plugins/archive_handlers/`` at init time. User
plugins with the same ``metadata.name`` as a built-in override the built-in.
Also supports entry-point discovery via the ``chaoscypher.archive_handlers``
group.

Selection semantics are exclusive: :meth:`find_handler` walks every
registered handler, calls ``can_handle()``, and returns the one with the
highest specificity score. Ties break by registration order (built-ins
before user plugins unless a user plugin overrides by name).

Constructor handling
--------------------

Built-in handlers (``SphinxHTMLHandler``, ``MarkdownHandler``,
``OpenAPIHandler``, ``GenericHandler``) accept an optional
``EngineSettings`` argument in their ``__init__``. User-written plugin
classes are expected to take no arguments. The registry handles both by
trying ``cls(settings)`` first and falling back to ``cls()`` on
``TypeError``. Plugins with any other signature log a warning and are
skipped.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import structlog

from chaoscypher_core.plugins.registry import BaseRegistry
from chaoscypher_core.services.sources.loaders.archive.handlers.base import (
    ArchiveHandler,
)


if TYPE_CHECKING:
    from types import ModuleType


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


# Filenames inside the handlers package that must never be treated as plugins.
_RESERVED_STEMS = frozenset({"base", "registry", "__init__"})


class ArchiveHandlerRegistry(BaseRegistry[ArchiveHandler]):
    """Registry for archive format handlers.

    Discovers handlers from:

    1. The built-in ``handlers/`` directory (always scanned).
    2. ``{settings.paths.data_dir}/plugins/archive_handlers/`` (scanned only
       when ``settings`` is provided and the directory exists).
    3. Entry points under the ``chaoscypher.archive_handlers`` group.

    User plugins override built-ins by sharing the same ``metadata.name``.
    """

    # Dotted module paths for the shipped built-in handlers. Loaded via
    # ``importlib.import_module`` so they share the normal sys.modules
    # cache — ``spec_from_file_location`` stays reserved for user plugins
    # where the module isn't importable from the package tree.
    _BUILTIN_MODULES: ClassVar[tuple[str, ...]] = (
        "chaoscypher_core.services.sources.loaders.archive.handlers.sphinx_handler",
        "chaoscypher_core.services.sources.loaders.archive.handlers.markdown_handler",
        "chaoscypher_core.services.sources.loaders.archive.handlers.openapi_handler",
        "chaoscypher_core.services.sources.loaders.archive.handlers.generic_handler",
    )

    @property
    def plugin_entry_point_group(self) -> str | None:
        """Entry-point group for third-party archive-handler packages."""
        return "chaoscypher.archive_handlers"

    def _discover(self) -> None:
        """Discover handlers from built-in modules + user-plugin directory."""
        for module_path in self._BUILTIN_MODULES:
            try:
                module = importlib.import_module(module_path)
            except Exception:
                logger.warning(
                    "archive_handler_builtin_import_failed",
                    module=module_path,
                    exc_info=True,
                )
                continue
            self._register_module_classes(
                module,
                source_path=Path(module.__file__) if module.__file__ else None,
                is_user=False,
            )

        if self.settings is not None:
            user_dir = Path(self.settings.paths.data_dir) / "plugins" / "archive_handlers"
            if user_dir.exists():
                self._scan_user_directory(user_dir)

    def _scan_user_directory(self, directory: Path) -> None:
        """Load every ``*.py`` user plugin and register its handlers.

        User plugins are loaded via ``spec_from_file_location`` (through
        :func:`load_user_python_plugin`) because their file paths aren't
        on the Python import path — they live in
        ``{data_dir}/plugins/archive_handlers/``.
        """
        from chaoscypher_core.plugins.user_plugin_loader import (
            load_user_python_plugin,
        )

        for file in sorted(directory.glob("*.py")):
            stem = file.stem
            if stem.startswith("_") or stem in _RESERVED_STEMS:
                continue

            module_name = f"_archive_handler_plugin_{stem}_{id(file)}"
            try:
                module = load_user_python_plugin(
                    file,
                    module_name=module_name,
                    registry="ArchiveHandlerRegistry",
                )
            except Exception:
                logger.warning(
                    "archive_handler_plugin_load_failed",
                    path=str(file),
                    is_user=True,
                    exc_info=True,
                )
                continue
            if module is None:
                continue
            self._register_module_classes(module, source_path=file, is_user=True)

    def _register_module_classes(
        self,
        module: ModuleType,
        *,
        source_path: Path | None,
        is_user: bool,
    ) -> None:
        """Scan ``module`` for archive-handler classes and register each."""
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if not isinstance(attr, type):
                continue
            # Skip classes that aren't defined in the plugin module itself
            # (otherwise we'd re-register imported symbols like PluginMetadata).
            if getattr(attr, "__module__", None) != module.__name__:
                continue
            if not (
                hasattr(attr, "metadata")
                and hasattr(attr, "can_handle")
                and hasattr(attr, "process")
            ):
                continue

            instance = self._instantiate(
                attr,
                path=source_path if source_path is not None else Path(),
                is_user=is_user,
            )
            if instance is None:
                continue
            self._register(
                instance,
                source_path=source_path,
                is_user=is_user,
            )

    def _instantiate(self, cls: type, *, path: Path, is_user: bool) -> ArchiveHandler | None:
        """Instantiate a handler class with or without settings.

        Tries ``cls(settings)`` first so the built-in handlers (which accept
        an optional ``EngineSettings``) receive one when the registry has
        settings, and falls back to ``cls()`` so simple user plugins with
        bare ``__init__`` work too.

        Args:
            cls: Candidate handler class.
            path: Source file path (for diagnostics).
            is_user: ``True`` when the class came from a user plugin.

        Returns:
            The instantiated handler, or ``None`` if every constructor
            shape raised an error.
        """
        attempts: list[tuple[str, tuple[object, ...]]] = []
        if self.settings is not None:
            attempts.append(("with_settings", (self.settings,)))
        attempts.append(("no_args", ()))

        last_error: Exception | None = None
        for label, args in attempts:
            try:
                instance: ArchiveHandler = cls(*args)
                return instance
            except TypeError as exc:
                # Signature mismatch — try the next strategy.
                last_error = exc
                logger.debug(
                    "archive_handler_plugin_init_signature_mismatch",
                    class_name=cls.__name__,
                    strategy=label,
                    path=str(path),
                    is_user=is_user,
                )
            except Exception as exc:
                # Non-signature error (e.g. constructor ran and raised).
                # Don't keep trying other signatures — record and give up.
                last_error = exc
                break

        logger.warning(
            "archive_handler_plugin_instantiate_failed",
            class_name=cls.__name__,
            path=str(path),
            is_user=is_user,
            error=repr(last_error) if last_error is not None else None,
        )
        return None

    def find_handler(self, extracted_dir: Path) -> ArchiveHandler | None:
        """Find the most specific handler for an extracted archive.

        Walks every registered handler, calls ``can_handle()``, and returns
        the one with the highest non-zero score. Handlers returning ``0``
        are considered non-applicable. On ties, the first handler
        registered wins (built-ins before user plugins unless a user plugin
        overrides by name).

        Args:
            extracted_dir: Path to extracted archive contents.

        Returns:
            The most specific handler, or ``None`` if no handler claims the
            archive.
        """
        best: ArchiveHandler | None = None
        best_score = 0
        for handler in self._plugins.values():
            try:
                score = handler.can_handle(extracted_dir)
            except Exception:
                logger.warning(
                    "archive_handler_can_handle_failed",
                    handler=handler.metadata.name,
                    extracted_dir=str(extracted_dir),
                    exc_info=True,
                )
                continue
            if score > best_score:
                best_score = score
                best = handler
        return best

    def __init__(
        self,
        settings: EngineSettings | None = None,
        database_name: str = "default",
    ) -> None:
        """Initialize the registry and run discovery.

        Args:
            settings: Engine settings. When provided, ``settings.paths.data_dir``
                is scanned for user plugins and ``settings`` itself is passed
                to built-in handler constructors.
            database_name: Database context (unused for archive handlers;
                kept for compatibility with :class:`BaseRegistry`).
        """
        super().__init__(settings=settings, database_name=database_name)


__all__ = ["ArchiveHandlerRegistry"]
