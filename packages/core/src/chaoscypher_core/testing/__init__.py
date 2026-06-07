# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Test utilities exported by chaoscypher-core for use across all packages.

This module is for **test infrastructure only** — production code must not
import from here. The structlog fixtures live here (not in a tests/conftest.py)
because pytest's conftest discovery doesn't cross package boundaries.

xdist-isolation note (May 2026)
-------------------------------
Importing ``chaoscypher_cortex.boot`` calls ``configure_logging()`` at
module-load time, which switches structlog to ``wrapper_class=
BoundLogger`` + ``cache_logger_on_first_use=True``. Once that flips in
an xdist worker process, module-level
``logger = structlog.get_logger(__name__)`` calls resolve to cached
stdlib-bridge ``BoundLogger`` instances — these route log events
through stdlib logging, NOT through the processor list that
``structlog.testing.capture_logs()`` mutates. Tests asserting on
``capture_logs`` entries then see an empty list even though the log
*was* emitted (visible in pytest's ``Captured stdout`` and
``Captured log`` sections).

The ``configure_logging`` idempotency guard is keyed on
``logging._chaoscypher_logging_configured``. Setting that attribute
BEFORE any conftest imports cortex / neuron transitively keeps
structlog in its default ``BoundLoggerLazyProxy`` configuration, which
is what ``capture_logs`` is designed to intercept. Tests that
explicitly need the stdlib bridge (so ``caplog`` can capture structlog
events) still request the per-test ``structlog_for_caplog`` fixture
below, which snapshots / configures / restores around the test body.

This pre-empt runs at conftest-load time because every per-package
conftest imports from this module to expose ``structlog_for_caplog``
as a fixture — so the guard flag lands before any cortex import in
the worker. The full investigation (residual failure modes and
follow-up work) is recorded in the project's xdist-isolation notes.
"""

from __future__ import annotations

import logging as _stdlib_logging


# Pre-empt the configure_logging() idempotency guard so test workers
# observe structlog's default capture-friendly configuration regardless
# of which packages import cortex / neuron during a worker's lifetime.
# The guard is implemented as a sentinel attribute on the stdlib
# ``logging`` module (a private-by-convention test contract — the
# attribute name carries the project prefix so this is unambiguous).
# See module docstring for the xdist-isolation context.
_stdlib_logging._chaoscypher_logging_configured = True  # type: ignore[attr-defined]  # noqa: SLF001 - intentional test-time pre-empt of configure_logging() guard


from chaoscypher_core.testing.structlog_fixtures import (
    structlog_for_caplog,
)


__all__ = ["structlog_for_caplog"]
