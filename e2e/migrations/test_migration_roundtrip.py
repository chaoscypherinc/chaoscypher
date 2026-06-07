# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Migration-roundtrip e2e smoke.

Boots a separate copy of the e2e stack pre-seeded with a snapshot
of ``app.db`` + ``credentials.json`` from a previous build, then
asserts:

  1. The container reaches healthy (Alembic ran cleanly against the
     snapshot — no FK violations, no missing tables, no destructive
     migration that refuses to boot).
  2. The pre-existing admin user can still log in with the same
     credentials (auth model survived migrations).
  3. The sources / templates list endpoints return without error
     (read paths still work against the snapshot's data shape).

The snapshot is committed at ``e2e/fixtures/snapshots/`` and
refreshed by ``e2e/fixtures/snapshots/refresh.py`` whenever a
migration legitimately changes the on-disk shape.

This test runs against a separate compose project
(``chaoscypher-e2e-snap`` on port 8889) so it doesn't collide with
the main e2e stack on port 8888. It manages its own container
lifecycle via ``docker compose`` subprocesses.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest


_PROJECT = "chaoscypher-e2e-snap"
_COMPOSE_FILE = (
    Path(__file__).parents[2]
    / "packages"
    / "docker"
    / "e2e"
    / "docker-compose.snapshot.yml"
)
_BASE_URL = "http://localhost:8889"

# The credentials baked into post-setup-app.db. See
# ``e2e/fixtures/snapshots/refresh.py``.
_SNAP_USERNAME = "e2e_admin"
_SNAP_PASSWORD = "E2eTestPass123"


def _have_docker() -> bool:
    return shutil.which("docker") is not None


pytestmark = pytest.mark.skipif(
    not _have_docker(),
    reason="docker not on PATH — migration-roundtrip test needs a runner with docker.",
)


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
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
        timeout=300,
        check=check,
    )


def _wait_healthy(url: str, timeout: int = 180) -> None:
    start = time.time()
    last_err: Exception | None = None
    while time.time() - start < timeout:
        try:
            resp = httpx.get(f"{url}/api/v1/health", timeout=3.0)
            if resp.status_code == 200 and resp.json().get("healthy"):
                return
        except Exception as e:
            last_err = e
        time.sleep(2.0)
    raise TimeoutError(
        f"snapshot stack did not become healthy within {timeout}s "
        f"(last_err={last_err!r})"
    )


@pytest.fixture(scope="module")
def snapshot_stack():
    """Bring the snapshot stack up; tear it down on module teardown.

    Cleaning the volume on both up + down isolates the test from any
    previous run and from the default e2e stack.
    """
    # Make sure no previous snap stack is half-running.
    _compose("down", "-v", check=False)
    try:
        _compose("up", "-d", "app")
    except subprocess.CalledProcessError as exc:
        pytest.fail(
            f"docker compose up failed: rc={exc.returncode}\n"
            f"stdout={exc.stdout}\nstderr={exc.stderr}"
        )

    try:
        _wait_healthy(_BASE_URL, timeout=180)
    except TimeoutError:
        # Surface container logs so a CI failure is debuggable.
        logs = subprocess.run(
            ["docker", "logs", "chaoscypher-e2e-snap-app"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout[-3000:]
        _compose("down", "-v", check=False)
        pytest.fail(f"snapshot stack failed to become healthy. Tail of logs:\n{logs}")

    yield _BASE_URL
    _compose("down", "-v", check=False)


def test_snapshot_boots_clean(snapshot_stack: str) -> None:
    """Container boots healthy with the pre-seeded snapshot.

    Implicit assertion via the fixture — health is the gate.
    """
    assert snapshot_stack == _BASE_URL


def test_snapshot_admin_can_log_in(snapshot_stack: str) -> None:
    """Credentials in the snapshot's auth.json still work post-migrations."""
    resp = httpx.post(
        f"{snapshot_stack}/api/v1/auth/login",
        json={"username": _SNAP_USERNAME, "password": _SNAP_PASSWORD},
        timeout=15.0,
    )
    assert resp.status_code == 200, resp.text
    assert resp.cookies.get("cc_session"), "no cc_session cookie returned"


def test_snapshot_sources_list_returns_envelope(snapshot_stack: str) -> None:
    """Sources list endpoint works against migrated snapshot data."""
    # Log in first to get a cookie.
    login = httpx.post(
        f"{snapshot_stack}/api/v1/auth/login",
        json={"username": _SNAP_USERNAME, "password": _SNAP_PASSWORD},
        timeout=15.0,
    )
    login.raise_for_status()
    cookie = login.cookies.get("cc_session")

    with httpx.Client(
        base_url=snapshot_stack,
        cookies={"cc_session": cookie},
        timeout=15.0,
    ) as client:
        resp = client.get("/api/v1/sources")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert "pagination" in body
