# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Round-trip tests for `Settings.load_from_yaml`.

The loader explicitly enumerates each top-level field rather than
splatting the parsed dict into the model — that's intentional, since
nested settings groups need their own per-group `XxxSettings(**data)`
construction. Risk of the explicit list: forgetting to add a new
top-level scalar field to the constructor call, in which case the YAML
value is silently ignored and the field reverts to its Pydantic default.

This test guards `setup_completed` against that regression — the
first-run wizard sets it to True, and if the load step drops it the
user gets re-prompted on every container restart even though the YAML
was written correctly.
"""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.app_config import Settings


def test_load_from_yaml_round_trips_setup_completed(tmp_path: Path) -> None:
    """`setup_completed: true` written to YAML must load as True."""
    yaml = tmp_path / "settings.yaml"
    yaml.write_text("setup_completed: true\n")

    settings = Settings.load_from_yaml(yaml)

    assert settings.setup_completed is True


def test_load_from_yaml_setup_completed_default_when_missing(tmp_path: Path) -> None:
    """Absent `setup_completed` in YAML must produce False (the model default)."""
    yaml = tmp_path / "settings.yaml"
    yaml.write_text("dark_mode: false\n")

    settings = Settings.load_from_yaml(yaml)

    assert settings.setup_completed is False


def test_load_from_yaml_round_trips_other_top_level_scalars(tmp_path: Path) -> None:
    """Sanity check that the existing top-level fields still round-trip.

    If any of these regress, the loader's hand-rolled field list is out of
    sync with the `Settings` model — the same class of bug that masked
    `setup_completed`.
    """
    yaml = tmp_path / "settings.yaml"
    yaml.write_text(
        """
app_name: My Cypher
current_database: prod
dark_mode: false
auto_enable: false
setup_completed: true
""".strip()
        + "\n",
    )

    settings = Settings.load_from_yaml(yaml)

    assert settings.app_name == "My Cypher"
    assert settings.current_database == "prod"
    assert settings.dark_mode is False
    assert settings.auto_enable is False
    assert settings.setup_completed is True
