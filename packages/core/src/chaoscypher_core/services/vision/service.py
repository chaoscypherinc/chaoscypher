# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Vision processing service.

Single-page describe with an output-token cap and finish_reason
surfacing. The legacy batched ``describe_images`` helper was removed in
PR 2 (Task 12, 2026-05-13) alongside the indexing_handler rewire — the
per-page queue handler now calls ``describe_image`` directly, one page
per task on QUEUE_LLM.
"""

from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.vision.prompts import STANDALONE_IMAGE_PROMPT


if TYPE_CHECKING:
    from chaoscypher_core.ports.llm import LLMProviderPort
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class VisionResult:
    """Result of one ``describe_image`` call.

    description: Text content of the LLM response, or None on hard
                 failure (LLM raised, empty content with no thinking,
                 etc.). Callers should treat None as FAILED.
    finish_reason: Provider-normalised reason. Common values:
                 'stop'   — model completed naturally.
                 'length' — hit max_tokens cap. Caller should mark
                            TRUNCATED and bump VISION_PAGES_TRUNCATED.
                 'other'  — provider returned something else (rare).
                 None     — provider didn't surface a finish reason.
    input_tokens: Prompt tokens reported by the provider (0 if unknown).
    output_tokens: Completion tokens reported by the provider (0 if
                 unknown). Surfaced so the per-page handler can record
                 vision spend against the daily/per-source cap.
    """

    description: str | None
    finish_reason: str | None
    input_tokens: int = 0
    output_tokens: int = 0


class VisionService:
    """Describes images using a vision-capable LLM.

    Builds multimodal messages (text + base64 image) and calls
    the configured ``LLMProviderPort`` directly. Does not interact
    with queues.

    The caller provides a port configured for the vision model.
    ``create_vision_provider()`` is a convenience factory that builds
    a concrete ``LLMProvider`` from engine settings.
    """

    def __init__(self, llm_provider: LLMProviderPort) -> None:
        """Initialize VisionService.

        Args:
            llm_provider: LLMProviderPort configured with vision model.
        """
        self.llm_provider = llm_provider

    async def describe_image(
        self,
        image_bytes: bytes,
        prompt: str = STANDALONE_IMAGE_PROMPT,
        mime_type: str = "image/png",
        *,
        max_tokens: int | None = None,
    ) -> VisionResult:
        """Describe a single image. Returns ``VisionResult``.

        Args:
            image_bytes: Raw image bytes.
            prompt: Text prompt for the vision model.
            mime_type: MIME type of the image.
            max_tokens: Per-call output budget. None means use the
                provider's default. Caller passes
                ``settings.llm.<provider>_vision_max_output_tokens``.

        Returns:
            VisionResult(description, finish_reason). description is
            None on hard failure (exception, empty content). On
            finish_reason='length', description is the partial content
            the model produced before being cut off — caller marks the
            row TRUNCATED.
        """
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                    },
                ],
            }
        ]

        try:
            response = await self.llm_provider.chat(
                messages=messages,
                max_tokens=max_tokens,
            )
        except Exception:
            logger.warning("vision_description_failed", exc_info=True)
            return VisionResult(description=None, finish_reason=None)

        content = response.content
        finish_reason = response.finish_reason

        # Surface token usage so the caller can record vision spend. Extract
        # defensively: a provider may omit usage, and the fields must be real
        # ints (guards against None / test doubles).
        usage = getattr(response, "usage", None)
        raw_in = getattr(usage, "input_tokens", 0)
        raw_out = getattr(usage, "output_tokens", 0)
        input_tokens = raw_in if isinstance(raw_in, int) and not isinstance(raw_in, bool) else 0
        output_tokens = raw_out if isinstance(raw_out, int) and not isinstance(raw_out, bool) else 0

        logger.info(
            "vision_description_complete",
            description_length=len(content) if content else 0,
            finish_reason=finish_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        return VisionResult(
            description=content if content else None,
            finish_reason=finish_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def create_vision_provider(settings: EngineSettings, vision_model: str) -> LLMProviderPort:
    """Build a vision-configured ``LLMProviderPort``.

    Factory helper for CLI and Cortex indexing paths that need a
    vision-capable port without touching Engine internals. Creates a
    deep copy of settings with the active provider's chat model
    overridden to the vision model name, then constructs a concrete
    ``LLMProvider`` (which satisfies ``LLMProviderPort`` structurally).

    The late adapter import is intentional — this is wiring code, not
    business logic, and is allowlisted by the CC012 port-compliance
    rule.

    Args:
        settings: Engine settings (EngineSettings).
        vision_model: Vision model name to use.

    Returns:
        ``LLMProviderPort`` configured for vision calls.
    """
    from chaoscypher_core.adapters.llm.provider import LLMProvider

    vision_settings = copy.deepcopy(settings)
    provider = settings.llm.chat_provider
    setattr(vision_settings.llm, f"{provider}_chat_model", vision_model)
    return LLMProvider(settings=vision_settings)


__all__ = ["VisionResult", "VisionService", "create_vision_provider"]
