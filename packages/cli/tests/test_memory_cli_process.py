# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The real memory CLI in a process of its own (``memory_cli_child.py``, a fake model).

- Its output, logs included, never holds a memory's text (spec 2026-10-24 D16): the CLI's
  own logging setup, nothing captured or reconfigured, a model raising an error that
  quotes the prompt, and so the memory.
- SIGTERM stops ``memory relink`` and ``mcp`` with the claim of the memory they were
  linking given back, so the next link takes it at once rather than after the stale age.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from chaoscypher_core import Engine
from chaoscypher_core.models import MEMORY_SOURCE_TYPE, MemoryLinkState
from chaoscypher_core.settings import EngineSettings


LYON = "Pierre Bezukhov moved to Lyon."
WORDS = ("Pierre", "Bezukhov", "Lyon")
CHILD = Path(__file__).with_name("memory_cli_child.py")


def child_env(**extra: str) -> dict[str, str]:
    """The environment of a CLI child: vector search off, the first-run gate passed."""
    return {
        **os.environ,
        "CHAOSCYPHER_SEARCH__ENABLE_VECTOR_SEARCH": "false",
        "CHAOSCYPHER_LLM_PROVIDER": "ollama",
        **extra,
    }


async def stored_memory(data_dir: Path, text: str) -> str:
    """A memory in the CLI's default database, stored and not linked yet."""
    settings = EngineSettings()
    settings.search.enable_vector_search = False
    engine = Engine(str(data_dir / "databases" / "default"), settings=settings)
    try:
        result = await engine.process_document(
            text,
            filename="memory.txt",
            forced_domain="generic",
            source_type=MEMORY_SOURCE_TYPE,
            source_fields={
                "memory_by": "alice",
                "valid_from": "2026-10-20",
                "full_text": text,
                "memory_link_state": MemoryLinkState.PENDING.value,
            },
            extract=False,
        )
    finally:
        engine.close()
    return result.source_id


async def test_relink_prints_no_memory_text_when_the_model_quotes_it(
    data_dir_in_tmp: Path,
) -> None:
    await stored_memory(data_dir_in_tmp, LYON)
    done = subprocess.run(  # noqa: S603, ASYNC221 - the CLI under test, run to its end
        [sys.executable, str(CHILD), "raise", "memory", "relink"],
        env=child_env(LOG_LEVEL="INFO"),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    printed = done.stdout + done.stderr
    assert done.returncode == 3, printed
    assert "still pending 1 (model failed)" in done.stdout
    assert "memory_link_failed" in printed  # the failure was logged, and the log is read here
    assert [line for line in printed.splitlines() if any(w in line for w in WORDS)] == []
    assert "Traceback" not in printed
    assert "func_name=log_failure" not in printed  # the call site is the caller's


async def test_relink_logs_no_memory_text_from_lines_that_do_not_parse(
    data_dir_in_tmp: Path,
) -> None:
    """The parser's warnings quote the bad lines; the chunk's names the entities it kept."""
    await stored_memory(data_dir_in_tmp, LYON)
    done = subprocess.run(  # noqa: S603, ASYNC221 - the CLI under test, run to its end
        [sys.executable, str(CHILD), "malformed", "memory", "relink"],
        env=child_env(LOG_LEVEL="INFO"),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    printed = done.stdout + done.stderr
    assert done.returncode == 0, printed
    # the paths that quote the memory ran, and their records are read here
    assert "chunk_has_entities_but_no_relationships" in printed
    assert "entity_line_missing_name" in printed
    assert [line for line in printed.splitlines() if any(w in line for w in WORDS)] == []


def _row(data_dir: Path, source_id: str) -> tuple[str, str | None, int]:
    with sqlite3.connect(data_dir / "databases" / "default" / "app.db") as db:
        return db.execute(
            "SELECT memory_link_state, memory_link_claim, recovery_attempts FROM sources "
            "WHERE id = ?",
            (source_id,),
        ).fetchone()


def _until_linking(data_dir: Path, source_id: str, child: subprocess.Popen[str]) -> None:
    """Wait until the child has claimed the memory (its held model is waiting)."""
    deadline = time.monotonic() + 120
    while _row(data_dir, source_id)[1] is None:
        assert child.poll() is None, child.communicate()
        assert time.monotonic() < deadline, "the memory was never claimed"
        time.sleep(0.1)


def _start(args: list[str], **extra: str) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603 - the CLI under test
        [sys.executable, str(CHILD), "hold", *args],
        env=child_env(**extra),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _ended(child: subprocess.Popen[str]) -> int:
    try:
        child.wait(timeout=60)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    return child.returncode


async def test_sigterm_stops_relink_and_gives_the_claim_back(data_dir_in_tmp: Path) -> None:
    source_id = await stored_memory(data_dir_in_tmp, LYON)
    child = _start(["memory", "relink"])
    _until_linking(data_dir_in_tmp, source_id, child)
    child.send_signal(signal.SIGTERM)
    assert _ended(child) == 128 + signal.SIGTERM
    assert _row(data_dir_in_tmp, source_id) == ("pending", None, 0)


def _send(child: subprocess.Popen[str], message: dict[str, Any]) -> None:
    assert child.stdin is not None
    child.stdin.write(json.dumps(message) + "\n")
    child.stdin.flush()


def _answer(child: subprocess.Popen[str], request_id: int) -> dict[str, Any]:
    assert child.stdout is not None
    while True:
        line = child.stdout.readline()
        assert line, child.communicate()
        message = json.loads(line)
        if message.get("id") == request_id:
            return message


async def _remembered_while_held(data_dir: Path) -> tuple[subprocess.Popen[str], str]:
    """A write-mode ``chaoscypher mcp`` that remembered LYON and is still linking it (held)."""
    await stored_memory(data_dir, "The weather was grey.")  # the database exists
    child = _start(["mcp", "--mode", "write"], CHAOSCYPHER_MCP__MEMORY__WAIT_SECONDS="1")
    _send(
        child,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
    )
    assert "result" in _answer(child, 1)
    _send(child, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    _send(
        child,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "remember", "arguments": {"text": LYON}},
        },
    )
    answer = _answer(child, 2)["result"]["content"][0]["text"]
    assert "did not answer in time" in answer
    with sqlite3.connect(data_dir / "databases" / "default" / "app.db") as db:
        [(source_id,)] = db.execute(
            "SELECT id FROM sources WHERE full_text = ?", (LYON,)
        ).fetchall()
    assert _row(data_dir, source_id)[0] == "linking"
    return child, source_id


async def test_sigterm_stops_mcp_and_gives_the_remembered_memorys_claim_back(
    data_dir_in_tmp: Path,
) -> None:
    child, source_id = await _remembered_while_held(data_dir_in_tmp)
    child.send_signal(signal.SIGTERM)  # stdin still open
    assert _ended(child) == 128 + signal.SIGTERM, child.stderr.read() if child.stderr else ""
    assert _row(data_dir_in_tmp, source_id) == ("pending", None, 0)


async def test_ctrl_c_stops_mcp_and_gives_the_claim_back(data_dir_in_tmp: Path) -> None:
    child, source_id = await _remembered_while_held(data_dir_in_tmp)
    child.send_signal(signal.SIGINT)  # stdin still open
    assert _ended(child) == 128 + signal.SIGINT, child.stderr.read() if child.stderr else ""
    assert _row(data_dir_in_tmp, source_id) == ("pending", None, 0)


async def test_a_client_closing_stdin_ends_mcp_cleanly_with_the_claim_given_back(
    data_dir_in_tmp: Path,
) -> None:
    child, source_id = await _remembered_while_held(data_dir_in_tmp)
    assert child.stdin is not None
    child.stdin.close()
    assert _ended(child) == 0, child.stderr.read() if child.stderr else ""
    assert _row(data_dir_in_tmp, source_id) == ("pending", None, 0)


async def test_a_second_sigterm_during_shutdown_still_gives_the_claim_back(
    data_dir_in_tmp: Path,
) -> None:
    child, source_id = await _remembered_while_held(data_dir_in_tmp)
    child.send_signal(signal.SIGTERM)
    child.send_signal(signal.SIGTERM)  # an impatient service manager
    assert _ended(child) == 128 + signal.SIGTERM, child.stderr.read() if child.stderr else ""
    assert _row(data_dir_in_tmp, source_id) == ("pending", None, 0)


def test_mcp_on_a_database_that_does_not_exist_yet_starts_without_an_error(
    data_dir_in_tmp: Path,
) -> None:
    """``chaoscypher mcp --database NEW`` builds the database; nothing logs a migration error."""
    done = subprocess.run(  # noqa: S603 - the CLI under test, run to its end
        [sys.executable, str(CHILD), "raise", "mcp", "--database", "fresh1"],
        env=child_env(LOG_LEVEL="INFO"),
        stdin=subprocess.DEVNULL,  # the client goes away at once: the server ends
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "mcp_startup_migration_error" not in done.stdout + done.stderr
    assert (data_dir_in_tmp / "databases" / "fresh1" / "app.db").exists()
