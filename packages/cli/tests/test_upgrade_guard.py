# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for ``_upgrade_guard`` in ``chaoscypher_cli.__main__``.

The guard exists to give users a clear "go run ``db migrate apply``"
banner instead of a downstream schema-mismatch crash when a database
is sitting on tier-needs-confirmation migrations. It runs as the
parent group callback, which means Click invokes it before any
subcommand body — including before the subcommand's ``--help``
handler. Without an early-out, ``chaoscypher source --help`` and
similar DB-free discovery commands would be unhelpfully blocked
behind a migration the user might not yet understand.

These tests pin both halves of that contract:

* ``--help`` and shell completion bypass the guard, even when the
  upgrade state is reported as Blocked.
* Real (non-help) invocations of DB-touching subcommands still get
  the gate banner and exit code 2 when the state is Blocked.

The guard inspects ``sys.argv`` to spot ``--help`` / ``-h``, because
Click 8.3 has already drained ``ctx.args`` / ``ctx.protected_args``
by the time the parent group callback fires (the subcommand parser
hasn't run yet either, so we can't inspect a child context). Tests
therefore set ``sys.argv`` explicitly to mirror the production
invocation we're simulating.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from click.testing import CliRunner


def _install_blocked_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the three helpers ``_upgrade_guard`` lazily imports so the
    state-check path sees a Blocked DB without touching any real disk.
    """
    blocked = SimpleNamespace(
        ready=False,
        message="3 migration(s) need confirmation before the app can start.",
        last_backup="/tmp/pre-upgrade.db",
    )

    monkeypatch.setattr(
        "chaoscypher_cli.engine_config.read_current_database",
        lambda: "default",
    )
    monkeypatch.setattr(
        "chaoscypher_core.database.engine.get_db_path",
        lambda name: f"/tmp/{name}/app.db",
    )
    monkeypatch.setattr(
        "chaoscypher_core.database.migrations.state.get_upgrade_state",
        lambda _path: blocked,
    )


def test_upgrade_guard_bypasses_help_on_db_touching_subcommand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``chaoscypher source --help`` must render help, not the gate banner."""
    _install_blocked_state(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["chaoscypher", "source", "--help"])

    from chaoscypher_cli.__main__ import main

    result = CliRunner().invoke(main, ["source", "--help"])

    assert result.exit_code == 0, (result.output, result.stderr)
    assert "Usage:" in result.output
    assert "waiting on a schema upgrade" not in result.output
    assert "waiting on a schema upgrade" not in result.stderr


def test_upgrade_guard_bypasses_help_flag_anywhere_in_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--help`` on a sub-subcommand (e.g. ``source list --help``) also
    bypasses the guard — argv inspection is intentionally positional-
    agnostic so the user can ask for help at any level of nesting.
    """
    _install_blocked_state(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["chaoscypher", "source", "list", "--help"])

    from chaoscypher_cli.__main__ import main

    result = CliRunner().invoke(main, ["source", "list", "--help"])

    assert "waiting on a schema upgrade" not in result.stderr


def test_upgrade_guard_blocks_real_invocation_when_db_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``--help``, the gate still fires for DB-touching subcommands."""
    _install_blocked_state(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["chaoscypher", "source", "list"])

    from chaoscypher_cli.__main__ import main

    result = CliRunner().invoke(main, ["source", "list"])

    assert result.exit_code == 2
    assert "waiting on a schema upgrade" in result.stderr
    assert "chaoscypher db migrate" in result.stderr


def test_upgrade_guard_allows_safe_subcommand_when_db_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``db`` is on the safe-subcommands allowlist — the user must be
    able to inspect / repair the gate state without first satisfying
    the gate.
    """
    _install_blocked_state(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["chaoscypher", "db", "--help"])

    from chaoscypher_cli.__main__ import main

    result = CliRunner().invoke(main, ["db", "--help"])

    assert result.exit_code == 0, (result.output, result.stderr)
    assert "waiting on a schema upgrade" not in result.stderr


# ---------------------------------------------------------------------------
# Bug 3: ``--database <other>`` must route the gate at the other DB, not the
# one cli.yaml calls "current". Without this, users with one Blocked DB and
# any number of other Ready DBs would be unable to use the others until
# they applied migrations on a DB they may not even care about.
# ---------------------------------------------------------------------------


def test_extract_database_override_recognises_long_form() -> None:
    from chaoscypher_cli.__main__ import _extract_database_override

    assert _extract_database_override(["source", "list", "--database", "fresh"]) == "fresh"


def test_extract_database_override_recognises_short_form() -> None:
    from chaoscypher_cli.__main__ import _extract_database_override

    assert _extract_database_override(["source", "list", "-d", "fresh"]) == "fresh"


def test_extract_database_override_recognises_equals_form() -> None:
    from chaoscypher_cli.__main__ import _extract_database_override

    assert _extract_database_override(["source", "list", "--database=fresh"]) == "fresh"
    assert _extract_database_override(["source", "list", "-d=fresh"]) == "fresh"


def test_extract_database_override_returns_none_when_absent() -> None:
    from chaoscypher_cli.__main__ import _extract_database_override

    assert _extract_database_override(["source", "list", "--quick"]) is None


def test_extract_database_override_does_not_consume_trailing_flag() -> None:
    """If ``--database`` is the LAST arg (operator typo), don't try to
    read past the end of argv — return ``None`` so the gate falls back
    to the configured current DB.
    """
    from chaoscypher_cli.__main__ import _extract_database_override

    assert _extract_database_override(["source", "list", "--database"]) is None


def test_upgrade_guard_routes_to_override_database_when_current_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default DB is Blocked but the override DB is Ready — the gate
    must let the command run. Without this routing, every fresh-DB
    workflow gets jammed by the configured-current DB's migration state.

    The mocked ``get_upgrade_state`` returns Blocked for the configured
    ``default`` DB and Ready for any other path — so a passing test
    proves the guard targeted the override path, not the configured one.
    """
    monkeypatch.setattr(
        "chaoscypher_cli.engine_config.read_current_database",
        lambda: "default",
    )
    monkeypatch.setattr(
        "chaoscypher_core.database.engine.get_db_path",
        lambda name: f"/tmp/{name}/app.db",
    )

    blocked = SimpleNamespace(
        ready=False,
        message="default DB has pending migrations",
        last_backup=None,
    )
    ready = SimpleNamespace(ready=True, message="", last_backup=None)

    def _state_by_path(path: str) -> SimpleNamespace:
        # Blocked only for the configured "default" DB.
        return blocked if "default" in path else ready

    monkeypatch.setattr(
        "chaoscypher_core.database.migrations.state.get_upgrade_state",
        _state_by_path,
    )
    monkeypatch.setattr(sys, "argv", ["chaoscypher", "source", "list", "--database", "fresh_db"])

    from chaoscypher_cli.__main__ import main

    result = CliRunner().invoke(main, ["source", "list", "--database", "fresh_db"])

    # The gate must NOT fire. The downstream ``source list`` body may
    # still fail (we haven't given it a real DB), but the banner must
    # not appear and the exit code must not be the gate's 2.
    assert "waiting on a schema upgrade" not in result.stderr, (
        "The gate fired despite --database fresh_db pointing at a Ready DB. "
        "Bug 3 regression: gate is reading cfg.database.current instead of "
        "the override."
    )


def test_upgrade_guard_still_blocks_when_override_database_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The override DB itself is Blocked — the gate must fire.

    Symmetric to the previous test: routing must work in both directions.
    If a user explicitly targets a Blocked DB, they get the banner for
    that DB, not for whatever ``current`` happens to be.
    """
    monkeypatch.setattr(
        "chaoscypher_cli.engine_config.read_current_database",
        lambda: "default",
    )
    monkeypatch.setattr(
        "chaoscypher_core.database.engine.get_db_path",
        lambda name: f"/tmp/{name}/app.db",
    )

    # Ready for default, Blocked for "blocked_other".
    ready = SimpleNamespace(ready=True, message="", last_backup=None)
    blocked = SimpleNamespace(
        ready=False, message="blocked_other has pending migrations", last_backup=None
    )

    def _state_by_path(path: str) -> SimpleNamespace:
        return blocked if "blocked_other" in path else ready

    monkeypatch.setattr(
        "chaoscypher_core.database.migrations.state.get_upgrade_state",
        _state_by_path,
    )
    monkeypatch.setattr(
        sys, "argv", ["chaoscypher", "source", "list", "--database", "blocked_other"]
    )

    from chaoscypher_cli.__main__ import main

    result = CliRunner().invoke(main, ["source", "list", "--database", "blocked_other"])

    assert result.exit_code == 2
    assert "waiting on a schema upgrade" in result.stderr
    assert "blocked_other has pending migrations" in result.stderr
