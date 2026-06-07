# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ConfigManager: Handles reading and writing application settings.

Uses dynaconf-powered Settings with YAML persistence.
"""

import contextlib
import os
import shutil
from pathlib import Path
from typing import Any

import structlog
import yaml

from chaoscypher_core.app_config import Settings


logger = structlog.get_logger(__name__)


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge overrides into base, returning a new dict.

    Nested dicts are merged key-by-key so that missing keys in overrides
    are preserved from base (e.g. a stripped secret field stays intact).
    Non-dict values in overrides replace base values outright.
    """
    merged = dict(base)
    for key, value in overrides.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class ConfigManager:
    """Manages application configuration with YAML persistence."""

    def __init__(
        self,
        settings_path: str | None = None,
        default_settings_path: str | None = None,
    ):
        """Initialize config manager.

        Args:
            settings_path: Path to user settings file (defaults to PathSettings.data_dir / PathSettings.settings_filename)
            default_settings_path: Path to default settings template (defaults to PathSettings.default_settings_path)

        """
        from chaoscypher_core.app_config import PathSettings

        # Use centralized PathSettings for defaults
        path_defaults = PathSettings()

        # Use provided paths or fall back to centralized settings
        self.settings_path = (
            settings_path or f"{path_defaults.data_dir}/{path_defaults.settings_filename}"
        )
        self.default_settings_path = default_settings_path or path_defaults.default_settings_path
        self._settings: Settings | None = None

        # Ensure data directory exists
        os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)

        # Initialize settings file if it doesn't exist
        if not os.path.exists(self.settings_path):
            self._create_default_settings()

        # Load settings
        self.load_settings()

    def _create_default_settings(self) -> None:
        """Copy default settings to user settings location."""
        try:
            if os.path.exists(self.default_settings_path):
                shutil.copy2(self.default_settings_path, self.settings_path)
                logger.info(
                    "settings_file_created_from_default",
                    settings_path=self.settings_path,
                )
            else:
                # Create minimal settings file. All values default from code
                # so users only need to add specific overrides.
                self._write_minimal_settings_file()
                logger.info("minimal_settings_file_created", settings_path=self.settings_path)

        except Exception as e:
            logger.exception(
                "default_settings_creation_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

    def _write_minimal_settings_file(self) -> None:
        """Write a minimal settings file with only a comment header.

        All configuration values default from code (Pydantic models), so new
        installs start with an empty file.  Users add only the specific keys
        they want to override.  This prevents stale YAML values from silently
        hiding improved code defaults after upgrades.
        """
        header = (
            "# Chaos Cypher Settings\n"
            "#\n"
            "# All values default from code. Add only the keys you want to\n"
            "# override.  Example:\n"
            "#\n"
            "#   llm:\n"
            "#     chat_provider: openai\n"
            "#     openai_chat_model: gpt-4o\n"
            "#\n"
            "# See documentation for all available settings.\n"
            "custom_settings: {}\n"
        )
        with open(self.settings_path, "w") as f:
            f.write(header)

    def _write_settings_to_file(self, settings_dict: dict[str, Any]) -> None:
        """Write settings dictionary to YAML file atomically (tmp + replace).

        The file can hold plaintext provider API keys, so it is written
        owner-only (0600 — effectively a no-op on Windows). ``os.replace``
        is atomic on POSIX and Windows, so concurrent readers (Cortex on
        the same data_dir, a second CLI invocation) never observe a torn
        file.
        """
        settings_path = Path(self.settings_path)
        tmp_path = settings_path.with_name(f"{settings_path.name}.tmp")
        try:
            with tmp_path.open("w") as f:
                yaml.safe_dump(settings_dict, f, default_flow_style=False, sort_keys=False)
            tmp_path.chmod(0o600)
            tmp_path.replace(settings_path)
            logger.debug("settings_written_to_file", settings_path=self.settings_path)

        except Exception as e:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            logger.exception(
                "settings_write_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

    def load_settings(self) -> Settings:
        """Load settings from file using dynaconf-powered Settings."""
        try:
            # Use the new Settings.load_from_yaml() method
            self._settings = Settings.load_from_yaml(self.settings_path)
            logger.debug("settings_loaded_successfully")
            return self._settings

        except FileNotFoundError:
            logger.exception("settings_file_not_found", settings_path=self.settings_path)
            raise
        except Exception as e:
            logger.exception(
                "settings_load_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

    def invalidate_cache(self) -> None:
        """Invalidate the cached settings, forcing a reload on next access."""
        self._settings = None

    def get_settings(self) -> Settings:
        """Get current settings (cached)."""
        if self._settings is None:
            self._settings = self.load_settings()
        return self._settings

    def update_settings(self, updates: dict[str, Any]) -> Settings:
        """Update settings with new values."""
        try:
            # Get current settings
            current = self.get_settings()

            # Deep-merge updates so that stripped secret fields (removed by
            # strip_masked_values) are preserved from current settings rather
            # than falling back to Pydantic defaults.
            settings_dict = _deep_merge(current.model_dump(), updates)

            # Validate new settings
            new_settings = Settings(**settings_dict)

            # Write to file (mode='json' converts Path objects to strings)
            self._write_settings_to_file(
                new_settings.model_dump(mode="json", exclude_defaults=True)
            )

            # Update both this manager's instance cache AND the module-global
            # singleton + lru_cache so callers reading via the top-level
            # ``get_settings()`` (notably hot-reloading middleware like
            # ``HostHeaderCheckMiddleware``) see the change on the very next
            # request instead of holding stale policy until restart.
            self._settings = new_settings
            from chaoscypher_core.app_config import set_settings

            set_settings(new_settings)

            logger.info("settings_updated_successfully")
            return new_settings

        except Exception as e:
            logger.exception(
                "settings_update_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

    def reset_to_defaults(self) -> Settings:
        """Reset settings to default values."""
        try:
            # Remove current settings
            if os.path.exists(self.settings_path):
                os.remove(self.settings_path)

            # Recreate from defaults
            self._create_default_settings()

            # Reload
            return self.load_settings()

        except Exception as e:
            logger.exception(
                "settings_reset_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise
