# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Path Utilities - Cross-platform path management.

Provides consistent paths for configuration, cache, and package directories
across different platforms using platformdirs (XDG-compliant).

Example:
    from chaoscypher_cli.utils.paths import get_config_dir, get_cache_dir

    config_dir = get_config_dir()
    cache_dir = get_cache_dir()
    packages_dir = get_packages_dir()
"""

import os
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_data_dir


APP_NAME = "chaoscypher"


def get_config_dir() -> Path:
    """Get the Chaos Cypher config directory.

    Honors ``CHAOSCYPHER_CONFIG_DIR`` (matching core ``PathSettings.config_dir``)
    so the CLI and engine agree on where config-dir files (auth tokens, the
    retired cli.yaml) live; falls back to the XDG-compliant platform default:
    - Linux: ~/.config/chaoscypher
    - macOS: ~/Library/Application Support/chaoscypher
    - Windows: %APPDATA%/chaoscypher
    """
    config_dir = Path(os.getenv("CHAOSCYPHER_CONFIG_DIR", user_config_dir(APP_NAME)))
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_cache_dir() -> Path:
    """Get the Chaos Cypher cache directory.

    Returns XDG-compliant cache directory:
    - Linux: ~/.cache/chaoscypher
    - macOS: ~/Library/Caches/chaoscypher
    - Windows: %LOCALAPPDATA%/chaoscypher/Cache
    """
    cache_dir = Path(user_cache_dir(APP_NAME))
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_data_dir() -> Path:
    """Get the Chaos Cypher data directory.

    Returns XDG-compliant data directory:
    - Linux: ~/.local/share/chaoscypher
    - macOS: ~/Library/Application Support/chaoscypher
    - Windows: %LOCALAPPDATA%/chaoscypher
    """
    data_dir = Path(user_data_dir(APP_NAME))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_packages_dir() -> Path:
    """Get the directory for installed packages.

    Returns the packages directory within the data directory.
    """
    packages_dir = get_data_dir() / "packages"
    packages_dir.mkdir(parents=True, exist_ok=True)
    return packages_dir


def get_databases_dir() -> Path:
    """Get the directory for databases.

    Returns the databases directory within the data directory.
    """
    databases_dir = get_data_dir() / "databases"
    databases_dir.mkdir(parents=True, exist_ok=True)
    return databases_dir


def get_package_cache_dir(package_name: str, version: str | None = None) -> Path:
    """Get the cache directory for a specific package.

    Args:
        package_name: Package identifier (e.g., "john/medical-ontology")
        version: Optional version string (e.g., "1.0.0")

    Returns:
        Path to the package's cache directory
    """
    cache_dir = get_cache_dir() / "packages"

    # Normalize package name to path
    if "/" in package_name:
        parts = package_name.split("/", 1)
        package_path = cache_dir / parts[0] / parts[1]
    else:
        package_path = cache_dir / package_name

    if version:
        package_path = package_path / version

    package_path.mkdir(parents=True, exist_ok=True)
    return package_path


__all__ = [
    "get_cache_dir",
    "get_config_dir",
    "get_data_dir",
    "get_databases_dir",
    "get_package_cache_dir",
    "get_packages_dir",
]
