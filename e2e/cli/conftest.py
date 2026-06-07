# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CLI E2E test fixtures.

Provides a temp data directory and Click CliRunner for testing
CLI commands against real (but temporary) databases.

Note: ChaosCypher CLI uses LazyGroup which checks sys.argv to decide
whether to load real commands vs stubs. We patch sys.argv in the
invoke helper to make LazyGroup load the real commands.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner


def pytest_collection_modifyitems(config, items):
    """Auto-apply cli marker to all tests in this directory (collection time)."""
    for item in items:
        if "/cli/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.cli)


@pytest.fixture(autouse=True)
def reset_cli_singletons() -> None:
    """Reset process-wide CLI / settings singletons between tests.

    Without this, the cached ``_context_instance`` in
    ``chaoscypher_cli.context`` and the ``lru_cache`` on
    ``get_settings`` / ``get_config_manager`` keep pointing at the
    first test's ``tmp_path``. Subsequent tests then operate on a
    stale Engine + DB and surface as either silent-stale-reads or
    UNIQUE / FK violations when fixture data overlaps.
    """
    from chaoscypher_cli.context import reset_context
    from chaoscypher_core import app_config

    reset_context()
    app_config.get_settings.cache_clear()
    app_config.get_config_manager.cache_clear()
    # cache_clear alone is not enough: get_settings is also backed by the
    # module-global ``_settings``, which would survive the lru reset and
    # keep serving the first test's data_dir.
    app_config._settings = None


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a Click CLI runner."""
    return CliRunner()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory for CLI tests."""
    data = tmp_path / "data"
    data.mkdir()
    return data


@pytest.fixture
def cli_env(data_dir: Path) -> dict[str, str]:
    """Environment variables for CLI commands pointing to temp data dir.

    A minimal settings.yaml marks the install as configured so the
    first-run setup gate stays out of the way of non-interactive tests.
    (Engine config lives in data_dir/settings.yaml since the 2026-06
    config unification; previously these tests passed only because the
    gate saw the developer's real cli.yaml outside the temp dir.)
    """
    (data_dir / "settings.yaml").write_text("setup_completed: true\n", encoding="utf-8")
    config_dir = data_dir.parent / "config"
    config_dir.mkdir(exist_ok=True)
    return {
        "CHAOSCYPHER_DATA_DIR": str(data_dir),
        # Hermetic config dir: without this, tests read the dev machine's
        # real cli.yaml (and historically one wrote it — test debris there
        # masked real failures for months).
        "CHAOSCYPHER_CONFIG_DIR": str(config_dir),
        "LOG_LEVEL": "WARNING",
    }


def invoke_cli(runner: CliRunner, args: list[str], **kwargs):
    """Invoke CLI with sys.argv patched for LazyGroup compatibility.

    LazyGroup checks sys.argv to decide whether to load real commands
    vs stubs. CliRunner doesn't set sys.argv, so we patch it.
    """
    from chaoscypher_cli.__main__ import main

    fake_argv = ["chaoscypher", *args]
    with patch.object(sys, "argv", fake_argv):
        return runner.invoke(main, args, **kwargs)


@pytest.fixture
def run_cli(cli_runner: CliRunner):
    """Fixture that returns invoke_cli bound to the runner."""

    def _run(args: list[str], **kwargs):
        return invoke_cli(cli_runner, args, **kwargs)

    return _run


@pytest.fixture
def sample_txt(sample_data_dir: str) -> Path:
    """Path to sample.txt test file."""
    return Path(sample_data_dir) / "sample.txt"


@pytest.fixture
def sample_pdf(sample_data_dir: str) -> Path:
    """Path to sample.pdf test file."""
    return Path(sample_data_dir) / "sample.pdf"


@pytest.fixture
def seed_ccx(e2e_fixtures_dir: str) -> Path:
    """Path to seed.ccx fixture file."""
    return Path(e2e_fixtures_dir) / "seed.ccx"
