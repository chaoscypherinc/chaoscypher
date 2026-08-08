# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``dispose_all_engines`` must survive a single engine's dispose failure.

Pre-fix, the loop aborted on the first ``dispose()`` exception: remaining
engines kept their stale pooled connections alive and ``_engines.clear()``
never ran, so the cache silently disagreed with the caller's assumption that
every connection was closed (backup restore, database reset).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from chaoscypher_core.adapters.sqlite import engine as engine_module


def test_one_failing_dispose_does_not_abort_the_sweep() -> None:
    failing = MagicMock()
    failing.dispose.side_effect = RuntimeError("pool teardown failed")
    healthy = MagicMock()

    # Isolate the module cache for the test, then restore it.
    original = dict(engine_module._engines)
    engine_module._engines.clear()
    engine_module._engines["/tmp/fail/app.db"] = failing
    engine_module._engines["/tmp/ok/app.db"] = healthy
    try:
        engine_module.dispose_all_engines()

        failing.dispose.assert_called_once()
        healthy.dispose.assert_called_once()
        assert engine_module._engines == {}
    finally:
        engine_module._engines.clear()
        engine_module._engines.update(original)
