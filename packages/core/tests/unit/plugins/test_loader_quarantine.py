# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests that LoaderRegistry quarantines classes whose __init__ raises."""

from pathlib import Path

from structlog.testing import capture_logs

from chaoscypher_core.services.sources.loaders.registry import LoaderRegistry
from chaoscypher_core.settings import EngineSettings, PathSettings


def _make_settings(tmp_path: Path) -> EngineSettings:
    return EngineSettings(
        paths=PathSettings(
            data_dir=str(tmp_path),
            config_dir=str(tmp_path / "c"),
            cache_dir=str(tmp_path / "ch"),
        )
    )


def test_failed_class_is_quarantined_on_second_discover(tmp_path: Path) -> None:
    """A loader whose __init__ raises logs a warning once and then DEBUG-only."""
    loaders_dir = tmp_path / "plugins" / "loaders"
    loaders_dir.mkdir(parents=True)
    (loaders_dir / "bad_loader.py").write_text(
        """
class BadLoader:
    supported_extensions = ['.bad']
    def __init__(self, settings=None):
        raise RuntimeError("missing native dep")
""",
        encoding="utf-8",
    )

    settings = _make_settings(tmp_path)

    with capture_logs() as logs_first:
        registry = LoaderRegistry(settings=settings)

    first_warnings = [
        e
        for e in logs_first
        if e.get("event") == "loader_instantiation_failed" and e.get("log_level") == "warning"
    ]
    assert len(first_warnings) == 1

    # Force a rediscover on the same registry instance.
    with capture_logs() as logs_second:
        registry._discover()

    second_warnings = [
        e
        for e in logs_second
        if e.get("event") == "loader_instantiation_failed" and e.get("log_level") == "warning"
    ]
    assert second_warnings == [], "quarantined classes must not re-warn"

    skip_events = [e for e in logs_second if e.get("event") == "loader_class_quarantined"]
    assert len(skip_events) == 1
    assert skip_events[0].get("log_level") == "debug"
    assert skip_events[0].get("loader_class") == "BadLoader"
