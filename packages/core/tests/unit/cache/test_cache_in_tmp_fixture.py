# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The autouse fixture keeps every cache store out of the real data directory."""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.cache import wiring
from chaoscypher_core.settings import EngineSettings


def test_a_store_opened_through_the_wiring_lands_in_the_fixture_dir(llm_cache_in_tmp: Path) -> None:
    settings = EngineSettings()  # the real default data dir
    path = wiring.store_path(settings, "embeddings")
    assert path.parent == llm_cache_in_tmp
    store = wiring.open_store(settings, "embeddings")
    assert Path(store._path).parent == llm_cache_in_tmp
    real = settings.cache.resolved_dir(settings.paths.data_dir)
    assert not (real / "embeddings.db").exists() or llm_cache_in_tmp == real
