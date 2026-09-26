# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``python -m chaoscypher_cortex.main`` must run the Click CLI.

``chaoscypher serve`` and ``chaoscypher compose up`` launch the server as
``python -m chaoscypher_cortex.main start …``. Before the ``__main__`` guard
existed the module only built ``app`` at import time and exited 0, so both
commands reported a server that was never there. Pin the guard by running
the module as a script and asserting it renders the CLI's help.
"""

from __future__ import annotations

import subprocess
import sys


def test_module_runs_the_cli_when_executed_as_main() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "chaoscypher_cortex.main", "--help"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "start" in result.stdout, result.stdout
    assert "status" in result.stdout, result.stdout


def test_module_rejects_unknown_command_instead_of_exiting_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "chaoscypher_cortex.main", "definitely-not-a-command"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode != 0
    assert "No such command" in result.stderr
