# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A test's SQLite engines are released when it ends (``sqlite_engines_released``).

``get_engine`` caches one SQLAlchemy engine per database path for the process, and
``Engine.close()`` leaves it cached (the next Engine on that path reuses it). Every test opens a
database under its own ``tmp_path``, so an xdist worker kept every test's engine, its pooled
connections and their page caches: 35 MB a test in the MCP memory tests, a worker of a 2-worker
shard climbing from 1.7 to 4.7 GB in its last minute (2026-10-26).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.adapters.sqlite import engine as sqlite_engine
from chaoscypher_core.testing.engine_fixtures import sqlite_engines_released


_PACKAGES = Path(__file__).resolve().parents[4]


def test_an_engine_opened_by_the_test_is_released_and_one_from_before_is_kept(
    tmp_path: Path,
) -> None:
    kept = sqlite_engine.get_engine(tmp_path / "before.db")
    gen = sqlite_engines_released.__wrapped__()  # type: ignore[attr-defined]  # pytest.fixture sets __wrapped__ at runtime
    next(gen)
    sqlite_engine.get_engine(tmp_path / "during.db")
    with pytest.raises(StopIteration):
        next(gen)

    cached = set(sqlite_engine._engines)
    assert str((tmp_path / "during.db").resolve()) not in cached
    assert sqlite_engine.get_engine(tmp_path / "before.db") is kept


@pytest.mark.parametrize("package", ["core", "cortex", "cli", "neuron"])
def test_every_package_conftest_releases_engines(package: str) -> None:
    conftest = (_PACKAGES / package / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "sqlite_engines_released" in conftest, f"packages/{package}/tests/conftest.py"
