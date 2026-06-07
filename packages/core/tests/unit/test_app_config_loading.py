# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for app_config settings.yaml hydration."""

from chaoscypher_core.app_config import Settings


def test_load_from_yaml_hydrates_release_critical_nested_groups(tmp_path) -> None:
    """Production tuning groups should not silently fall back to defaults."""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(
        """
RATE_LIMIT:
  login_max_requests: 2
QUEUE_RECOVERY:
  heartbeat_ttl_seconds: 20
  heartbeat_refresh_interval_seconds: 5
SOURCE_RECOVERY:
  stalled_threshold_seconds: 60
INTERVALS:
  search_sweep_seconds: 60
WORKFLOWS:
  max_recursion_depth: 2
""",
        encoding="utf-8",
    )

    settings = Settings.load_from_yaml(settings_file)

    assert settings.rate_limit.login_max_requests == 2
    assert settings.queue_recovery.heartbeat_ttl_seconds == 20
    assert settings.queue_recovery.heartbeat_refresh_interval_seconds == 5
    assert settings.source_recovery.stalled_threshold_seconds == 60
    assert settings.intervals.search_sweep_seconds == 60
    assert settings.workflows.max_recursion_depth == 2
