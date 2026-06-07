# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for HandlerSpec dataclass."""

from chaoscypher_core.queue.handler_spec import (
    HandlerSpec,
    normalize_handler,
)


async def _dummy_handler(*args, **kwargs):
    return "ok"


def test_handler_spec_default_retry_off() -> None:
    spec = HandlerSpec(handler=_dummy_handler)
    assert spec.retry_on_crash is False
    assert spec.handler is _dummy_handler


def test_handler_spec_retry_on() -> None:
    spec = HandlerSpec(handler=_dummy_handler, retry_on_crash=True)
    assert spec.retry_on_crash is True


def test_normalize_wraps_bare_callable() -> None:
    spec = normalize_handler(_dummy_handler)
    assert isinstance(spec, HandlerSpec)
    assert spec.handler is _dummy_handler
    assert spec.retry_on_crash is False


def test_normalize_passes_through_existing_spec() -> None:
    original = HandlerSpec(handler=_dummy_handler, retry_on_crash=True)
    spec = normalize_handler(original)
    assert spec is original
