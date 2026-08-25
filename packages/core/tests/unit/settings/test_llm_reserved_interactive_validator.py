# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``LLMSettings`` must clamp ``llm_reserved_interactive < llm_max_concurrent``, not reject it.

An earlier version of this validator raised ``ValidationError`` on
``llm_reserved_interactive >= llm_max_concurrent``. That is the only thing
that can *brick* a previously-working deployment here: ``PrioritySemaphore``
already clamps its own ``reserved_high_priority`` defensively (see
``adapters/llm/limit.py``), so the bad config only ever starved background
work at runtime — it never crashed. But ``Settings.load_from_yaml`` builds
``LLMSettings(**llm_data)`` with no surrounding ``try``/``except``
(``app_config/__init__.py``), so the raise turned a merely-starved,
running deployment into one that would not start at all — including the
``max_concurrent=1, reserved_high_priority=1`` example this settings
module's own docstring taught until it was corrected. The validator now
clamps and warns instead, matching the semaphore's own runtime clamp and
the UI's clamp (``VRAMPresets.tsx``, ``maxReserved = maxConcurrent - 1``).
"""

from __future__ import annotations

import structlog

from chaoscypher_core.settings import LLMSettings


def test_reserved_equal_to_max_concurrent_is_clamped_and_warns() -> None:
    """``reserved == max_concurrent`` must be normalized down, not raise."""
    with structlog.testing.capture_logs() as logs:
        settings = LLMSettings(llm_max_concurrent=2, llm_reserved_interactive=2)

    assert settings.llm_reserved_interactive == 1
    assert any(entry.get("event") == "llm_reserved_interactive_clamped" for entry in logs)


def test_reserved_greater_than_max_concurrent_is_clamped_and_warns() -> None:
    """A reserved value further above ``max_concurrent`` still clamps to ``max_concurrent - 1``."""
    with structlog.testing.capture_logs() as logs:
        settings = LLMSettings(llm_max_concurrent=2, llm_reserved_interactive=5)

    assert settings.llm_reserved_interactive == 1
    assert any(entry.get("event") == "llm_reserved_interactive_clamped" for entry in logs)


def test_reserved_equal_to_max_concurrent_one_slot_clamps_to_zero() -> None:
    """The single-slot case (``max_concurrent=1``) must clamp to 0, matching the semaphore floor."""
    settings = LLMSettings(llm_max_concurrent=1, llm_reserved_interactive=1)
    assert settings.llm_reserved_interactive == 0


def test_reserved_below_max_concurrent_is_accepted_unchanged() -> None:
    """A valid config passes through untouched and logs no clamp warning."""
    with structlog.testing.capture_logs() as logs:
        settings = LLMSettings(llm_max_concurrent=4, llm_reserved_interactive=1)

    assert settings.llm_reserved_interactive == 1
    assert not any(entry.get("event") == "llm_reserved_interactive_clamped" for entry in logs)


def test_default_settings_pass_validation_unchanged() -> None:
    """The shipped defaults (max_concurrent=1, reserved_interactive=0) must not regress."""
    settings = LLMSettings()
    assert settings.llm_reserved_interactive < settings.llm_max_concurrent
