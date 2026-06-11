# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Path Utilities - Cross-platform path management.

Provides consistent paths for configuration and package directories across
different platforms using platformdirs (XDG-compliant). Data-dir resolution
delegates to ``chaoscypher_cli.engine_config.data_dir`` so the CLI and engine
always agree on where data lives.

Example:
    from chaoscypher_cli.utils.paths import get_config_dir, get_packages_dir

    config_dir = get_config_dir()
    packages_dir = get_packages_dir()
"""

import os
from pathlib import Path

from platformdirs import user_config_dir

from chaoscypher_cli.engine_config import data_dir


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


def get_packages_dir() -> Path:
    r"""Get the directory for installed/cached packages (created if missing).

    Single canonical authority for the packages directory: the data dir is
    resolved exactly like ``engine_config.data_dir()`` does (``CHAOSCYPHER_DATA_DIR``
    env override, else ``platformdirs.user_data_dir("chaoscypher", appauthor=False)``)
    with ``packages`` appended.

    This exists to fix a split-brain where ``lexicon list`` and ``lexicon
    remove`` scanned different directories on Windows: ``lexicon list``
    hardcoded a ``~/.local/share/chaoscypher`` fallback, while this module's
    old implementation called platformdirs without ``appauthor=False`` and so
    resolved a doubled ``...\AppData\Local\chaoscypher\chaoscypher`` path —
    a pulled package could be listed but not removed (or vice versa). All
    packages-dir consumers must call this function instead of resolving paths
    themselves.
    """
    packages_dir = data_dir() / "packages"
    packages_dir.mkdir(parents=True, exist_ok=True)
    return packages_dir


__all__ = [
    "get_config_dir",
    "get_packages_dir",
]
