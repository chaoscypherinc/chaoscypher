# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests: loader registry cache keys on data_dir, not id(settings).

The prior ``id(settings)`` key meant every caller holding a freshly built
``EngineSettings`` (per-request handlers, the multi-database rebind path)
missed the cache forever and leaked one dead entry per settings object.
The cache now keys on the resolved user-plugin root, mirroring
``LoaderRegistry._get_user_plugins_path`` resolution.
"""

from pathlib import Path

from chaoscypher_core.services.sources.loaders.factory import (
    clear_loader_registry_cache,
    get_loader_registry,
)
from chaoscypher_core.settings import EngineSettings, PathSettings


def _make_settings(data_dir: Path) -> EngineSettings:
    return EngineSettings(paths=PathSettings(data_dir=str(data_dir)))


def test_fresh_settings_with_same_data_dir_hit_cache(tmp_path: Path) -> None:
    clear_loader_registry_cache()
    try:
        first = get_loader_registry(_make_settings(tmp_path / "data"))
        second = get_loader_registry(_make_settings(tmp_path / "data"))
        assert first is second
    finally:
        clear_loader_registry_cache()


def test_different_data_dirs_get_distinct_registries(tmp_path: Path) -> None:
    clear_loader_registry_cache()
    try:
        first = get_loader_registry(_make_settings(tmp_path / "data-a"))
        second = get_loader_registry(_make_settings(tmp_path / "data-b"))
        assert first is not second
    finally:
        clear_loader_registry_cache()
