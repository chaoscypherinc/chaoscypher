# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""DatabaseRepository must use path-boundary containment, not a string prefix.

``get_database`` / ``get_database_path`` validated containment with
``realpath(db_path).startswith(realpath(databases_dir))``. A string prefix is
weaker than a path boundary: ``/data/databases`` is a prefix of the sibling
``/data/databases_evil``, so a traversal name could resolve outside the
databases directory yet still pass the check. The fix uses
``Path.is_relative_to`` (matching ``delete_database``).

``is_relative_to`` is also reflexive: a path is relative to itself, so
``name="."`` resolves to ``databases_dir`` itself and passes the containment
guard on all three methods. On ``delete_database`` this is worse than a
no-op — it also bypasses the ``name == "default"`` check, so ``rmtree``
deletes every database in the directory before failing on the final rmdir
(entry 827). The fix adds a strict-child check
(``resolved.parent == Path(databases_dir).resolve()``) and consults
``_RESERVED_DB_NAMES`` (which already contains ``"."``) on the delete/get
paths, matching what ``create_database`` already does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.database.repository import DatabaseRepository
from chaoscypher_core.exceptions import ValidationError


def test_get_database_rejects_sibling_prefix_directory(tmp_path: Path) -> None:
    repo = DatabaseRepository(str(tmp_path))

    # A sibling directory that shares the "databases" string prefix and even
    # contains an app.db, so the os.path.exists gate alone would not stop it.
    sibling = Path(repo.databases_dir + "_evil")
    sibling.mkdir(parents=True, exist_ok=True)
    (sibling / repo.path_settings.app_db_filename).write_text("not a real db")

    traversal_name = f"../{sibling.name}"
    assert repo.get_database(traversal_name) is None
    assert repo.get_database_path(traversal_name) is None


def _seed_databases(repo: DatabaseRepository, names: tuple[str, ...]) -> None:
    """Create a real database directory (with an app.db stub) for each name."""
    for name in names:
        db_dir = Path(repo.databases_dir) / name
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / repo.path_settings.app_db_filename).write_text("not a real db")


def test_delete_database_rejects_reflexive_current_dir(tmp_path: Path) -> None:
    """``delete_database(".")`` must not resolve to ``databases_dir`` itself.

    Regression test for entry 827: reflexively, ``is_relative_to`` treats a
    path as relative to itself, so the old containment guard let ``"."``
    through and ``rmtree`` wiped every database in the directory.
    """
    repo = DatabaseRepository(str(tmp_path))
    _seed_databases(repo, ("default", "alpha", "beta"))

    with pytest.raises(ValidationError):
        repo.delete_database(".")

    # All three databases must still exist — the guard must reject before
    # any filesystem mutation happens, not merely raise after the fact.
    remaining = sorted(p.name for p in Path(repo.databases_dir).iterdir())
    assert remaining == ["alpha", "beta", "default"]


def test_get_database_rejects_reflexive_current_dir(tmp_path: Path) -> None:
    """``get_database(".")`` must not resolve to ``databases_dir`` itself."""
    repo = DatabaseRepository(str(tmp_path))
    _seed_databases(repo, ("default",))

    assert repo.get_database(".") is None


def test_get_database_path_rejects_reflexive_current_dir(tmp_path: Path) -> None:
    """``get_database_path(".")`` must not resolve inside ``databases_dir``."""
    repo = DatabaseRepository(str(tmp_path))
    _seed_databases(repo, ("default",))

    assert repo.get_database_path(".") is None


def test_delete_database_still_rejects_parent_traversal(tmp_path: Path) -> None:
    """``".."`` must remain rejected after the strict-child check lands."""
    repo = DatabaseRepository(str(tmp_path))
    _seed_databases(repo, ("default",))

    with pytest.raises(ValidationError):
        repo.delete_database("..")


def test_get_database_still_rejects_parent_traversal(tmp_path: Path) -> None:
    """``".."`` must remain rejected by ``get_database`` too."""
    repo = DatabaseRepository(str(tmp_path))
    _seed_databases(repo, ("default",))

    assert repo.get_database("..") is None


def test_get_database_path_still_rejects_parent_traversal(tmp_path: Path) -> None:
    """``".."`` must remain rejected by ``get_database_path`` too."""
    repo = DatabaseRepository(str(tmp_path))
    _seed_databases(repo, ("default",))

    assert repo.get_database_path("..") is None


def test_valid_database_names_still_work(tmp_path: Path) -> None:
    """The strict-child check must not regress ordinary, valid names."""
    repo = DatabaseRepository(str(tmp_path))
    _seed_databases(repo, ("default", "alpha"))

    assert repo.get_database("alpha") is not None
    assert repo.get_database_path("alpha") is not None

    assert repo.delete_database("alpha") is True
    assert not (Path(repo.databases_dir) / "alpha").exists()
    # The untouched sibling proves the delete was scoped to "alpha" only.
    assert (Path(repo.databases_dir) / "default").exists()
