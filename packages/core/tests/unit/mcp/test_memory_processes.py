# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The server-process helper leaves no process behind when its test ends."""

from __future__ import annotations

from pathlib import Path

from tests.unit.mcp.memory_processes import ServerProcesses, collect


def test_stop_kills_a_server_still_running(tmp_path: Path) -> None:
    started = ServerProcesses()
    process, out = started.spawn(
        tmp_path, [("remember", {"text": "Pierre Bezukhov moved to Lyon."})], model="hold"
    )
    collect(out, until="held", process=process)
    assert process.is_alive()  # a held server never ends by itself
    started.stop()
    assert not process.is_alive()
    assert process.exitcode is not None
