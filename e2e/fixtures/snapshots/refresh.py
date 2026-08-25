# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regenerate the e2e snapshot fixture. DO NOT RUN CASUALLY.

The committed snapshot at ``e2e/fixtures/snapshots/`` is a database from
*before* the 2026-06-02 migration squash — it is stamped at ``0044``, a
revision the package no longer ships. That staleness is the point:
``e2e/migrations/test_migration_roundtrip.py`` mounts it to prove startup
refuses an unsupported pre-squash lineage with a guided error. Running
this script would replace it with a current-head database and quietly
gut that test, so the script refuses to run without ``--force``.

There is no longer a schema change that *wants* a refresh. Use this only
to build a snapshot for a genuinely new fixture path — and commit it
under a new filename rather than overwriting ``post-setup-app.db``.

  uv run python e2e/fixtures/snapshots/refresh.py --force

Steps the script performs:

  1. Bring up the default e2e stack on a clean volume.
  2. POST /api/v1/auth/setup with the canonical admin creds.
  3. WAL-checkpoint app.db inside the container.
  4. Copy app.db + credentials.json out into this directory.
  5. Tear the stack down.

Note the output paths below still point at ``post-setup-app.db`` /
``credentials.json``. Change them before committing anything this script
produces — overwriting the committed pre-squash pair is the one outcome
the ``--force`` guard exists to prevent.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


_SNAPSHOT_DIR = Path(__file__).parent
_COMPOSE_DIR = Path(__file__).parents[2] / "packages" / "docker" / "e2e"
_COMPOSE_FILE = _COMPOSE_DIR / "docker-compose.yml"
_PROJECT = "chaoscypher-e2e"
_APP_CONTAINER = "chaoscypher-e2e-app"
_BASE_URL = "http://localhost:8888"

_ADMIN_USERNAME = "e2e_admin"
_ADMIN_PASSWORD = "E2eTestPass123"
_ADMIN_EMAIL = "snapshot@e2e-test.example.com"


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(args)}", flush=True)
    return subprocess.run(args, check=True, **kwargs)  # noqa: S603 — argv is built here, not user input


def _compose(*args: str) -> subprocess.CompletedProcess:
    return _run(
        ["docker", "compose", "-f", str(_COMPOSE_FILE), "--project-name", _PROJECT, *args],
    )


def _wait_healthy(timeout: int = 180) -> None:
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(  # noqa: S310 — hardcoded http://localhost
                f"{_BASE_URL}/api/v1/health", timeout=3
            ) as resp:
                if resp.status == 200 and json.loads(resp.read()).get("healthy"):
                    return
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):  # fmt: skip
            pass
        time.sleep(2.0)
    raise TimeoutError(f"app not healthy within {timeout}s")


def main() -> int:
    if "--force" not in sys.argv:
        print(
            "Refusing to run: the committed snapshot is a deliberately "
            "pre-squash database that e2e/migrations/ depends on. Re-read this "
            "script's docstring, then pass --force if you really mean it.",
            file=sys.stderr,
            flush=True,
        )
        return 1

    print("Regenerating snapshot...", flush=True)
    print(f"  output dir: {_SNAPSHOT_DIR}", flush=True)

    # Clean slate.
    _compose("down", "-v")
    _compose("up", "-d", "app")
    print("Waiting for app to become healthy...", flush=True)
    _wait_healthy()

    # Do canonical setup.
    print("Running /auth/setup...", flush=True)
    req = urllib.request.Request(  # noqa: S310 — hardcoded http://localhost
        f"{_BASE_URL}/api/v1/auth/setup",
        data=json.dumps(
            {
                "username": _ADMIN_USERNAME,
                "password": _ADMIN_PASSWORD,
                "email": _ADMIN_EMAIL,
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — hardcoded http://localhost
        if resp.status not in (200, 201):
            raise RuntimeError(f"setup returned {resp.status}: {resp.read()[:200]!r}")

    # WAL checkpoint so app.db is self-contained (no -wal / -shm needed).
    print("WAL checkpointing app.db...", flush=True)
    _run(
        [
            "docker",
            "exec",
            _APP_CONTAINER,
            "python3",
            "-c",
            (
                "import sqlite3; "
                "c = sqlite3.connect('/data/databases/default/app.db'); "
                "c.execute('PRAGMA wal_checkpoint(TRUNCATE);'); "
                "c.close()"
            ),
        ]
    )

    # Copy out.
    print("Copying snapshot files out...", flush=True)
    _run(
        [
            "docker",
            "cp",
            f"{_APP_CONTAINER}:/data/databases/default/app.db",
            str(_SNAPSHOT_DIR / "post-setup-app.db"),
        ]
    )
    _run(
        [
            "docker",
            "cp",
            f"{_APP_CONTAINER}:/data/credentials.json",
            str(_SNAPSHOT_DIR / "credentials.json"),
        ]
    )

    # Clean up.
    _compose("down", "-v")

    print("Done. Snapshot regenerated:", flush=True)
    for f in sorted(_SNAPSHOT_DIR.glob("*")):
        if f.suffix in {".db", ".json"}:
            print(f"  {f.name} ({f.stat().st_size} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
