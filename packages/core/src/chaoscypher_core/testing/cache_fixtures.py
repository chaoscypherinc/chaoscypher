# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The user's data directory, and the LLM caches in it, are temporary while tests run.

Test infrastructure only. The caches are on by default and keyed on the
real model build, so a test that embeds or chats through the real factories
with a fake provider would otherwise store fake vectors and answers in the
user's data directory (``<data_dir>/cache/llm``) — and later tests, or the
product, would serve them (found the night the cache landed, 2026-10-06).
The data directory itself is the same hazard the other way round: a test
that resolves a benchmark dataset, reference pack or workspace by its
default root reads whatever the developer's data directory holds (a
dataset overlay placed there for a generic-domain run made the install
tests assert the wrong domain, 2026-10-07). Every package's ``conftest.py``
imports :func:`data_dir_in_tmp` and :func:`llm_cache_in_tmp` the way it
imports ``ollama_offline``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.cache.wiring import CacheName
    from chaoscypher_core.settings import EngineSettings


@pytest.fixture(autouse=True)
def data_dir_in_tmp(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """Point ``CHAOSCYPHER_DATA_DIR`` at a fresh temporary directory for one test.

    Every default root under the data directory (benchmark datasets and
    their user overlay, reference packs, the workspace, the caches) then
    resolves into it; a test that wants a particular data directory sets the
    variable itself, after this fixture. Settings already memoised by
    ``get_settings`` are the caller's to reset (the CLI's ``isolated_settings``
    does).

    Returns:
        The directory the test's data lives in.
    """
    data_dir = tmp_path_factory.mktemp("data-dir")
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(data_dir))
    return data_dir


@pytest.fixture(autouse=True)
def llm_cache_in_tmp(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Path]:
    """Point every cache store at a fresh temporary directory for the duration of one test.

    ``store_path`` (the one place a store's file is resolved) is patched, the
    ``CHAOSCYPHER_CACHE__DIR`` variable is set for settings loaded from the
    environment, and the process's memoised stores are closed before and
    after, so no store opened by one test is seen by the next.

    Yields:
        The directory the caches use during the test.
    """
    from chaoscypher_core.cache import wiring

    cache_dir = tmp_path_factory.mktemp("llm-cache")

    def tmp_store_path(settings: EngineSettings, name: CacheName) -> Path:
        """``<tmp>/<name>.db`` whatever the settings say."""
        del settings
        return cache_dir / f"{name}.db"

    wiring.close_stores()
    monkeypatch.setattr(wiring, "store_path", tmp_store_path)
    monkeypatch.setenv("CHAOSCYPHER_CACHE__DIR", str(cache_dir))
    try:
        yield cache_dir
    finally:
        wiring.close_stores()


__all__ = ["data_dir_in_tmp", "llm_cache_in_tmp"]
