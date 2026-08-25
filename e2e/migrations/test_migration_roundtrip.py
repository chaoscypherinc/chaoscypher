# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pre-squash-database refusal e2e smoke.

Boots a separate copy of the e2e stack pre-seeded with a snapshot of
``app.db`` + ``credentials.json`` taken from a build that predates the
2026-06-02 migration squash. That snapshot is stamped at ``0044`` — a
revision from the original 0001-0050 chain the squash deleted — so it is
exactly the artifact this tier needs. **Do not regenerate it**; a
refreshed snapshot would be stamped at the current head and would stop
exercising anything.

Ruled 2026-08-14: databases from the pre-squash lineage are UNSUPPORTED
(the squash predates every public release, so no released build ever
wrote one). This tier asserts the supported behaviour:

  1. Cortex refuses to start and says why — the offending revision id,
     that the database predates the 2026-06-02 baseline (pre-v0.1.0),
     and the recovery (back up, then re-create or export/re-import).
  2. The stamp is left untouched. The pre-2026-08-14 code silently
     re-stamped it at ``0001`` and replayed 0002→HEAD against a schema
     those migrations were never written for, which boots into a
     ``SchemaIntegrityError`` restart loop.
  3. The stack never reports healthy — the refusal is terminal, not a
     transient that a restart clears.

This test runs against a separate compose project
(``chaoscypher-e2e-snap`` on port 8889) so it doesn't collide with
the main e2e stack on port 8888. It manages its own container
lifecycle via ``docker compose`` subprocesses.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest


_PROJECT = "chaoscypher-e2e-snap"
_CONTAINER = "chaoscypher-e2e-snap-app"
_COMPOSE_FILE = (
    Path(__file__).parents[2] / "packages" / "docker" / "e2e" / "docker-compose.snapshot.yml"
)
_BASE_URL = "http://localhost:8889"

# The revision the committed snapshot is stamped at. From the pre-squash
# 0001-0050 chain, so this build's script directory cannot resolve it.
_SNAPSHOT_REVISION = "0044"

# Substrings the refusal must put in front of an operator. Each is matched
# against the container's combined log stream.
_REQUIRED_LOG_MARKERS = (
    "ChaosCypher cannot start",
    _SNAPSHOT_REVISION,
    "2026-06-02",
    "pre-v0.1.0",
    "back up the database file",
)


def _have_docker() -> bool:
    return shutil.which("docker") is not None


pytestmark = pytest.mark.skipif(
    not _have_docker(),
    reason="docker not on PATH — pre-squash refusal test needs a runner with docker.",
)


def _compose(*args: str, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a ``docker compose`` subcommand against this tier's own project."""
    return subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(_COMPOSE_FILE),
            "--project-name",
            _PROJECT,
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def _container_logs() -> str:
    """Return the snapshot container's combined stdout/stderr so far."""
    proc = subprocess.run(
        ["docker", "logs", _CONTAINER],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout + proc.stderr


def _wait_for_refusal(timeout: int = 300) -> str:
    """Poll container logs until the lineage refusal appears; return the logs.

    Cortex fails during its pre-uvicorn ``init_database`` call, so the
    message lands within a boot cycle. Polling (rather than sleeping out a
    fixed window) keeps the tier fast when the stack is warm.
    """
    start = time.time()
    logs = ""
    while time.time() - start < timeout:
        logs = _container_logs()
        if "ChaosCypher cannot start" in logs:
            return logs
        time.sleep(2.0)
    msg = (
        f"snapshot container never logged the pre-squash refusal within "
        f"{timeout}s. Tail of logs:\n{logs[-4000:]}"
    )
    raise AssertionError(msg)


def _reports_healthy(url: str) -> bool:
    """True if the readiness probe currently reports a healthy stack."""
    try:
        resp = httpx.get(f"{url}/api/v1/health", timeout=3.0)
    except Exception:
        return False
    return resp.status_code == 200 and bool(resp.json().get("healthy"))


def _stamped_revision() -> str:
    """Read ``alembic_version`` straight out of the container's app.db."""
    proc = subprocess.run(
        [
            "docker",
            "exec",
            _CONTAINER,
            "python3",
            "-c",
            (
                "import sqlite3; "
                "c = sqlite3.connect('/data/databases/default/app.db'); "
                "print(c.execute('SELECT version_num FROM alembic_version').fetchone()[0])"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return proc.stdout.strip()


@contextmanager
def _snapshot_stack() -> Iterator[None]:
    """Bring the snapshot stack up for the duration of the block.

    Cleaning the volume on both entry and exit isolates the run from any
    previous one and from the default e2e stack. Unlike the pre-2026-08-14
    version of this tier there is no wait-for-healthy here: this stack is
    expected never to become healthy.
    """
    _compose("down", "-v", check=False)
    try:
        try:
            # ``--build`` is load-bearing: this tier asserts behaviour that only
            # exists in the working tree's cortex. Reusing a stale
            # ``chaoscypher-e2e-snap-app`` image left over from an earlier run
            # would silently test old code and fail for the wrong reason.
            #
            # Nested inside this ``try`` (not a bare call ahead of it) so a
            # ``subprocess.TimeoutExpired`` from a build that overruns 2700s —
            # not just a ``CalledProcessError`` rc failure — still reaches the
            # ``finally`` below and tears the half-built stack down instead of
            # leaking it.
            _compose("up", "-d", "--build", "app", timeout=2700)
        except subprocess.CalledProcessError as exc:
            pytest.fail(
                f"docker compose up failed: rc={exc.returncode}\n"
                f"stdout={exc.stdout}\nstderr={exc.stderr}"
            )
        yield
    finally:
        _compose("down", "-v", check=False)


def test_pre_squash_snapshot_refuses_to_boot() -> None:
    """A pre-squash database is refused loudly, left alone, and stays down.

    One test, one stack lifecycle: bringing the container up costs minutes,
    and splitting the assertions across tests would let pytest-xdist
    schedule them into different workers, each racing its own ``up``/
    ``down -v`` against the same compose project.
    """
    with _snapshot_stack():
        logs = _wait_for_refusal()

        missing = [m for m in _REQUIRED_LOG_MARKERS if m not in logs]
        assert not missing, (
            f"refusal message is missing guidance {missing}. Tail of logs:\n{logs[-4000:]}"
        )

        # No silent re-stamp: the row is exactly as the snapshot shipped it.
        assert _stamped_revision() == _SNAPSHOT_REVISION, (
            "startup rewrote alembic_version on an unsupported database — the "
            "refusal must not touch the stamp"
        )

        # Terminal, not transient: give supervisord room to exhaust its
        # restart attempts and confirm the API never comes up.
        deadline = time.time() + 45
        while time.time() < deadline:
            assert not _reports_healthy(_BASE_URL), (
                "stack reported healthy despite an unsupported database lineage"
            )
            time.sleep(5.0)
