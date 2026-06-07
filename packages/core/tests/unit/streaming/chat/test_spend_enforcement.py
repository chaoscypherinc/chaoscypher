# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests that the interactive streaming chat path enforces the LLM spend cap.

Regression: ``stream_chat_response`` called the provider directly with neither a
``check_and_raise`` before nor a ``record`` after, so a configured
``max_tokens_per_day`` was silently ignored on the most-used chat path.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.exceptions import LLMSpendCapExceededError
from chaoscypher_core.services.llm.spend import _reset_tracker_for_tests
from chaoscypher_core.streaming.chat.handler import (
    _spend_check,
    _spend_record,
    _track_streaming_tokens,
)


def _settings(per_day=None):
    settings = MagicMock()
    settings.current_database = "test_db"
    settings.llm.max_tokens_per_day = per_day
    settings.llm.max_tokens_per_source = None
    return settings


@pytest.fixture(autouse=True)
def _fresh_tracker():
    """Each test starts with a clean process-wide spend tracker."""
    _reset_tracker_for_tests()
    yield
    _reset_tracker_for_tests()


@patch("chaoscypher_core.database.adapter_factory.get_sqlite_adapter")
def test_spend_check_raises_when_daily_cap_reached(mock_get_adapter):
    """The pre-call gate raises (and disconnects its adapter) once the daily cap
    is reached, so the streaming turn never starts.
    """
    adapter = MagicMock()
    adapter.get_daily_token_spend.return_value = 10_000
    mock_get_adapter.return_value = adapter

    with pytest.raises(LLMSpendCapExceededError):
        _spend_check(_settings(per_day=5_000))

    adapter.disconnect.assert_called_once()


@patch("chaoscypher_core.database.adapter_factory.get_sqlite_adapter")
def test_spend_check_passes_when_under_cap(mock_get_adapter):
    """Under the cap, the gate does not raise."""
    adapter = MagicMock()
    adapter.get_daily_token_spend.return_value = 100
    mock_get_adapter.return_value = adapter

    _spend_check(_settings(per_day=5_000))  # no raise

    adapter.disconnect.assert_called_once()


@patch("chaoscypher_core.database.adapter_factory.get_sqlite_adapter")
def test_spend_record_adds_turn_tokens_to_daily_total(mock_get_adapter):
    """A completed turn's tokens are written to the persisted daily total."""
    adapter = MagicMock()
    mock_get_adapter.return_value = adapter

    _spend_record(_settings(per_day=5_000), 1234)

    adapter.add_daily_token_spend.assert_called_once()
    assert adapter.add_daily_token_spend.call_args.kwargs["tokens"] == 1234
    adapter.disconnect.assert_called_once()


@pytest.mark.asyncio
@patch("chaoscypher_core.database.adapter_factory.get_sqlite_adapter")
async def test_track_streaming_tokens_records_spend_when_settings_present(mock_get_adapter):
    """End-of-turn token tracking also feeds the persisted daily spend cap."""
    adapter = MagicMock()
    mock_get_adapter.return_value = adapter

    with patch("chaoscypher_core.streaming.chat.handler.queue_client") as qc:
        qc.track_tokens = AsyncMock()
        await _track_streaming_tokens(
            [{"role": "user", "content": "hello"}],
            "hello world response",
            "chat-1",
            settings=_settings(per_day=5_000),
        )

    adapter.add_daily_token_spend.assert_called_once()
