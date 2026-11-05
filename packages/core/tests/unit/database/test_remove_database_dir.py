# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``remove_database_dir`` evicts every cached engine under a database folder, then removes it.

A cached engine keeps pooled connections open on its file. Deleting the folder
under it left those handles open on a deleted file (on Windows the delete
fails outright), and the next ``get_engine`` for the path had to notice the
file had changed. Every place that deletes a database folder goes through this
one helper: the composed database of ``compose build``, the database
repository's delete and ``chaoscypher db delete``.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text

from chaoscypher_core.adapters.sqlite import engine as engine_module
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.database.engine import remove_database_dir


_USED: list[Path] = []


@pytest.fixture(autouse=True)
def _evict_used_paths() -> Iterator[None]:
    yield
    for path in _USED:
        evict_engine(path)
    _USED.clear()


def _open(path: Path) -> None:
    _USED.append(path)
    with get_engine(path).begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS pack_a (x)"))


def _cached(path: Path) -> bool:
    return str(path.resolve()) in engine_module._engines


def test_evicts_the_engines_under_the_folder_then_removes_it(tmp_path: Path) -> None:
    folder = tmp_path / "databases" / "pack-a"
    main = folder / "app.db"
    nested = folder / "graphs" / "extra.db"
    neighbour = tmp_path / "databases" / "pack-a-copy" / "app.db"
    for path in (main, nested, neighbour):
        _open(path)

    remove_database_dir(folder)

    assert not folder.exists()
    assert not _cached(main)
    assert not _cached(nested)
    # A folder whose name merely starts with the same letters is not inside it.
    assert _cached(neighbour)


def test_engines_are_disposed_before_the_folder_is_removed(tmp_path: Path) -> None:
    folder = tmp_path / "pack-a"
    _open(folder / "app.db")
    cached_when_removed: list[bool] = []
    real_rmtree = shutil.rmtree

    def _rmtree(path: Path, *args: object, **kwargs: object) -> None:
        cached_when_removed.append(_cached(folder / "app.db"))
        real_rmtree(path)

    with patch("chaoscypher_core.database.engine.shutil.rmtree", side_effect=_rmtree):
        remove_database_dir(folder)

    assert cached_when_removed == [False]
    assert not folder.exists()


def test_a_missing_folder_is_nothing_to_do(tmp_path: Path) -> None:
    remove_database_dir(tmp_path / "never-built")


def test_the_repository_delete_goes_through_it(tmp_path: Path) -> None:
    with patch("chaoscypher_core.app_config.PathSettings") as mock_ps:
        mock_ps.return_value.databases_subdir = "databases"
        mock_ps.return_value.app_db_filename = "app.db"
        from chaoscypher_core.database.repository import DatabaseRepository

        repo = DatabaseRepository(data_root=str(tmp_path))
    db = Path(repo.databases_dir) / "pack-a" / "app.db"
    _open(db)

    assert repo.delete_database("pack-a") is True

    assert not db.parent.exists()
    assert not _cached(db)
