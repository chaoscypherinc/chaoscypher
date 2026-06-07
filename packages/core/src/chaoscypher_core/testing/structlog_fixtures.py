# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared structlog fixtures for cross-package tests.

The default structlog configuration uses ``PrintLogger`` and emits to
stdout via its own renderer chain. ``caplog`` captures via the stdlib
``logging`` module, so structlog events are invisible to it unless the
config wires structlog through stdlib.

Five test files in the core package previously each defined a local
``_configure_structlog_for_caplog()`` helper that mutated the global
structlog config without restoring it. Under ``pytest-xdist`` the
worker-process lifetime spans many tests, so the polluted singleton
breaks tests later in the same worker and accidentally makes other
tests pass by leaking the stdlib bridge they actually need.

The fixture below:

1. Clears any per-proxy ``BoundLogger`` caches left over from earlier
   tests (path B in the project's xdist-isolation investigation notes).
2. Re-enables any stdlib logger an earlier test disabled (path C —
   Alembic ``env.py``'s ``fileConfig`` defaults to
   ``disable_existing_loggers=True``, which silently mutes module loggers
   so the bridged structlog event never reaches ``caplog``).
3. Snapshots the current structlog config.
4. Applies the bridge config so structlog events flow into stdlib (and
   therefore into ``caplog``). Forces ``cache_logger_on_first_use=False``
   while the fixture is active so module-level ``logger =
   structlog.get_logger(__name__)`` calls touched during the test body
   don't cache a bridge-wired ``BoundLogger`` that would survive the
   teardown.
4. On teardown: restores the snapshot AND clears per-proxy caches once
   more so any cache the test body may have produced doesn't pollute
   subsequent tests in the same xdist worker.

Tests that need to assert on log lines via ``caplog`` request this
fixture. Tests that don't touch ``caplog`` should NOT request it — they
work with the default structlog config.
"""

from __future__ import annotations

import logging as _stdlib_logging
import sys
from collections.abc import Generator

import pytest
import structlog
import structlog._config as _structlog_config


def _reenable_disabled_stdlib_loggers() -> None:
    """Clear the ``disabled`` flag on every existing stdlib logger.

    Path C (xdist isolation): Alembic's migration ``env.py`` calls
    ``logging.config.fileConfig(...)`` at import time, which defaults to
    ``disable_existing_loggers=True``. That sets ``Logger.disabled = True``
    on EVERY logger that already exists in ``logging.Logger.manager`` —
    including module-level loggers like
    ``chaoscypher_core.operations.importing.import_service`` once an earlier
    test has instantiated them. A disabled stdlib logger silently drops
    every record before it can propagate to ``caplog``'s capture handler,
    so a structlog event routed through the stdlib bridge (the config this
    fixture installs) emits into a void: ``caplog`` sees nothing even
    though the proxy/config are otherwise correct.

    Re-enabling all existing loggers at fixture setup is safe: ``disabled``
    only ever suppresses output, so clearing it can never make another test
    miss a log it expected to drop. This complements the lazy-proxy cache
    sweep below — together they cover both ways a prior test can render a
    module logger invisible to ``caplog``.
    """
    manager = _stdlib_logging.Logger.manager
    for logger in list(manager.loggerDict.values()):
        # ``loggerDict`` holds ``Logger`` and ``PlaceHolder`` instances;
        # only real ``Logger`` objects carry a ``disabled`` flag.
        if isinstance(logger, _stdlib_logging.Logger) and logger.disabled:
            logger.disabled = False


def _clear_bound_logger_lazy_proxy_caches() -> None:
    """Drop any per-proxy ``bind`` overrides cached on module-level loggers.

    ``BoundLoggerLazyProxy`` implements ``cache_logger_on_first_use``
    by monkey-patching ``self.bind`` on the proxy instance the first
    time the proxy is used. The cached closure captures the
    ``wrapper_class`` / ``processors`` / ``logger_factory`` that were
    active at first-use time, so changing the global ``_CONFIG`` later
    has no effect on a logger that's already cached. ``structlog`` has
    no public API to invalidate that cache, so we walk ``sys.modules``
    and clear the instance-level ``bind`` attribute on every proxy we
    find. This is cheap (one ``isinstance`` per module attribute and a
    single ``__dict__`` lookup per matching proxy) and only runs when
    a test explicitly requests this fixture — i.e., never on the hot
    path of tests that don't touch ``caplog``.
    """
    proxy_cls = _structlog_config.BoundLoggerLazyProxy
    # Snapshot ``sys.modules`` to avoid "dict changed size during iteration"
    # if a logger emission during teardown triggers a deferred import.
    # ``sys.modules`` values are typed ``ModuleType`` in stubs but can
    # be ``None`` in practice (Python uses None as a sentinel for failed
    # imports), so we tolerate either via ``getattr`` for ``__dict__``.
    for module in list(sys.modules.values()):
        module_dict = getattr(module, "__dict__", None)
        if module_dict is None:
            continue
        # Snapshot attribute names too — a getattr below may import a
        # submodule and mutate ``module_dict`` mid-iteration.
        for attr_name in list(module_dict):
            value = module_dict.get(attr_name)
            if isinstance(value, proxy_cls) and "bind" in value.__dict__:
                # The cache lives at the instance level (closure assignment
                # in BoundLoggerLazyProxy.bind). Deleting the instance
                # attribute restores method resolution to the class-level
                # bind, so the next call re-assembles a fresh BoundLogger
                # from the current _CONFIG.
                del value.__dict__["bind"]


@pytest.fixture
def structlog_for_caplog() -> Generator[None]:
    """Route structlog events through stdlib for the duration of one test.

    Clears leaked ``BoundLoggerLazyProxy`` caches on both setup and
    teardown so xdist workers (which span many tests in one process)
    don't accumulate leaked state. Snapshots the pre-test config and
    restores it on teardown. Tests must explicitly request this fixture
    rather than relying on a global config leak.
    """
    # Path B (xdist isolation): clear any cached BoundLogger that an
    # earlier test left behind, otherwise this test's structlog config
    # changes are invisible to module-level loggers that already cached
    # a wrapper class against the previous config.
    _clear_bound_logger_lazy_proxy_caches()

    # Path C (xdist isolation): re-enable any stdlib logger that an earlier
    # test disabled (e.g. Alembic's env.py fileConfig with the default
    # disable_existing_loggers=True). A disabled logger silently drops the
    # bridged structlog event so caplog captures nothing.
    _reenable_disabled_stdlib_loggers()

    snapshot = structlog.get_config()
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        # Force caching off for the duration of the fixture so the test
        # body can't create a new cached BoundLogger that would survive
        # the snapshot restore on teardown. Without this, a previous
        # test that flipped cache_logger_on_first_use=True (captured in
        # the snapshot above) would leak its setting back through.
        cache_logger_on_first_use=False,
    )
    try:
        yield
    finally:
        structlog.configure(**snapshot)
        # Even with cache_logger_on_first_use=False during the fixture
        # body, the snapshot's cache flag may have been True and the
        # restore re-enables it for subsequent tests. Any proxy the
        # test body bound while the bridge was active is still pointing
        # at the bridge's wrapper_class/processors. Clear them so the
        # next test sees a clean slate.
        _clear_bound_logger_lazy_proxy_caches()
