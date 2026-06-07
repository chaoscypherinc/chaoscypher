# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Drain-iteration injection for ``OllamaLoadBalancer``.

Verifies the Tier-2 settings-injection contract:

* ``OllamaLoadBalancer(drain_max_iterations=N)`` stores ``N`` as the drain
  ceiling without reading any app-config singleton.
* ``OllamaLoadBalancer()`` (no args) falls back to the
  ``RetrySettings`` class default (10).
* ``configure_drain`` still overrides whatever the constructor seeded.

These tests run the *real* ``__init__`` (no ``__new__`` bypass) so a
regression that re-introduces a ``get_settings()`` read at construction
time would surface here.
"""

from __future__ import annotations

from chaoscypher_core.adapters.llm.load_balancer import OllamaLoadBalancer
from chaoscypher_core.settings import RetrySettings


def test_default_constructor_uses_retry_settings_default() -> None:
    """No-arg construction resolves to RetrySettings().ollama_drain_max_iterations."""
    bal = OllamaLoadBalancer()
    assert bal._drain_max_wait == RetrySettings().ollama_drain_max_iterations
    assert bal._drain_max_wait == 10  # current default, pinned for clarity


def test_injected_drain_max_iterations_is_stored() -> None:
    """An explicit drain_max_iterations overrides the RetrySettings default."""
    bal = OllamaLoadBalancer(drain_max_iterations=3)
    assert bal._drain_max_wait == 3


def test_injected_zero_is_not_treated_as_unset() -> None:
    """Passing 0 must be honored, not silently replaced by the default.

    (None means 'use default'; 0 is an explicit, if degenerate, override.)
    """
    bal = OllamaLoadBalancer(drain_max_iterations=0)
    assert bal._drain_max_wait == 0


def test_none_falls_back_to_default() -> None:
    """Explicit None behaves identically to omitting the argument."""
    bal = OllamaLoadBalancer(drain_max_iterations=None)
    assert bal._drain_max_wait == RetrySettings().ollama_drain_max_iterations


def test_configure_drain_still_overrides_constructor_value() -> None:
    """configure_drain replaces the constructor-seeded drain ceiling."""
    bal = OllamaLoadBalancer(drain_max_iterations=7)
    bal.configure_drain(max_wait=99, check_interval=0.25)
    assert bal._drain_max_wait == 99
    assert bal._drain_check_interval == 0.25
