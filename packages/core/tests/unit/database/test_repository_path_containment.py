# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""DatabaseRepository must use path-boundary containment, not a string prefix.

``get_database`` / ``get_database_path`` validated containment with
``realpath(db_path).startswith(realpath(databases_dir))``. A string prefix is
weaker than a path boundary: ``/data/databases`` is a prefix of the sibling
``/data/databases_evil``, so a traversal name could resolve outside the
databases directory yet still pass the check. The fix uses
``Path.is_relative_to`` (matching ``delete_database``).
"""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.database.repository import DatabaseRepository


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
