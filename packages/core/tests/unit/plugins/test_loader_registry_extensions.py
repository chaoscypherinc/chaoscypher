# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for supported_extensions validation in LoaderRegistry."""

from pathlib import Path

import pytest
from structlog.testing import capture_logs

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.loaders.registry import LoaderRegistry
from chaoscypher_core.settings import EngineSettings, PathSettings


def _write_plugin_file(dir_path: Path, filename: str, body: str) -> None:
    (dir_path / filename).write_text(body, encoding="utf-8")


def _make_settings(tmp_path: Path) -> EngineSettings:
    return EngineSettings(
        paths=PathSettings(
            data_dir=str(tmp_path),
            config_dir=str(tmp_path / "cfg"),
            cache_dir=str(tmp_path / "cache"),
        )
    )


def test_loader_with_empty_extensions_is_skipped_with_warning(tmp_path: Path) -> None:
    loaders_dir = tmp_path / "plugins" / "loaders"
    loaders_dir.mkdir(parents=True)
    _write_plugin_file(
        loaders_dir,
        "broken_loader.py",
        """
class BrokenLoader:
    supported_extensions = []
    def __init__(self, settings=None):
        self.settings = settings
""",
    )

    settings = _make_settings(tmp_path)

    with capture_logs() as logs:
        registry = LoaderRegistry(settings=settings)

    # The empty-extensions loader must not be registered under any key
    # (including the empty-string key that a naive implementation would
    # produce from supported_extensions = []).
    registered_class_names = {cls.__name__ for cls in registry.loaders.values()}
    assert "BrokenLoader" not in registered_class_names
    assert "" not in registry.loaders

    # A dedicated warning event must have been emitted.
    events = [e for e in logs if e.get("event") == "loader_empty_extensions"]
    assert len(events) >= 1
    assert events[0].get("log_level") == "warning"
    assert events[0].get("loader_class") == "BrokenLoader"


def test_loader_with_blank_extension_strings_is_skipped(tmp_path: Path) -> None:
    loaders_dir = tmp_path / "plugins" / "loaders"
    loaders_dir.mkdir(parents=True)
    _write_plugin_file(
        loaders_dir,
        "blank_loader.py",
        """
class BlankLoader:
    supported_extensions = ['', '  ']
    def __init__(self, settings=None):
        self.settings = settings
""",
    )
    settings = _make_settings(tmp_path)

    with capture_logs() as logs:
        registry = LoaderRegistry(settings=settings)

    assert "" not in registry.loaders
    events = [e for e in logs if e.get("event") == "loader_empty_extensions"]
    assert len(events) >= 1


def test_user_plugin_overrides_builtin_logs_warning(tmp_path: Path) -> None:
    """Audit fix #12: silent override of a built-in loader emits a WARNING.

    The user plugin still wins (documented behavior), but operators get a
    log line they can grep for to correlate broken uploads to plugin
    overrides.
    """
    user_plugins = tmp_path / "plugins" / "loaders"
    user_plugins.mkdir(parents=True)
    _write_plugin_file(
        user_plugins,
        "pdf_loader.py",
        """
from chaoscypher_core.plugins import PluginMetadata

class UserPdfLoader:
    @property
    def metadata(self):
        return PluginMetadata(
            plugin_id='.pdf', name='User Pdf',
            description='Custom PDF loader', category='loader',
        )
    @property
    def supported_extensions(self):
        return ['.pdf']
    def __init__(self, settings=None):
        pass
    def load_document(self, filepath):
        return [{'content': 'user', 'metadata': {}}]
""",
    )
    settings = _make_settings(tmp_path)

    with capture_logs() as logs:
        LoaderRegistry(settings=settings)

    override_events = [e for e in logs if e.get("event") == "user_plugin_overrides_builtin_loader"]
    assert len(override_events) >= 1, (
        "expected user_plugin_overrides_builtin_loader WARNING; got events: "
        + str([e.get("event") for e in logs])
    )
    assert override_events[0].get("log_level") == "warning"
    assert override_events[0].get("extension") == ".pdf"
    assert override_events[0].get("user_class") == "UserPdfLoader"


def test_quarantined_loader_surfaces_in_no_loader_error(tmp_path: Path) -> None:
    """Audit fix #13: a loader that failed to initialize is named in the user-facing error."""
    user_plugins = tmp_path / "plugins" / "loaders"
    user_plugins.mkdir(parents=True)
    _write_plugin_file(
        user_plugins,
        "broken_loader.py",
        """
class BrokenLoader:
    supported_extensions = ['.brk']
    def __init__(self, settings=None):
        raise RuntimeError('plugin author bug')
""",
    )
    settings = _make_settings(tmp_path)

    registry = LoaderRegistry(settings=settings)

    target = tmp_path / "test.brk"
    target.write_text("hi", encoding="utf-8")

    with pytest.raises(ValidationError, match="plugin author bug"):
        registry.load_document(str(target))
