# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the shared structlog-for-caplog fixture."""

from __future__ import annotations

import logging
import sys
import types

import pytest
import structlog
import structlog._config as _structlog_config

from chaoscypher_core.testing.structlog_fixtures import structlog_for_caplog


def test_structlog_for_caplog_routes_to_stdlib(
    structlog_for_caplog: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A structlog WARN emitted while the fixture is active reaches caplog."""
    logger = structlog.get_logger("test_module")
    with caplog.at_level(logging.WARNING):
        logger.warning("test_event", key="value")
    combined = " ".join(r.getMessage() for r in caplog.records)
    assert "test_event" in combined


def test_structlog_for_caplog_setup_teardown_round_trip() -> None:
    """Driving the fixture manually round-trips structlog config.

    Records the pre-yield config, asserts the mid-yield config is the
    stdlib bridge, then asserts the post-teardown config matches
    pre-yield exactly. This is the only test that actually proves
    the finally block runs.

    A known non-BoundLogger wrapper is installed before driving the
    fixture so the test is not fooled by leaked state from earlier
    tests in the same process.
    """
    # Establish a known non-BoundLogger sentinel so the mid-yield and
    # post-teardown assertions are meaningful regardless of prior test order.
    sentinel_wrapper = structlog.BoundLogger  # stdlib-free default wrapper class
    structlog.configure(wrapper_class=sentinel_wrapper)

    # The pytest fixture decorator wraps the generator; unwrap to drive manually.
    gen = structlog_for_caplog.__wrapped__()  # type: ignore[attr-defined]  # pytest.fixture uses functools.wraps; __wrapped__ is set at runtime but absent from stubs

    pre_config = structlog.get_config()
    pre_wrapper = pre_config["wrapper_class"]
    assert pre_wrapper is sentinel_wrapper, (
        "precondition: sentinel wrapper should be active before entering fixture"
    )

    next(gen)  # enter the fixture (apply the stdlib bridge)

    mid_config = structlog.get_config()
    assert mid_config["wrapper_class"] is structlog.stdlib.BoundLogger, (
        "fixture did not apply the stdlib-bridge BoundLogger wrapper"
    )

    # Drive teardown by advancing the generator past its yield.
    with pytest.raises(StopIteration):
        next(gen)

    post_config = structlog.get_config()
    assert post_config["wrapper_class"] is sentinel_wrapper, (
        "fixture did not restore the pre-test wrapper_class on teardown"
    )


def test_structlog_for_caplog_clears_lazy_proxy_cache_on_teardown() -> None:
    """Path B (xdist isolation): a BoundLoggerLazyProxy cached during the
    fixture body must NOT survive teardown.

    The fixture forces ``cache_logger_on_first_use=False`` during its
    body, but a proxy that was already cached BEFORE the fixture ran
    (e.g., by a previous test with caching enabled) is the leak we
    have to clear. Drive the fixture manually with a known cached
    proxy installed in ``sys.modules`` and assert that its cached
    ``bind`` attribute is gone after teardown.
    """
    # Set up production-style caching, then capture a proxy and force
    # it to cache itself (so its instance dict gets a ``bind`` entry).
    structlog.configure(
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    proxy = structlog.get_logger("test.path_b_cache.module")
    assert isinstance(proxy, _structlog_config.BoundLoggerLazyProxy)
    proxy.bind()  # first use → instance-level ``bind`` is installed
    assert "bind" in proxy.__dict__, (
        "precondition: proxy must be cached for the test to be meaningful"
    )

    # Park the proxy on a real sys.modules entry so the fixture's
    # walker can find it the same way it would find a module-level
    # ``logger = structlog.get_logger(__name__)``.
    fake_module_name = "test_structlog_fixtures_path_b_probe"
    fake_module = types.ModuleType(fake_module_name)
    fake_module.logger = proxy  # type: ignore[attr-defined]
    sys.modules[fake_module_name] = fake_module

    try:
        gen = structlog_for_caplog.__wrapped__()  # type: ignore[attr-defined]  # pytest.fixture sets __wrapped__ at runtime
        next(gen)  # enter fixture (setup clears, then bridge config)
        # The setup-time clear should have already invalidated our cache.
        assert "bind" not in proxy.__dict__, (
            "fixture setup did not clear the pre-existing cached proxy bind"
        )

        # Inside the fixture body, cache_logger_on_first_use must be
        # False so any new ``bind()`` calls don't re-cache.
        mid_config = structlog.get_config()
        assert mid_config["cache_logger_on_first_use"] is False
        proxy.bind()  # would re-cache if caching were still on
        assert "bind" not in proxy.__dict__, (
            "cache_logger_on_first_use was not forced off during the fixture body"
        )

        with pytest.raises(StopIteration):
            next(gen)  # teardown (snapshot restore + clear caches again)

        # After teardown the snapshot restored cache_logger_on_first_use=True,
        # but any proxy that the fixture body caused to cache should have
        # been swept again by the teardown clear.
        assert "bind" not in proxy.__dict__, (
            "fixture teardown did not clear caches set during the test body"
        )
    finally:
        sys.modules.pop(fake_module_name, None)
        structlog.reset_defaults()
