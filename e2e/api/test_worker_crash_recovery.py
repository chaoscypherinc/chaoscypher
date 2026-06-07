# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Worker-crash recovery e2e test.

Validates the queue-rehydration / SourceRecovery paths that
2026-05-21's operability hardening campaign added: when the neuron
worker dies mid-extraction, its in-flight Valkey hash gets orphaned,
but the next worker boot reconciles the DB-side ``ChunkExtractionTask``
rows in (``pending``, ``queued``, ``running``) and re-enqueues them.

Race shape:
  t=0   : upload with extract_entities=True
  t≈0   : indexing completes, extraction tasks enter LLM queue
  t≈1   : extraction call hits fake-ollama, blocks for ``delay`` s
  t≈2   : test runs ``supervisorctl restart neuron`` inside the app
          container — kills the worker mid-LLM-call
  t≈2-5 : supervisor restarts cc-neuron; rehydration kicks in;
          orphaned task hash is re-enqueued
  t≈?   : extraction resumes (fake-ollama no longer blocked) and
          the source eventually reaches committed

The test asserts the source reaches a terminal-success status
(``committed``) within a generous timeout. If rehydration is broken,
the source either stays stuck in ``extracting`` forever (timeout) or
flips to ``error`` with a worker-crashed message.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from e2e.api._fake_ollama_helpers import (
    reset_fake_ollama,
    set_chat_delay,
    wait_for_queue_idle,
)


_APP_CONTAINER = os.environ.get("E2E_APP_CONTAINER", "chaoscypher-e2e-app")


def _docker_exec(*args: str) -> subprocess.CompletedProcess:
    """Run ``docker exec`` against the app container.

    Skipped at module load if ``docker`` isn't on PATH; the test
    self-skips if the container isn't running. Returns the
    CompletedProcess so callers can read stdout/stderr.
    """
    return subprocess.run(  # noqa: S603 - fixed argv, no shell; e2e harness shells out to docker by design
        ["docker", "exec", _APP_CONTAINER, *args],  # noqa: S607 - docker resolved from PATH on every dev/CI host
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


# Discover cc-neuron's PID by scanning /proc — the slim app image
# doesn't ship ``pgrep`` / ``pkill`` / ``ps``, and supervisorctl
# needs an auth password we don't have. /proc is always there.
#
# Two self-match guards (the classic pgrep footgun): the scanning
# shell's own cmdline contains this script text, so (a) the pattern
# uses the ``[n]`` bracket trick — the class must sit OUTSIDE the
# quotes (a quoted ``[n]`` is matched literally in POSIX case
# patterns) so it matches the real worker's ``…/cc-neuron `` but not
# the ``/cc-neuro"[n]"`` text inside our own cmdline — and (b) the
# scanner skips its own PID outright. Without these, /proc glob
# order (lexicographic, so PID "15000" sorts before "25") decides
# whether the scanner finds the worker or itself — and
# ``_kill_neuron`` then SIGKILLs its own shell (docker exec rc=137).
_NEURON_PID_CMD = (
    "for d in /proc/[0-9]*; do "
    '[ "${d##*/}" = "$$" ] && continue; '
    'cmd=$(cat $d/cmdline 2>/dev/null | tr "\\0" " "); '
    'case "$cmd" in '
    '*"/cc-neuro"[n]" "*|*"/cc-neuro"[n]) echo "${d##*/}"; exit 0;; '
    "esac; done"
)


def _neuron_pid() -> str | None:
    """Return the cc-neuron PID inside the container (or None)."""
    result = _docker_exec("sh", "-c", _NEURON_PID_CMD)
    return result.stdout.strip() or None


def _kill_neuron() -> subprocess.CompletedProcess:
    """SIGKILL cc-neuron via /proc lookup + ``kill -9``.

    Bypasses ``supervisorctl`` (auth) and ``pkill``/``pgrep`` (not in
    the slim image). ``supervisord`` notices via SIGCHLD and respawns
    the worker because the program has ``autorestart=true``.
    """
    return _docker_exec(
        "sh",
        "-c",
        f'pid=$({_NEURON_PID_CMD}); if [ -n "$pid" ]; then kill -9 "$pid"; fi',
    )


@pytest.fixture
def cleanup_fake_ollama():
    yield
    reset_fake_ollama()


@pytest.mark.requires_llm
def test_neuron_crash_during_extraction_recovers(
    client: httpx.Client,
    sample_data_dir: str,
    cleanup_fake_ollama,
) -> None:
    """Killing the neuron worker mid-extraction recovers cleanly.

    Source either reaches ``committed`` (rehydration re-enqueued the
    chunk task, worker completed it) or stays in a recoverable state
    where the operator could retry. ``error`` with no recovery path
    is the failure mode this test pins against.
    """
    # Verify docker exec works at all before we go further. If the
    # runner doesn't have docker (host-side run vs runner-side run),
    # skip rather than fail confusingly.
    probe = _docker_exec("true")
    if probe.returncode != 0:
        pytest.skip(
            f"docker exec against {_APP_CONTAINER} failed (rc={probe.returncode}): {probe.stderr}"
        )

    # Drain any leftover queue work first. The chat_delay we're about
    # to set is global, so a backlogged task would inherit the
    # slowdown and starve our own upload.
    wait_for_queue_idle(client, timeout=30.0)
    set_chat_delay(8.0)

    sample = Path(sample_data_dir) / "sample.txt"
    with sample.open("rb") as f:
        upload = client.post(
            "/api/v1/sources",
            files={"file": ("crash_test.txt", f, "text/plain")},
            # auto_confirm bypasses the domain-confirmation gate — without
            # it the source parks at awaiting_confirmation after indexing
            # and never reaches extracting (the gate landed after this
            # test was written; there is no human to click Confirm here).
            data={"extract_entities": "true", "auto_confirm": "true"},
        )
    assert upload.status_code == 202, upload.text
    source_id = upload.json()["id"]

    # Wait for the source to actually enter the extracting phase —
    # otherwise we'd be restarting the worker before there's anything
    # for it to recover. Generous window: the chat_delay set above is
    # global, so pre-extraction LLM calls (eager domain detection at
    # chunk time) already eat 8s+ of it, and 2-core CI runners add
    # real overhead on top — 15s passes locally but flakes in CI.
    _wait_for_status_in(client, source_id, statuses={"extracting"}, timeout=60)

    pid_before = _neuron_pid()
    assert pid_before is not None, "neuron worker isn't running"

    # SIGKILL the worker. supervisord notices via SIGCHLD + restarts
    # it because the program has autorestart=true.
    kill_result = _kill_neuron()
    assert kill_result.returncode in (0, 1), (
        f"pkill failed: rc={kill_result.returncode} {kill_result.stderr}"
    )

    # Wait for supervisord to spawn a fresh worker with a new PID,
    # then for that worker to finish its rehydration sweep.
    _wait_for_neuron_restart(pid_before, timeout=30)

    # Now drop the fake-ollama delay so the re-enqueued extraction
    # completes promptly when the recovered worker picks it up. If
    # rehydration is broken, source stays stuck in extracting and the
    # poll times out below.
    set_chat_delay(0.0)

    detail = _wait_for_status_in(
        client,
        source_id,
        statuses={"committed", "indexed", "error"},
        timeout=60,
    )
    status = detail.get("processing_status") or detail.get("status")
    assert status in ("committed", "indexed"), (
        f"crash recovery failed: source ended in {status!r} "
        f"(stage={detail.get('error_stage')}, "
        f"err={detail.get('error_message')!r}). "
        f"Rehydration probably didn't re-enqueue the orphaned task."
    )


def _wait_for_status_in(
    client: httpx.Client,
    source_id: str,
    statuses: set[str],
    timeout: int,
) -> dict:
    """Poll until source status hits one of ``statuses`` or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        resp = client.get(f"/api/v1/sources/{source_id}")
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("processing_status") or data.get("status", "")
            if status in statuses:
                return data
        time.sleep(0.3)
    raise TimeoutError(f"source {source_id} never reached any of {statuses} within {timeout}s")


def _wait_for_neuron_restart(pid_before: str, timeout: int = 30) -> None:
    """Poll until cc-neuron has a different PID than ``pid_before``.

    Confirms the old process is gone AND supervisord respawned. A
    short post-respawn sleep gives the new worker time to complete
    its rehydration sweep before tests interact with the queue.
    """
    start = time.time()
    while time.time() - start < timeout:
        pid_now = _neuron_pid()
        if pid_now and pid_now != pid_before:
            # New worker is up — give it a moment to finish boot/rehydrate.
            time.sleep(2.0)
            return
        time.sleep(0.5)
    raise TimeoutError(
        f"neuron did not restart with a new PID within {timeout}s "
        f"(pid_before={pid_before}, pid_now={_neuron_pid()!r})"
    )
