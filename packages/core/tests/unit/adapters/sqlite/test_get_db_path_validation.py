# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``get_db_path`` must reject database names that escape the databases dir.

Pre-fix, a caller-supplied name (e.g. queue-task ``metadata["database_name"]``)
was bare-joined into the filesystem path, and ``get_engine`` mkdir'd it on
first connect — arbitrary directory creation / SQLite file write via
``../`` traversal or an absolute path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.adapters.sqlite.engine import get_db_path
from chaoscypher_core.exceptions import ValidationError


def test_valid_name_builds_expected_path(tmp_path: Path) -> None:
    path = get_db_path("my_db-1", data_dir=str(tmp_path))
    assert path == tmp_path / "databases" / "my_db-1" / "app.db"


@pytest.mark.parametrize("name", ["研究ノート", "Bücher", "a" * 64])
def test_names_create_database_accepts_are_accepted(name: str, tmp_path: Path) -> None:
    """The sink mirrors ``DatabaseRepository.create_database``'s unicode-aware rule.

    An ASCII-only check here would brick a legitimately created non-ASCII
    database on every request (the lockout the settings validator warns about).
    """
    path = get_db_path(name, data_dir=str(tmp_path))
    assert path == tmp_path / "databases" / name / "app.db"


@pytest.mark.parametrize(
    "bad_name",
    [
        "../evil",
        "..",
        "/tmp/absolute",
        "nested/child",
        "dot.name",
        "",
        "name with space",
        "a" * 65,
    ],
)
def test_traversal_names_rejected_and_create_nothing(bad_name: str, tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as exc:
        get_db_path(bad_name, data_dir=str(tmp_path / "data"))
    assert exc.value.field == "database_name"
    # Nothing was created anywhere under the tmp sandbox.
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "evil").exists()
