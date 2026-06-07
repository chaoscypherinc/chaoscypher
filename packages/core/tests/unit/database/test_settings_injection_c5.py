# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Config-unification Tier 2 / Task C5: settings injected into the database layer.

These tests pin the injection seams that replace the retired core-internal
``chaoscypher_core.settings.get_settings()`` fallback and the app-config
singleton reads in the database layer:

- ``engine.get_db_path`` honours an explicit ``data_dir`` (and siblings)
  rather than reading a settings singleton, and defaults to the
  env/platformdirs-resolved ``PathSettings`` values.
- ``engine.init_database`` threads ``strict_schema_drift`` /
  ``auto_apply_destructive`` through to its post-migration steps and the
  startup runner.
- ``startup._resolve_auto_apply_destructive`` no longer falls back to the
  core settings singleton — the parameter is authoritative, and ``None``
  resolves from ``MigrationsSettings`` (env-aware), not a global.
- ``get_sqlite_adapter`` accepts an injected ``settings`` object.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.database import engine as engine_mod
from chaoscypher_core.database.migrations import startup as startup_mod


# ---------------------------------------------------------------------------
# get_db_path: explicit data_dir honoured; default resolves via PathSettings
# ---------------------------------------------------------------------------


def test_get_db_path_honours_explicit_data_dir() -> None:
    path = engine_mod.get_db_path("mydb", data_dir="/custom/root")
    assert path == Path("/custom/root") / "databases" / "mydb" / "app.db"


def test_get_db_path_honours_subdir_and_filename_overrides() -> None:
    path = engine_mod.get_db_path(
        "mydb",
        data_dir="/custom/root",
        databases_subdir="dbs",
        app_db_filename="store.db",
    )
    assert path == Path("/custom/root") / "dbs" / "mydb" / "store.db"


def test_get_db_path_default_resolves_via_path_settings_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No app-config singleton: the default data_dir comes from PathSettings.

    PathSettings reads CHAOSCYPHER_DATA_DIR in its default factory.
    """
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))
    path = engine_mod.get_db_path("mydb")
    assert path == tmp_path.resolve() / "databases" / "mydb" / "app.db"


# ---------------------------------------------------------------------------
# init_database: strict_schema_drift threaded to the drift gate
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))
    return tmp_path


def test_init_database_threads_strict_schema_drift_param(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The explicit ``strict_schema_drift`` argument reaches check_schema_drift.

    Every post-migration step except the drift gate is stubbed (mirroring the
    auto-apply test below): seeding resolves its adapter through the
    process-wide app-config singleton, so letting it run for real makes the
    test order-dependent under xdist — a colocated test that already
    populated the singleton points seeding at a different (unmigrated) tmp
    DB and the seed query crashes. The assertion only needs the drift spy.
    """
    from chaoscypher_core.database.migrations import drift as drift_mod
    from chaoscypher_core.database.migrations import state as state_mod

    seen: dict[str, object] = {}

    def _spy(db_path: Path, *, strict: bool) -> None:
        seen["strict"] = strict

    monkeypatch.setattr(drift_mod, "check_schema_drift", _spy)
    monkeypatch.setattr(startup_mod, "run_startup_migrations", lambda *a, **k: None)
    monkeypatch.setattr(
        state_mod, "get_upgrade_state", lambda _p: state_mod.UpgradeState(ready=True)
    )
    monkeypatch.setattr(engine_mod, "seed_default_data", lambda *_a, **_k: None)
    monkeypatch.setattr(engine_mod, "engine_apply_schema_updates", lambda *_a, **_k: None)

    engine_mod.init_database("default", strict_schema_drift=False)

    assert seen.get("strict") is False


def test_init_database_threads_auto_apply_destructive_param(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The explicit ``auto_apply_destructive`` argument reaches the startup runner."""
    seen: dict[str, object] = {}

    def _spy(db_path: Path, *, auto_apply_destructive: bool | None = None) -> None:
        seen["aad"] = auto_apply_destructive

    monkeypatch.setattr(startup_mod, "run_startup_migrations", _spy)
    # Drift gate would run on a never-migrated DB; stub it out.
    from chaoscypher_core.database.migrations import drift as drift_mod
    from chaoscypher_core.database.migrations import state as state_mod

    monkeypatch.setattr(drift_mod, "check_schema_drift", lambda *a, **k: None)
    # Force the "ready" state so post-steps proceed past the gate.
    monkeypatch.setattr(
        state_mod, "get_upgrade_state", lambda _p: state_mod.UpgradeState(ready=True)
    )
    monkeypatch.setattr(engine_mod, "seed_default_data", lambda *_a, **_k: None)
    monkeypatch.setattr(engine_mod, "engine_apply_schema_updates", lambda *_a, **_k: None)

    engine_mod.init_database("default", auto_apply_destructive=False)

    assert seen.get("aad") is False


# ---------------------------------------------------------------------------
# _resolve_auto_apply_destructive: no core-singleton fallback
# ---------------------------------------------------------------------------


def test_resolve_auto_apply_destructive_param_authoritative() -> None:
    assert startup_mod._resolve_auto_apply_destructive(True) is True
    assert startup_mod._resolve_auto_apply_destructive(False) is False


def test_resolve_auto_apply_destructive_none_uses_migrations_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None must resolve from MigrationsSettings (env-aware), not a singleton.

    The retired core ``chaoscypher_core.settings.get_settings`` no longer
    exists; importing it inside the resolver would raise ImportError. This
    asserts the env-driven default path is taken instead.
    """
    monkeypatch.setenv("CHAOSCYPHER_AUTO_APPLY_DESTRUCTIVE", "false")
    assert startup_mod._resolve_auto_apply_destructive(None) is False
    monkeypatch.setenv("CHAOSCYPHER_AUTO_APPLY_DESTRUCTIVE", "true")
    assert startup_mod._resolve_auto_apply_destructive(None) is True


def test_core_settings_get_settings_is_deleted() -> None:
    """The core-internal singleton fallback is fully retired."""
    import chaoscypher_core.settings as core_settings

    assert not hasattr(core_settings, "get_settings")


# ---------------------------------------------------------------------------
# get_sqlite_adapter: injected settings honoured
# ---------------------------------------------------------------------------


def test_get_sqlite_adapter_honours_injected_settings(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from chaoscypher_core.database import adapter_factory

    injected = SimpleNamespace(
        current_database="injected_current",
        paths=SimpleNamespace(
            data_dir=str(tmp_path),
            databases_subdir="databases",
            app_db_filename="app.db",
        ),
    )

    adapter = adapter_factory.get_sqlite_adapter(database_name="other", settings=injected)
    try:
        assert adapter.db_path == tmp_path / "databases" / "other" / "app.db"
    finally:
        adapter.disconnect()
