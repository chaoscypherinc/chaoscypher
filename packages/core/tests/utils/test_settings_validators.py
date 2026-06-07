# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the max_length_from_settings validator factory."""

from types import SimpleNamespace
from typing import Annotated
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from chaoscypher_core.utils.settings_validators import max_length_from_settings


def _fake_settings(value: int) -> SimpleNamespace:
    """Build a fake settings object with the chat_message_max_length path populated."""
    return SimpleNamespace(chat_context=SimpleNamespace(chat_message_max_length=value))


class _ChatModel(BaseModel):
    content: Annotated[
        str,
        max_length_from_settings("chat_context.chat_message_max_length"),
    ]


@patch("chaoscypher_core.utils.settings_validators.get_settings")
def test_validator_accepts_when_under_limit(gs: MagicMock) -> None:
    gs.return_value = _fake_settings(10)
    m = _ChatModel(content="hi")
    assert m.content == "hi"


@patch("chaoscypher_core.utils.settings_validators.get_settings")
def test_validator_rejects_when_over_limit(gs: MagicMock) -> None:
    gs.return_value = _fake_settings(5)
    with pytest.raises(ValidationError) as exc:
        _ChatModel(content="this is too long")
    assert "exceeds configured maximum" in str(exc.value)
    assert "chat_context.chat_message_max_length" in str(exc.value)


@patch("chaoscypher_core.utils.settings_validators.get_settings")
def test_validator_passes_through_non_strings(gs: MagicMock) -> None:
    gs.return_value = _fake_settings(5)
    # ints, None, etc., are returned as-is; pydantic's normal coercion handles them.
    # We only enforce length when the value is actually a string.

    class M(BaseModel):
        x: Annotated[object, max_length_from_settings("chat_context.chat_message_max_length")]

    assert M(x=42).x == 42
    assert M(x=None).x is None


@patch("chaoscypher_core.utils.settings_validators.get_settings")
def test_validator_walks_nested_settings_path(gs: MagicMock) -> None:
    gs.return_value = SimpleNamespace(deeply=SimpleNamespace(nested=SimpleNamespace(limit=3)))

    class M(BaseModel):
        x: Annotated[str, max_length_from_settings("deeply.nested.limit")]

    M(x="abc")  # ok
    with pytest.raises(ValidationError):
        M(x="abcd")


@patch("chaoscypher_core.utils.settings_validators.get_settings")
def test_validator_raises_validation_error_on_typo_path(gs: MagicMock) -> None:
    """A misspelled setting path produces a clear ValidationError, not AttributeError."""
    gs.return_value = SimpleNamespace(chat_context=SimpleNamespace(chat_message_max_length=10))

    class M(BaseModel):
        x: Annotated[
            str, max_length_from_settings("chat_context.chat_messag_max_length")
        ]  # typo: 'messag' instead of 'message'

    with pytest.raises(ValidationError) as exc:
        M(x="hi")
    assert "could not be resolved" in str(exc.value)
    assert "chat_context.chat_messag_max_length" in str(exc.value)


@patch("chaoscypher_core.utils.settings_validators.get_settings")
def test_validator_raises_validation_error_when_setting_is_none(gs: MagicMock) -> None:
    """A None-valued setting field produces a clear ValidationError."""
    gs.return_value = SimpleNamespace(chat_context=SimpleNamespace(chat_message_max_length=None))

    class M(BaseModel):
        x: Annotated[str, max_length_from_settings("chat_context.chat_message_max_length")]

    with pytest.raises(ValidationError) as exc:
        M(x="hi")
    assert "resolved to None" in str(exc.value)


@patch("chaoscypher_core.utils.settings_validators.get_settings")
def test_validator_raises_validation_error_on_non_numeric_setting(gs: MagicMock) -> None:
    """A non-numeric setting value produces a clear ValidationError."""
    gs.return_value = SimpleNamespace(
        chat_context=SimpleNamespace(chat_message_max_length="not-a-number")
    )

    class M(BaseModel):
        x: Annotated[str, max_length_from_settings("chat_context.chat_message_max_length")]

    with pytest.raises(ValidationError) as exc:
        M(x="hi")
    assert "resolved to non-int" in str(exc.value)
