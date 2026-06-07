# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for VisionService."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.services.vision.service import VisionResult, VisionService


@pytest.fixture
def mock_llm_provider():
    """Create a mock LLMProvider that returns LLMChatResponse-like objects."""
    provider = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = "A bar chart showing Q3 revenue by region."
    mock_response.finish_reason = "stop"
    provider.chat.return_value = mock_response
    return provider


class TestVisionService:
    """Tests for VisionService."""

    def test_init(self, mock_llm_provider):
        """VisionService initializes with LLMProvider."""
        service = VisionService(llm_provider=mock_llm_provider)
        assert service.llm_provider is mock_llm_provider

    @pytest.mark.asyncio
    async def test_describe_image_builds_multimodal_message(self, mock_llm_provider):
        """describe_image sends base64 image with prompt to LLM and returns VisionResult."""
        service = VisionService(llm_provider=mock_llm_provider)
        image_bytes = b"fake-png-data"
        result = await service.describe_image(image_bytes, prompt="Describe this.")

        assert isinstance(result, VisionResult)
        assert result.description == "A bar chart showing Q3 revenue by region."
        assert result.finish_reason == "stop"
        mock_llm_provider.chat.assert_called_once()
        call_args = mock_llm_provider.chat.call_args
        messages = call_args[1]["messages"]
        assert len(messages) == 1
        content = messages[0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert "base64" in content[1]["image_url"]["url"]

    @pytest.mark.asyncio
    async def test_describe_image_returns_none_description_on_failure(self, mock_llm_provider):
        """describe_image returns VisionResult(None, None) when LLM call fails."""
        mock_llm_provider.chat.side_effect = Exception("LLM timeout")
        service = VisionService(llm_provider=mock_llm_provider)
        result = await service.describe_image(b"fake-data", prompt="Describe.")
        assert isinstance(result, VisionResult)
        assert result == VisionResult(description=None, finish_reason=None)

    @pytest.mark.asyncio
    async def test_describe_image_returns_none_description_on_empty_content(
        self, mock_llm_provider
    ):
        """describe_image returns None description when LLM returns empty content."""
        mock_response = MagicMock()
        mock_response.content = ""
        mock_response.finish_reason = "stop"
        mock_llm_provider.chat.return_value = mock_response
        service = VisionService(llm_provider=mock_llm_provider)
        result = await service.describe_image(b"fake-data", prompt="Describe.")
        assert isinstance(result, VisionResult)
        assert result.description is None
        # Empty-content branch must still carry the provider's finish_reason
        # through — it's used to distinguish "model stopped naturally with no
        # output" from "max_tokens cut off before any token landed."
        assert result.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_describe_image_threads_max_tokens_and_finish_reason(self) -> None:
        """Verify max_tokens flows through and finish_reason surfaces."""
        fake_response = MagicMock()
        fake_response.content = "transcription..."
        fake_response.finish_reason = "length"
        fake_provider = MagicMock()
        fake_provider.chat = AsyncMock(return_value=fake_response)

        service = VisionService(llm_provider=fake_provider)
        result = await service.describe_image(b"img", max_tokens=8192)

        assert result == VisionResult(description="transcription...", finish_reason="length")
        fake_provider.chat.assert_awaited_once()
        call_kwargs = fake_provider.chat.await_args.kwargs
        assert call_kwargs["max_tokens"] == 8192

    @pytest.mark.asyncio
    async def test_describe_image_surfaces_token_usage(self) -> None:
        """describe_image surfaces provider token usage on the VisionResult so the
        caller can record it against the LLM spend cap.
        """
        fake_response = MagicMock()
        fake_response.content = "A diagram of the pipeline."
        fake_response.finish_reason = "stop"
        fake_response.usage = MagicMock(input_tokens=120, output_tokens=45)
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=fake_response)

        service = VisionService(llm_provider=provider)
        result = await service.describe_image(b"img")

        assert result.input_tokens == 120
        assert result.output_tokens == 45

    def test_describe_images_batch_helper_is_removed(self):
        """The legacy batched ``describe_images`` helper was deleted in PR 2.

        Per-page LLM calls now go through ``OP_VISION_PAGE`` on
        ``QUEUE_LLM`` — see ``vision_page_handler.py``. The single-page
        ``describe_image`` is the only public LLM helper today.
        """
        service = VisionService(llm_provider=MagicMock())
        assert not hasattr(service, "describe_images"), (
            "describe_images was removed in PR 2 (Task 12, 2026-05-13) — "
            "use describe_image on the per-page handler instead."
        )
