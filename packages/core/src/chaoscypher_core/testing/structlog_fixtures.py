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
from collections.abc import Generator, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
import structlog
import structlog._config as _structlog_config


if TYPE_CHECKING:
    from types import ModuleType


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


# Module -> the lazy proxies among its attributes, found the first time the module is seen.
_proxies_by_module: dict[str, tuple[ModuleType, tuple[Any, ...]]] = {}


def _module_level_proxies() -> Iterator[Any]:
    """Yield every ``BoundLoggerLazyProxy`` held as a module attribute in ``sys.modules``.

    A module is scanned once, the first time it is seen (again if its name now holds another
    module object), so the per-test sweep costs a pass over ``sys.modules`` and not over every
    module's attributes: the full walk took 11 ms with cortex loaded, on every test. ``type() is``
    rather than ``isinstance`` so a lazy module attribute (dynaconf's settings) is not woken.
    """
    proxy_cls = _structlog_config.BoundLoggerLazyProxy
    # Snapshot ``sys.modules``: scanning a module may import another.
    for name, module in list(sys.modules.items()):
        seen = _proxies_by_module.get(name)
        if seen is None or seen[0] is not module:
            # ``sys.modules`` values are typed ``ModuleType`` in stubs but can be ``None`` in
            # practice (a failed import's sentinel), so ``__dict__`` is read with ``getattr``.
            module_dict = getattr(module, "__dict__", None) or {}
            found = tuple(v for v in list(module_dict.values()) if type(v) is proxy_cls)
            seen = (module, found)
            # A module still importing (on another thread) may not have its logger yet.
            if not getattr(getattr(module, "__spec__", None), "_initializing", False):
                _proxies_by_module[name] = seen
        yield from seen[1]


def _clear_bound_logger_lazy_proxy_caches() -> None:
    """Drop any per-proxy ``bind`` overrides cached on module-level loggers.

    ``BoundLoggerLazyProxy`` implements ``cache_logger_on_first_use``
    by monkey-patching ``self.bind`` on the proxy instance the first
    time the proxy is used. The cached closure captures the
    ``wrapper_class`` / ``processors`` / ``logger_factory`` that were
    active at first-use time, so changing the global ``_CONFIG`` later
    has no effect on a logger that's already cached. ``structlog`` has
    no public API to invalidate that cache, so we find every module-level
    proxy (:func:`_module_level_proxies`) and clear its instance-level
    ``bind``: method resolution then falls back to the class-level
    ``bind``, and the next call assembles a fresh ``BoundLogger`` from the
    current ``_CONFIG``.
    """
    for proxy in _module_level_proxies():
        proxy.__dict__.pop("bind", None)


def use_plain_tracebacks() -> None:
    """Keep structlog's processor chain, but render a logged exception as a plain traceback.

    Unconfigured, structlog's ``ConsoleRenderer`` formats exceptions with ``rich`` when it is
    installed: every frame syntax-highlighted (pygments) with its local variables printed.
    Tests run on that default (see the package docstring), and the code logs the exceptions it
    handles, so a retrieval recipe test spent 106 of its 120 seconds drawing tracebacks nobody
    read, with locals holding whole graphs (2026-10-16). Production logging already uses
    ``plain_traceback`` (``utils.logging.config.configure_logging``). Only a
    ``ConsoleRenderer`` is replaced; any other renderer is left as configured.
    """
    structlog.configure(
        processors=[
            structlog.dev.ConsoleRenderer(exception_formatter=structlog.dev.plain_traceback)
            if isinstance(processor, structlog.dev.ConsoleRenderer)
            else processor
            for processor in structlog.get_config()["processors"]
        ]
    )


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


def _is_pytests(handler: _stdlib_logging.Handler) -> bool:
    """Whether pytest's logging plugin owns ``handler`` (it adds and removes its own per phase)."""
    return type(handler).__module__.startswith("_pytest")


@dataclass(frozen=True)
class _LoggingState:
    """What a test can change about logging, process-wide, without meaning to keep it."""

    structlog_config: dict[str, Any]
    contextvars: dict[str, Any]
    root_level: int
    root_handlers: tuple[_stdlib_logging.Handler, ...]
    disable_level: int
    disabled_loggers: frozenset[str]


def _stdlib_loggers() -> Iterator[tuple[str, _stdlib_logging.Logger]]:
    # ``loggerDict`` holds ``Logger`` and ``PlaceHolder`` instances; only loggers can be disabled.
    for name, logger in list(_stdlib_logging.Logger.manager.loggerDict.items()):
        if isinstance(logger, _stdlib_logging.Logger):
            yield name, logger


def _logging_state() -> _LoggingState:
    config = structlog.get_config()
    root = _stdlib_logging.getLogger()
    return _LoggingState(
        # A copy: ``capture_logs`` edits the configured processor list in place.
        structlog_config={**config, "processors": list(config["processors"])},
        contextvars=structlog.contextvars.get_contextvars(),
        root_level=root.level,
        root_handlers=tuple(h for h in root.handlers if not _is_pytests(h)),
        disable_level=_stdlib_logging.root.manager.disable,
        disabled_loggers=frozenset(name for name, lg in _stdlib_loggers() if lg.disabled),
    )


def _restore_logging_state(state: _LoggingState) -> None:
    """Put back ``state``, leaving pytest's own capture handlers where they are."""
    if structlog.get_config() != state.structlog_config:
        structlog.configure(
            **{**state.structlog_config, "processors": list(state.structlog_config["processors"])}
        )
    # A proxy cached during the test holds the config it was first used under, even when the
    # test put the config itself back (``cache_logger_on_first_use`` and no invalidation API).
    _clear_bound_logger_lazy_proxy_caches()
    if structlog.contextvars.get_contextvars() != state.contextvars:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(**state.contextvars)
    root = _stdlib_logging.getLogger()
    if root.level != state.root_level:  # each set clears every logger's level cache
        root.setLevel(state.root_level)
    for handler in list(root.handlers):
        if not _is_pytests(handler) and handler not in state.root_handlers:
            root.removeHandler(handler)  # not closed: its stream may belong to someone else
    for handler in state.root_handlers:
        if handler not in root.handlers:
            root.addHandler(handler)
    if _stdlib_logging.root.manager.disable != state.disable_level:
        _stdlib_logging.disable(state.disable_level)
    for name, logger in _stdlib_loggers():
        logger.disabled = name in state.disabled_loggers


@pytest.fixture(autouse=True)
def logging_left_as_found() -> Iterator[None]:
    """Put structlog and stdlib logging back as they were before the test, whatever it did.

    Logging is process-wide and an xdist worker runs thousands of tests, so a change one test
    leaves behind decides what a later one, sharing the worker by the luck of the shard packing,
    can see. On 2026-10-26 a structlog test left the stdlib bridge on with caching (and the
    engine module's logger cached against it), and an in-process Alembic run left every existing
    logger disabled; together they swallowed ``test_schema_updates.py``'s drift warning in CI.
    Restored after each test: the structlog configuration and the loggers cached under it, the
    structlog contextvars, the root logger's level and handlers (not pytest's), ``logging.disable``
    and each logger's ``disabled`` flag. Every package's test ``conftest.py`` imports this.
    """
    state = _logging_state()
    yield
    _restore_logging_state(state)
