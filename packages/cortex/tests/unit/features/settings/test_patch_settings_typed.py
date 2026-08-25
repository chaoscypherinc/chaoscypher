# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for PATCH /settings typed-body validation.

The DTO's allowlist is derived from ``Settings.model_fields`` so it
cannot drift from the real model. These tests assert:

- Any real top-level Settings key is accepted.
- Unknown keys (typos, stale references from old refactors) are rejected.
- ``model_dump(exclude_unset=True, exclude_none=True)`` still produces a
  sparse dict for ``ConfigManager.update_settings``.
- A round-trip from ``GET /settings`` (whole object) validates — this is
  the frontend pattern that used to 422.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from chaoscypher_core.app_config import Settings
from chaoscypher_cortex.features.settings.models import SettingsUpdateRequest


def test_rejects_unknown_top_level_key() -> None:
    """Unknown keys fail validation with an actionable message."""
    with pytest.raises(ValidationError) as excinfo:
        SettingsUpdateRequest.model_validate({"unknown_key": "oops"})

    assert "unknown_key" in str(excinfo.value)


def test_accepts_known_nested_group() -> None:
    """A partial update with a real group is accepted and preserved."""
    req = SettingsUpdateRequest.model_validate({"llm": {"chat_provider": "ollama"}})
    dumped = req.model_dump(exclude_none=True, exclude_unset=True)
    assert dumped == {"llm": {"chat_provider": "ollama"}}


def test_accepts_local_auth_group() -> None:
    """local_auth is the real group name (not the stale 'auth')."""
    req = SettingsUpdateRequest.model_validate({"local_auth": {"cookie_secure": True}})
    dumped = req.model_dump(exclude_none=True, exclude_unset=True)
    assert dumped == {"local_auth": {"cookie_secure": True}}


def test_rejects_stale_auth_group() -> None:
    """'auth' was renamed to 'local_auth'; the old name must be rejected."""
    with pytest.raises(ValidationError) as excinfo:
        SettingsUpdateRequest.model_validate({"auth": {"enabled": False}})
    assert "auth" in str(excinfo.value)


def test_rejects_top_level_enable_auto_embedding() -> None:
    """This field lives under ``search``; the top-level alias was stale."""
    with pytest.raises(ValidationError):
        SettingsUpdateRequest.model_validate({"enable_auto_embedding": True})


def test_typo_in_group_name_rejected() -> None:
    """'llmm' (typo of 'llm') is rejected, not silently ignored."""
    with pytest.raises(ValidationError):
        SettingsUpdateRequest.model_validate({"llmm": {"chat_provider": "ollama"}})


def test_accepts_full_settings_round_trip() -> None:
    """Frontend sends the whole GET /settings payload back on save.

    Every real top-level Settings key must be accepted so the round-trip
    doesn't 422. Regression test for the Models-tab save failure.
    Security-protected keys (see below) are silently stripped, not 422'd,
    so the full-object save keeps working.
    """
    protected = {"dev_mode"}
    full_dump = Settings().model_dump()
    req = SettingsUpdateRequest.model_validate(full_dump)
    dumped = req.model_dump(exclude_none=True, exclude_unset=True)
    # Every key we sent should come back (minus None values and protected keys).
    for key in full_dump:
        if full_dump[key] is not None and key not in protected:
            assert key in dumped, f"Round-trip lost key: {key}"


def test_dev_mode_is_stripped_not_applied() -> None:
    """dev_mode must not be settable via the API (disables auth + bricks restart)."""
    req = SettingsUpdateRequest.model_validate(
        {"dev_mode": True, "llm": {"chat_provider": "ollama"}}
    )
    dumped = req.model_dump(exclude_none=True, exclude_unset=True)
    assert "dev_mode" not in dumped
    assert dumped == {"llm": {"chat_provider": "ollama"}}


def test_local_auth_secret_fields_are_stripped() -> None:
    """Auth secrets/paths are managed by the auth flow, not the generic PATCH."""
    req = SettingsUpdateRequest.model_validate(
        {
            "local_auth": {
                "cookie_secure": True,
                "edge_auth_token": "leaked-token",
                "edge_auth_header": "X-Renamed-Trust-Header",
                "session_secret_path": "/evil/path",
                "credentials_path": "/evil/creds",
            }
        }
    )
    dumped = req.model_dump(exclude_none=True, exclude_unset=True)
    assert dumped == {"local_auth": {"cookie_secure": True}}


@pytest.mark.parametrize(
    "bad_name",
    ["../../../tmp/pwn", "a/b", "", ".", "..", "a" * 65, "a\\b"],
)
def test_current_database_traversal_forms_rejected(bad_name: str) -> None:
    """current_database feeds raw path joins — traversal forms must 422 at the DTO."""
    with pytest.raises(ValidationError):
        SettingsUpdateRequest.model_validate({"current_database": bad_name})


@pytest.mark.parametrize("good_name", ["default", "my-db_2", "research-2026"])
def test_current_database_legitimate_names_accepted(good_name: str) -> None:
    """Every name create_database would accept passes the DTO check."""
    req = SettingsUpdateRequest.model_validate({"current_database": good_name})
    assert req.model_dump()["current_database"] == good_name
