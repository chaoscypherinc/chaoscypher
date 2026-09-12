# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for ``_first_run_gate`` in ``chaoscypher_cli.__main__``.

The gate spares fresh ``pipx install`` users the confusing "LLM
Required" error by detecting the first-run signature (no engine
configuration in ``settings.yaml`` and no ``CHAOSCYPHER_LLM_PROVIDER``
env override) and routing them into the setup wizard instead —
interactively when on a TTY, with an actionable error otherwise.

The tests pin three halves of the contract:

* The gate fires when the signature matches AND the subcommand needs
  setup (e.g. ``source``).
* The gate is silent for safe subcommands (``setup``, ``health``,
  ``doctor``, ``config``, ``db``) and for ``--help`` / ``--version``.
* Non-interactive callers (no TTY, no ``--yes``) exit 2 with an
  actionable message rather than hanging on a ``confirm`` prompt that
  no one will answer.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
import yaml
from click.testing import CliRunner

from chaoscypher_cli.__main__ import _FIRST_RUN_SAFE_SUBCOMMANDS


def _write_settings(data_dir: Path, data: dict) -> None:
    (data_dir / "settings.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _stub_upgrade_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the upgrade-guard's dependencies so it short-circuits cleanly.

    The two gates run in sequence at group time; the first-run gate is
    what these tests exercise. Without this the upgrade guard would try
    to read a real DB schema state.
    """
    monkeypatch.setattr(
        "chaoscypher_core.database.engine.get_db_path",
        lambda name: f"/tmp/{name}/app.db",
    )
    monkeypatch.setattr(
        "chaoscypher_core.database.migrations.state.get_upgrade_state",
        lambda _path: SimpleNamespace(ready=True, message=None, last_backup=None),
    )


def _call_gate(
    monkeypatch: pytest.MonkeyPatch, *, invoked: str, argv: list[str] | None = None
) -> str:
    """Drive ``_first_run_gate`` directly and return what it wrote to stderr.

    Invoking through ``CliRunner`` with ``--help`` cannot exercise the
    bypass branches: the gate returns on the ``--help`` in ``sys.argv``
    *before* it reads ``invoked_subcommand`` or calls
    ``is_setup_completed()``, so such a test asserts nothing about the
    branch it names. Calling the gate with a real ``click.Context`` reaches
    the branch and keeps the test hermetic — the safe subcommands include
    ``db`` and ``upgrade``, which must not actually run.

    Raises ``click.exceptions.Exit`` when the gate fires (exit code 2).
    """
    from chaoscypher_cli.__main__ import _first_run_gate, main

    monkeypatch.setattr(sys, "argv", argv if argv is not None else ["chaoscypher", invoked])

    buffer = io.StringIO()
    ctx = click.Context(main)
    ctx.invoked_subcommand = invoked
    with contextlib.redirect_stderr(buffer):
        _first_run_gate(ctx)
    return buffer.getvalue()


def test_gate_blocks_source_when_first_run_signature_matches(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: Path
) -> None:
    """Fresh install + ``source add`` → friendly message + exit 2 in
    non-interactive mode (the CliRunner has no real stdin TTY).
    """
    _stub_upgrade_guard(monkeypatch)
    # No settings.yaml written → first-run signature.
    monkeypatch.setattr(sys, "argv", ["chaoscypher", "source", "add", "doc.pdf"])

    from chaoscypher_cli.__main__ import main
    from chaoscypher_cli.engine_config import settings_yaml_path

    result = CliRunner().invoke(main, ["source", "add", "doc.pdf"])

    assert result.exit_code == 2, (result.output, result.stderr)
    assert "first time running" in result.stderr
    assert str(settings_yaml_path()) in result.stderr
    assert "chaoscypher setup" in result.stderr


def test_gate_fires_without_the_help_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control for the four bypass tests below: same call shape, gate fires.

    Without this, a bypass test that silently stopped reaching its branch
    would look identical to one that reached it and correctly stayed quiet.
    Here nothing is configured and ``source`` is not a safe subcommand, so
    the gate must reach its non-interactive arm and exit 2.
    """
    _stub_upgrade_guard(monkeypatch)

    with pytest.raises(click.exceptions.Exit) as excinfo:
        _call_gate(monkeypatch, invoked="source")

    assert excinfo.value.exit_code == 2


def test_gate_bypassed_when_setup_completed(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: Path
) -> None:
    """If settings.yaml records ``setup_completed: true`` we don't gate —
    the engine has been configured (CLI wizard, web wizard, or env).
    """
    _stub_upgrade_guard(monkeypatch)
    _write_settings(isolated_settings, {"setup_completed": True})

    assert _call_gate(monkeypatch, invoked="source") == ""


def test_gate_bypassed_when_llm_configured(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: Path
) -> None:
    """An explicit ``llm.chat_provider`` in settings.yaml counts as
    configured — the gate must stay silent.
    """
    _stub_upgrade_guard(monkeypatch)
    _write_settings(isolated_settings, {"llm": {"chat_provider": "ollama"}})

    assert _call_gate(monkeypatch, invoked="source") == ""


def test_gate_bypassed_when_env_provider_set(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: Path
) -> None:
    """Operators who set ``CHAOSCYPHER_LLM_PROVIDER`` via env (env-only
    deployments have no settings.yaml) shouldn't see the gate.
    """
    _stub_upgrade_guard(monkeypatch)
    # No settings.yaml on disk, but the env override marks it configured.
    monkeypatch.setenv("CHAOSCYPHER_LLM_PROVIDER", "ollama")

    assert _call_gate(monkeypatch, invoked="source") == ""


@pytest.mark.parametrize("safe_cmd", sorted(_FIRST_RUN_SAFE_SUBCOMMANDS))
def test_gate_silent_for_safe_subcommands(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: Path, safe_cmd: str
) -> None:
    """Bootstrap / read-only diagnostic commands must always run, even
    on a fresh install — they're the user's escape hatch.

    Parametrized off the production frozenset rather than a hand-copied
    list, so a subcommand added to the escape hatch is covered
    automatically instead of drifting out of test.
    """
    _stub_upgrade_guard(monkeypatch)
    # No settings.yaml → first-run signature, but safe subcommands bypass.

    assert _call_gate(monkeypatch, invoked=safe_cmd) == "", (
        f"Gate fired on safe subcommand `{safe_cmd}` — should have bypassed"
    )


def test_gate_silent_on_top_level_help(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: Path
) -> None:
    """`chaoscypher --help` must always render, even on a fresh install."""
    _stub_upgrade_guard(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["chaoscypher", "--help"])

    from chaoscypher_cli.__main__ import main

    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "first time running" not in result.stderr
    assert "Commands:" in result.output


def test_gate_silent_on_version_flag(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: Path
) -> None:
    _stub_upgrade_guard(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["chaoscypher", "--version"])

    from chaoscypher_cli.__main__ import main

    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert "first time running" not in result.stderr


def test_gate_non_interactive_mode_exits_with_actionable_message(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: Path
) -> None:
    """Non-TTY callers (CI scripts, IDE terminals, pipes) need a clean
    error — never a hung ``confirm`` prompt.
    """
    _stub_upgrade_guard(monkeypatch)
    # No settings.yaml → first-run signature.
    monkeypatch.setattr(sys, "argv", ["chaoscypher", "chat"])

    from chaoscypher_cli.__main__ import main

    # CliRunner's stdin is not a TTY, so the gate hits the
    # non-interactive branch automatically.
    result = CliRunner().invoke(main, ["chat"], input="")

    assert result.exit_code == 2
    assert "chaoscypher setup" in result.stderr
    assert "chat" in result.stderr
