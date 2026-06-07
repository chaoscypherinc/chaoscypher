# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared helper for loading user plugin files safely.

Every registry that ingests files from ``{data_dir}/plugins/`` MUST route
its user-space scan through :func:`load_user_python_plugin` (for Python
plugins) or :func:`audit_log_user_plugin_file` (for pure-data plugins
such as domain ``.jsonld`` files). This central point is where:

1. The ``CHAOSCYPHER_ALLOW_USER_PLUGINS`` kill switch is honored.
2. Each file's absolute path and SHA-256 content digest are emitted at
   ``WARNING`` level under the structured event ``user_plugin_loaded``.

See ``TRUST_BOUNDARY.md`` in this package for the threat model.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from typing import TYPE_CHECKING

import structlog


if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


logger = structlog.get_logger(__name__)


def user_plugins_allowed() -> bool:
    """Return ``False`` when the kill switch env var is set to ``"0"``.

    Default is ``True`` for backwards compatibility with existing
    deployments. Only the exact string ``"0"`` disables discovery;
    every other value (including empty) enables it so that common
    mis-settings don't accidentally turn off the plugin system.

    Returns:
        True if user plugins may be loaded, False if disabled.
    """
    return os.environ.get("CHAOSCYPHER_ALLOW_USER_PLUGINS", "1") != "0"


def audit_log_user_plugin_file(path: Path, *, registry: str) -> None:
    """Emit a WARNING-level audit log for a user plugin file.

    Call this for every user-space file a registry ingests, including
    pure-data files like ``*.jsonld`` configs. The event name is
    ``user_plugin_loaded`` with fields ``path`` (absolute) and
    ``sha256`` (hex digest of the file contents).

    Args:
        path: Path to the file being loaded.
        registry: Name of the registry loading the file (for grep-ability).
    """
    resolved = path.resolve()
    try:
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError as exc:
        logger.warning(
            "user_plugin_audit_hash_failed",
            path=str(resolved),
            registry=registry,
            error=str(exc),
        )
        digest = ""

    logger.warning(
        "user_plugin_loaded",
        path=str(resolved),
        sha256=digest,
        registry=registry,
    )


def load_user_python_plugin(path: Path, *, module_name: str, registry: str) -> ModuleType | None:
    """Audit-log, gate on kill switch, and execute a user plugin file.

    This is the SINGLE entry point used by every registry for
    ``{data_dir}/plugins/**/*.py`` files. Do not call into
    ``importlib.util`` from any registry directly -- route through this
    function so the kill switch and audit log are always in effect.

    Args:
        path: Absolute path to the user plugin .py file.
        module_name: Unique module name to register the loaded module
            under (e.g., ``"user_loader_excel"``). Caller is responsible
            for namespacing so different files don't collide.
        registry: Name of the registry loading the plugin (for audit
            logs).

    Returns:
        The loaded module, or ``None`` when user plugins are disabled
        or the spec could not be built. On unexpected import errors the
        exception propagates to the caller so the registry can log with
        its own event name.
    """
    if not user_plugins_allowed():
        logger.info(
            "user_plugin_disabled_skip",
            path=str(path),
            registry=registry,
        )
        return None

    audit_log_user_plugin_file(path, registry=registry)

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        logger.warning(
            "user_plugin_spec_failed",
            path=str(path),
            registry=registry,
        )
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


__all__ = [
    "audit_log_user_plugin_file",
    "load_user_python_plugin",
    "user_plugins_allowed",
]
