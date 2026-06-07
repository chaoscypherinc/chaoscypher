# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Drift test — frontend DEFAULT_PUBLIC_SETTINGS must agree with backend defaults.

If this test fails, update either:
- packages/interface/src/contexts/publicSettingsContextValue.ts (DEFAULT_PUBLIC_SETTINGS)
- packages/core/src/chaoscypher_core/app_config/__init__.py (Field defaults)
- packages/core/src/chaoscypher_core/settings.py (Field defaults)

so the two agree. Drift is dangerous because the SPA may render the stale
bundled default for up to 5 seconds (React Query staleTime) on app mount.
"""

import json
from pathlib import Path

from chaoscypher_core.app_config import Settings
from chaoscypher_cortex.features.settings_public.service import build_public_settings


_FRONTEND_DEFAULTS_FIXTURE = Path(__file__).parent / "frontend_defaults.json"


def test_backend_defaults_match_frontend_bundled_defaults() -> None:
    backend = build_public_settings(Settings()).model_dump()
    frontend = json.loads(_FRONTEND_DEFAULTS_FIXTURE.read_text())
    diffs = {k: (backend[k], frontend.get(k)) for k in backend if backend[k] != frontend.get(k)}
    assert not diffs, (
        "Drift between backend Pydantic defaults and frontend "
        "DEFAULT_PUBLIC_SETTINGS:\n"
        + "\n".join(f"  {k}: backend={b!r} frontend={f!r}" for k, (b, f) in diffs.items())
    )


def test_fixture_has_all_backend_keys() -> None:
    backend = build_public_settings(Settings()).model_dump()
    frontend = json.loads(_FRONTEND_DEFAULTS_FIXTURE.read_text())
    missing = set(backend.keys()) - set(frontend.keys())
    assert not missing, (
        f"Frontend DEFAULT_PUBLIC_SETTINGS is missing {len(missing)} field(s): {sorted(missing)}"
    )
    extra = set(frontend.keys()) - set(backend.keys())
    assert not extra, (
        f"Frontend DEFAULT_PUBLIC_SETTINGS has {len(extra)} field(s) not in PublicSettings: {sorted(extra)}"
    )
