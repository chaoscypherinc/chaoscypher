# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cheap, best-effort access to data_dir/settings.yaml for CLI startup paths.

The first-run gate and upgrade guard run on every CLI invocation, so they
must not pay the Dynaconf + full-Pydantic validation cost of
``chaoscypher_core.app_config.get_settings()``. This module does a raw
``yaml.safe_load`` peek instead. Engine construction and commands that need
validated settings use ``app_config.get_settings()`` directly.

settings.yaml is the single persisted home for engine-level config (llm,
embedding, current database) as of the 2026-06 config unification: the CLI
wizard writes it, the CLI engine reads it through the shared app_config
pipeline, and cli.yaml is reduced to client-only concerns.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def data_dir() -> Path:
    """Resolve the data directory exactly like core ``PathSettings`` does.

    Deliberately duplicates the two-line env/platformdirs resolution rather
    than importing core's PathSettings model — this module must stay cheap
    enough for the per-invocation startup path. This is the CLI's single
    path-resolution authority: CLIContext and the db commands must agree
    on where databases live. cli.yaml's ``paths`` section was never honored
    by CLIContext, so honoring it anywhere else creates a split-brain (a
    divergent value there once made ``db create`` and ``db list`` disagree
    about the databases directory).
    """
    import platformdirs

    return Path(
        os.getenv(
            "CHAOSCYPHER_DATA_DIR",
            platformdirs.user_data_dir("chaoscypher", appauthor=False),
        )
    )


def settings_yaml_path() -> Path:
    """Resolve data_dir/settings.yaml exactly like core ``PathSettings`` does."""
    return data_dir() / "settings.yaml"


def peek_settings_yaml() -> dict[str, Any]:
    """Best-effort raw parse of settings.yaml — ``{}`` on any error.

    Reads only the lower-case keys ConfigManager writes; this is a UX
    helper (gating, default resolution), not a correctness path.
    """
    try:
        import yaml

        raw = yaml.safe_load(settings_yaml_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def is_setup_completed() -> bool:
    """True if the engine has been configured (CLI wizard, web wizard, or env).

    Satisfied by a truthy top-level ``setup_completed``, an explicit
    ``llm.chat_provider`` key in settings.yaml, or the
    ``CHAOSCYPHER_LLM_PROVIDER`` env var (env-only deployments have no
    file to inspect).
    """
    if os.getenv("CHAOSCYPHER_LLM_PROVIDER"):
        return True
    data = peek_settings_yaml()
    if data.get("setup_completed"):
        return True
    llm = data.get("llm")
    return isinstance(llm, dict) and bool(llm.get("chat_provider"))


def read_current_database() -> str | None:
    """Current database from settings.yaml, or None when unset."""
    value = peek_settings_yaml().get("current_database")
    return str(value) if value else None


__all__ = [
    "data_dir",
    "is_setup_completed",
    "peek_settings_yaml",
    "read_current_database",
    "settings_yaml_path",
]
