# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CleanerRegistry — plugin discovery for normalizer cleaners.

Scans the built-in cleaners directory plus the user plugin directory at
``{settings.paths.data_dir}/plugins/cleaners/`` at init time. User plugins
with the same name as a built-in override the built-in. Also supports
entry-point discovery via the ``chaoscypher.cleaners`` group.

Selection semantics are pipeline-style: :meth:`list_applicable` returns
every cleaner whose ``metadata.applies_to`` accepts the metadata (or whose
``applies_to`` is ``None``), sorted by ``metadata.priority`` descending.

Constructor handling
--------------------

Built-in cleaners (``TextCleaner``, ``OCRCleaner``, ``WebCleaner``) take a
``NormalizerSettings`` argument in their ``__init__``. User-written plugin
classes are expected to take no arguments. The registry handles both by
trying ``cls(settings.normalizer)`` first and falling back to ``cls()`` on
``TypeError``. Plugins with any other signature log a warning and are
skipped.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import structlog

from chaoscypher_core.plugins.registry import BaseRegistry
from chaoscypher_core.services.sources.normalizer.cleaners.base import CleanerProtocol


if TYPE_CHECKING:
    from types import ModuleType


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


# Filenames inside the cleaners package that must never be treated as plugins.
_RESERVED_STEMS = frozenset({"base", "registry", "__init__"})


class CleanerRegistry(BaseRegistry[CleanerProtocol]):
    """Registry for normalizer cleaners.

    Discovers cleaners from:

    1. The built-in ``cleaners/`` directory (always scanned).
    2. ``{settings.paths.data_dir}/plugins/cleaners/`` (scanned only when
       ``settings`` is provided and the directory exists).
    3. Entry points under the ``chaoscypher.cleaners`` group.

    User plugins override built-ins by sharing the same ``metadata.name``.
    """

    # Dotted module paths for the shipped built-in cleaners. Loaded via
    # ``importlib.import_module`` so they share the normal sys.modules
    # cache — ``spec_from_file_location`` stays reserved for user plugins
    # where the module isn't already importable from the package tree.
    _BUILTIN_MODULES: ClassVar[tuple[str, ...]] = (
        "chaoscypher_core.services.sources.normalizer.cleaners.ocr_cleaner",
        "chaoscypher_core.services.sources.normalizer.cleaners.text_cleaner",
        "chaoscypher_core.services.sources.normalizer.cleaners.web_cleaner",
    )

    def __init__(
        self,
        settings: EngineSettings | None = None,
        database_name: str = "default",
    ) -> None:
        """Initialize the registry and run discovery.

        Args:
            settings: Engine settings. When provided, ``settings.paths.data_dir``
                is scanned for user plugins and ``settings.normalizer`` is
                passed to built-in cleaner constructors.
            database_name: Database context (unused for cleaners; kept for
                compatibility with :class:`BaseRegistry`).
        """
        # Phase 6 (2026-05-08): count user plugin load / instantiate failures.
        # Must be set BEFORE super().__init__() because the parent calls
        # _discover() → _scan_user_directory() → _register_module_classes()
        # which may increment this counter during discovery.
        self.plugin_load_failures: int = 0
        super().__init__(settings=settings, database_name=database_name)

    @property
    def plugin_entry_point_group(self) -> str | None:
        """Entry-point group for third-party cleaner packages."""
        return "chaoscypher.cleaners"

    def _discover(self) -> None:
        """Discover cleaners from built-in modules + user-plugin directory."""
        for module_path in self._BUILTIN_MODULES:
            try:
                module = importlib.import_module(module_path)
            except Exception:
                logger.warning(
                    "cleaner_builtin_import_failed",
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
            user_dir = Path(self.settings.paths.data_dir) / "plugins" / "cleaners"
            if user_dir.exists():
                self._scan_user_directory(user_dir)

    def _scan_user_directory(self, directory: Path) -> None:
        """Load every ``*.py`` user plugin file and register its cleaners.

        User plugins are loaded via ``spec_from_file_location`` (through
        :func:`load_user_python_plugin`) because their file paths aren't
        on the Python import path — they live in
        ``{data_dir}/plugins/cleaners/``.
        """
        from chaoscypher_core.plugins.user_plugin_loader import (
            load_user_python_plugin,
        )

        for file in sorted(directory.glob("*.py")):
            stem = file.stem
            if stem.startswith("_") or stem in _RESERVED_STEMS:
                continue

            module_name = f"_cleaner_plugin_{stem}_{id(file)}"
            try:
                module = load_user_python_plugin(
                    file,
                    module_name=module_name,
                    registry="CleanerRegistry",
                )
            except Exception:
                logger.warning(
                    "cleaner_plugin_load_failed",
                    path=str(file),
                    is_user=True,
                    exc_info=True,
                )
                self.plugin_load_failures += 1
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
        """Scan ``module`` for cleaner classes and register each instance."""
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if not isinstance(attr, type):
                continue
            # Skip classes that aren't defined in the plugin module itself
            # (otherwise we'd re-register imported symbols like PluginMetadata).
            if getattr(attr, "__module__", None) != module.__name__:
                continue
            if not (hasattr(attr, "metadata") and hasattr(attr, "clean")):
                continue

            instance = self._instantiate(
                attr,
                path=source_path if source_path is not None else Path(),
                is_user=is_user,
            )
            if instance is None:
                # Phase 6 (2026-05-08): count instantiation failures for user plugins
                # so the operator knows a plugin was silently dropped.
                if is_user:
                    self.plugin_load_failures += 1
                continue
            self._register(
                instance,
                source_path=source_path,
                is_user=is_user,
            )

    def _instantiate(self, cls: type, *, path: Path, is_user: bool) -> CleanerProtocol | None:
        """Instantiate a cleaner class with or without settings.

        Tries ``cls(settings.normalizer)`` first so the built-in cleaners
        (which require a ``NormalizerSettings``) work, and falls back to
        ``cls()`` so simple user plugins with bare ``__init__`` work too.

        Args:
            cls: Candidate cleaner class.
            path: Source file path (for diagnostics).
            is_user: ``True`` when the class came from a user plugin.

        Returns:
            The instantiated cleaner, or ``None`` if every constructor
            shape raised an error.
        """
        normalizer_settings = self.settings.normalizer if self.settings is not None else None

        # Try with-settings first, then no-args. Record the last failure so
        # we can log it if both attempts fail.
        attempts: list[tuple[str, tuple[object, ...]]] = []
        if normalizer_settings is not None:
            attempts.append(("with_settings", (normalizer_settings,)))
        attempts.append(("no_args", ()))

        last_error: Exception | None = None
        for label, args in attempts:
            try:
                instance: CleanerProtocol = cls(*args)
                return instance
            except TypeError as exc:
                # Signature mismatch — try the next strategy.
                last_error = exc
                logger.debug(
                    "cleaner_plugin_init_signature_mismatch",
                    class_name=cls.__name__,
                    strategy=label,
                    path=str(path),
                    is_user=is_user,
                )
            except Exception as exc:
                # Non-signature error (e.g. the constructor ran and raised).
                # Don't keep trying other signatures — record and give up.
                last_error = exc
                break

        logger.warning(
            "cleaner_plugin_instantiate_failed",
            class_name=cls.__name__,
            path=str(path),
            is_user=is_user,
            error=repr(last_error) if last_error is not None else None,
        )
        return None

    def list_applicable(self, source_metadata: Any) -> list[CleanerProtocol]:
        """Return applicable cleaners ordered by priority descending.

        A cleaner is applicable when its ``metadata.applies_to`` predicate
        returns truthy for ``source_metadata`` — or when ``applies_to`` is
        ``None`` (always applies).

        Args:
            source_metadata: Arbitrary source descriptor passed to each
                cleaner's ``applies_to`` predicate.

        Returns:
            List of applicable cleaners sorted by ``metadata.priority`` in
            descending order.
        """
        applicable: list[CleanerProtocol] = []
        for cleaner in self._plugins.values():
            predicate = cleaner.metadata.applies_to
            if predicate is None or predicate(source_metadata):
                applicable.append(cleaner)
        applicable.sort(key=lambda c: c.metadata.priority, reverse=True)
        return applicable


__all__ = ["CleanerRegistry"]
