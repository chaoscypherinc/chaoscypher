# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for worker configuration loading.

Covers default configuration, user YAML overrides, numeric type validation,
value clamping, and graceful handling of missing config files.
"""

from unittest.mock import MagicMock, patch

import pytest
import yaml

from chaoscypher_neuron.config import load_worker_config


# ============================================================================
# Helpers
# ============================================================================


def _patch_defaults(defaults):
    """Patch _get_defaults to return the given defaults dict."""
    return patch("chaoscypher_neuron.config._get_defaults", return_value=defaults)


def _make_defaults():
    """Return a standard defaults dict for testing."""
    return {
        "llm_worker": {
            "max_concurrent": 1,
            "queue_name": "llm",
            "timeout": 3600,
            "max_tries": 5,
        },
        "operations_worker": {
            "max_concurrent": 8,
            "queue_name": "operations",
            "timeout": 3600,
            "max_tries": 5,
        },
    }


def _mock_path_settings(data_dir: str):
    """Create a mock PathSettings that points to the given data directory."""
    mock_ps = MagicMock()
    mock_ps.data_dir = data_dir
    mock_ps.workers_config_filename = "workers.yaml"
    return mock_ps


def _load_config_with_yaml(tmp_path, worker_type, yaml_content=None):
    """Load worker config with an optional YAML override file.

    Creates a workers.yaml in tmp_path if yaml_content is provided.
    Patches _get_defaults and PathSettings so the function reads from tmp_path.
    """
    defaults = _make_defaults()

    if yaml_content is not None:
        config_file = tmp_path / "workers.yaml"
        config_file.write_text(yaml_content)

    mock_ps = _mock_path_settings(str(tmp_path))

    with (
        _patch_defaults(defaults),
        patch("chaoscypher_core.app_config.PathSettings", return_value=mock_ps),
    ):
        return load_worker_config(worker_type)


# ============================================================================
# Default Configuration
# ============================================================================


class TestDefaultConfiguration:
    """Tests that load_worker_config returns correct defaults for each queue."""

    def test_llm_worker_defaults(self, tmp_path) -> None:
        """LLM worker returns correct default values."""
        config = _load_config_with_yaml(tmp_path, "llm_worker")

        assert config["queue_name"] == "llm"
        assert config["max_concurrent"] == 1
        assert config["timeout"] == 3600
        assert config["max_tries"] == 5

    def test_operations_worker_defaults(self, tmp_path) -> None:
        """Operations worker returns correct default values."""
        config = _load_config_with_yaml(tmp_path, "operations_worker")

        assert config["queue_name"] == "operations"
        assert config["max_concurrent"] == 8
        assert config["timeout"] == 3600
        assert config["max_tries"] == 5

    def test_unknown_worker_type_raises_value_error(self) -> None:
        """Requesting an unknown worker type raises ValueError."""
        defaults = _make_defaults()
        with _patch_defaults(defaults), pytest.raises(ValueError, match="Unknown worker type"):
            load_worker_config("unknown_worker")


# ============================================================================
# User YAML Overrides
# ============================================================================


class TestUserYamlOverrides:
    """Tests that user YAML overrides are applied correctly."""

    def test_user_overrides_applied(self, tmp_path) -> None:
        """User config values override defaults."""
        yaml_content = yaml.dump({"llm_worker": {"max_concurrent": 4, "timeout": 1200}})
        config = _load_config_with_yaml(tmp_path, "llm_worker", yaml_content)

        assert config["max_concurrent"] == 4
        assert config["timeout"] == 1200
        # max_tries not overridden, should keep default
        assert config["max_tries"] == 5

    def test_only_allowed_keys_applied(self, tmp_path) -> None:
        """Unknown keys in user config are ignored."""
        yaml_content = yaml.dump(
            {
                "operations_worker": {
                    "max_concurrent": 16,
                    "bogus_key": 999,
                    "queue_name": "hacked",
                }
            }
        )
        config = _load_config_with_yaml(tmp_path, "operations_worker", yaml_content)

        assert config["max_concurrent"] == 16
        # queue_name is not in allowed_keys, so it should remain the default
        assert config["queue_name"] == "operations"

    def test_other_worker_section_ignored(self, tmp_path) -> None:
        """YAML containing only the other worker's section does not affect this one."""
        yaml_content = yaml.dump({"operations_worker": {"max_concurrent": 32}})
        config = _load_config_with_yaml(tmp_path, "llm_worker", yaml_content)

        # LLM worker should be unaffected by operations_worker section
        assert config["max_concurrent"] == 1


# ============================================================================
# Numeric Type Validation
# ============================================================================


class TestNumericTypeValidation:
    """Tests that boolean and string values are rejected for numeric fields."""

    def test_boolean_max_concurrent_rejected(self, tmp_path) -> None:
        """Boolean True for max_concurrent is stripped and falls back to clamped default."""
        # YAML true parses to Python True (bool), which is an int subclass.
        # Write raw YAML to ensure boolean type is preserved.
        yaml_content = "llm_worker:\n  max_concurrent: true\n"
        config = _load_config_with_yaml(tmp_path, "llm_worker", yaml_content)

        # Boolean was rejected, clamping uses fallback default of 1
        assert config["max_concurrent"] == 1
        assert isinstance(config["max_concurrent"], int)
        assert not isinstance(config["max_concurrent"], bool)

    def test_string_timeout_rejected(self, tmp_path) -> None:
        """String value for timeout is stripped and falls back to clamped default."""
        yaml_content = 'llm_worker:\n  timeout: "not_a_number"\n'
        config = _load_config_with_yaml(tmp_path, "llm_worker", yaml_content)

        # String was rejected, clamping uses fallback of 3600
        assert config["timeout"] == 3600

    def test_boolean_false_max_tries_rejected(self, tmp_path) -> None:
        """Boolean False for max_tries is stripped and falls back to clamped default."""
        yaml_content = "operations_worker:\n  max_tries: false\n"
        config = _load_config_with_yaml(tmp_path, "operations_worker", yaml_content)

        # Boolean was rejected, clamping uses fallback of 5
        assert config["max_tries"] == 5


# ============================================================================
# Value Clamping
# ============================================================================


class TestValueClamping:
    """Tests that numeric values are clamped to safe ranges."""

    def test_max_concurrent_clamped_to_minimum(self, tmp_path) -> None:
        """max_concurrent below 1 is clamped to 1."""
        yaml_content = yaml.dump({"llm_worker": {"max_concurrent": 0}})
        config = _load_config_with_yaml(tmp_path, "llm_worker", yaml_content)

        assert config["max_concurrent"] == 1

    def test_max_concurrent_clamped_to_maximum(self, tmp_path) -> None:
        """max_concurrent above 64 is clamped to 64."""
        yaml_content = yaml.dump({"operations_worker": {"max_concurrent": 999}})
        config = _load_config_with_yaml(tmp_path, "operations_worker", yaml_content)

        assert config["max_concurrent"] == 64

    def test_timeout_clamped_to_minimum(self, tmp_path) -> None:
        """Timeout below 60 is clamped to 60."""
        yaml_content = yaml.dump({"llm_worker": {"timeout": 10}})
        config = _load_config_with_yaml(tmp_path, "llm_worker", yaml_content)

        assert config["timeout"] == 60

    def test_timeout_clamped_to_maximum(self, tmp_path) -> None:
        """Timeout above 86400 is clamped to 86400."""
        yaml_content = yaml.dump({"llm_worker": {"timeout": 999999}})
        config = _load_config_with_yaml(tmp_path, "llm_worker", yaml_content)

        assert config["timeout"] == 86400

    def test_max_tries_clamped_to_minimum(self, tmp_path) -> None:
        """max_tries below 1 is clamped to 1."""
        yaml_content = yaml.dump({"llm_worker": {"max_tries": -5}})
        config = _load_config_with_yaml(tmp_path, "llm_worker", yaml_content)

        assert config["max_tries"] == 1

    def test_max_tries_clamped_to_maximum(self, tmp_path) -> None:
        """max_tries above 20 is clamped to 20."""
        yaml_content = yaml.dump({"llm_worker": {"max_tries": 100}})
        config = _load_config_with_yaml(tmp_path, "llm_worker", yaml_content)

        assert config["max_tries"] == 20

    def test_valid_values_not_clamped(self, tmp_path) -> None:
        """Values within valid ranges are not changed."""
        yaml_content = yaml.dump(
            {"llm_worker": {"max_concurrent": 4, "timeout": 7200, "max_tries": 10}}
        )
        config = _load_config_with_yaml(tmp_path, "llm_worker", yaml_content)

        assert config["max_concurrent"] == 4
        assert config["timeout"] == 7200
        assert config["max_tries"] == 10


# ============================================================================
# Missing / Invalid Config File
# ============================================================================


class TestMissingConfigFile:
    """Tests that missing workers.yaml is handled gracefully."""

    def test_missing_yaml_uses_defaults(self, tmp_path) -> None:
        """When workers.yaml does not exist, defaults are used without error."""
        # No yaml_content means no file is written
        config = _load_config_with_yaml(tmp_path, "llm_worker")

        assert config["max_concurrent"] == 1
        assert config["queue_name"] == "llm"
        assert config["timeout"] == 3600
        assert config["max_tries"] == 5

    def test_corrupt_yaml_falls_back_to_defaults(self, tmp_path) -> None:
        """When workers.yaml cannot be read, defaults are used."""
        defaults = _make_defaults()
        mock_ps = _mock_path_settings(str(tmp_path))

        # Create a file that will exist but fail to open
        config_file = tmp_path / "workers.yaml"
        config_file.write_text("valid: yaml")  # Create the file so .exists() returns True

        with (
            _patch_defaults(defaults),
            patch("chaoscypher_core.app_config.PathSettings", return_value=mock_ps),
            patch("builtins.open", side_effect=OSError("permission denied")),
        ):
            config = load_worker_config("llm_worker")

        # Should still return valid defaults
        assert config["max_concurrent"] == 1
        assert config["timeout"] == 3600

    def test_empty_yaml_uses_defaults(self, tmp_path) -> None:
        """When workers.yaml is empty, defaults are used."""
        config = _load_config_with_yaml(tmp_path, "llm_worker", "")

        assert config["max_concurrent"] == 1
        assert config["timeout"] == 3600
        assert config["max_tries"] == 5

    def test_pathsettings_import_failure_uses_fallback_path(self) -> None:
        """When PathSettings import fails, falls back to /data/workers.yaml."""
        defaults = _make_defaults()

        with (
            _patch_defaults(defaults),
            patch(
                "chaoscypher_core.app_config.PathSettings",
                side_effect=ImportError("no module"),
            ),
            patch("chaoscypher_neuron.config.Path") as mock_path_cls,
        ):
            # The fallback path /data/workers.yaml should not exist
            mock_fallback_path = MagicMock()
            mock_fallback_path.exists.return_value = False

            def path_side_effect(arg):
                return mock_fallback_path

            mock_path_cls.side_effect = path_side_effect

            config = load_worker_config("llm_worker")

        assert config["max_concurrent"] == 1
        assert config["queue_name"] == "llm"
