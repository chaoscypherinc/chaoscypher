# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Atomic owner-only file writes for secret material.

The canonical fix for the write-then-chmod anti-pattern: ``mkstemp``
creates the tempfile with mode 0600 from the start, so the secret is
never world-readable — not even for the window between file creation
and a later ``chmod`` (a plain ``write_bytes``/``write_text`` creates
the file under the process umask, typically 0644). The final rename is
atomic, so a concurrent reader never observes a partial file.

Extracted 2026-08-16 from the two private copies that already did this
correctly (``services/tls/service._write_private_key`` and
``services/local_auth/credentials._atomic_write``), which now delegate
here.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_secret_write(path: Path, data: bytes | str, *, prefix: str = ".secret_") -> None:
    """Write ``data`` to ``path`` atomically with owner-only permissions.

    Args:
        path: Final destination. The parent directory must exist.
        data: Secret payload; ``str`` is encoded as UTF-8.
        prefix: Tempfile prefix, kept distinctive per call site so an
            interrupted write is attributable on inspection.

    """
    payload = data.encode("utf-8") if isinstance(data, str) else data
    fd, tmp_path_str = tempfile.mkstemp(prefix=prefix, dir=str(path.parent))
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        if os.name == "posix":
            tmp_path.chmod(0o600)
        # ``os.replace`` (not ``Path.replace``) is intentional: tests
        # monkeypatch the global to simulate a failed rename, and
        # ``Path.replace`` would bypass those patches.
        os.replace(tmp_path, path)  # noqa: PTH105 — see comment above
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
