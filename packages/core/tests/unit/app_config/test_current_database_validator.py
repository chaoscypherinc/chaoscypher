# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings.current_database format validation.

The field is writable through PATCH /api/v1/settings and joined as a raw
path segment by database/backup/MCP path helpers (``database_dir``,
``app_db_path``, ``graphs_dir``), so the model itself must refuse any
value ``DatabaseRepository.create_database`` could not have created.
The rule mirrors creation semantics exactly (unicode-aware alnum after
stripping ``_``/``-``, max 64 chars) so legitimately created databases
keep loading.
"""

import pydantic
import pytest
from pydantic import ValidationError

from chaoscypher_core.app_config import Settings


@pytest.mark.parametrize(
    "bad_name",
    ["../../../tmp/pwn", "a/b", "", ".", "..", "a" * 65, "a\\b", "x\x00y"],
)
def test_traversal_and_malformed_names_rejected(bad_name: str) -> None:
    with pytest.raises(ValidationError):
        Settings(current_database=bad_name)


@pytest.mark.parametrize("good_name", ["default", "my-db_2", "research-2026", "test_db"])
def test_creatable_names_accepted(good_name: str) -> None:
    assert Settings(current_database=good_name).current_database == good_name


def test_config_manager_update_rejects_traversal_without_writing(tmp_path) -> None:
    """update_settings validates the merged Settings before persisting."""
    from chaoscypher_core.app_config import set_settings
    from chaoscypher_core.app_config.manager import ConfigManager

    settings_path = tmp_path / "settings.yaml"
    manager = ConfigManager(settings_path=str(settings_path))
    try:
        with pytest.raises(pydantic.ValidationError):
            manager.update_settings({"current_database": "../x"})
        content = settings_path.read_text() if settings_path.exists() else ""
        assert "../x" not in content
    finally:
        set_settings(Settings())
