# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Structured-extraction protocol.

Plugins and services that need provider-agnostic JSON-schema extraction
accept a ``StructuredExtractorPort`` rather than importing the concrete
``StructuredExtractor`` from the adapter layer. The Engine (or tool
runtime) wires an instance at composition time; tests substitute a fake.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StructuredExtractorPort(Protocol):
    """Service-facing contract for structured JSON extraction.

    Mirrors the subset of the concrete ``StructuredExtractor`` surface
    that callers currently exercise. Kept narrow: callers should not
    peek at provider_factory or internal retry state through this port.
    """

    async def extract_structured(
        self,
        text: str,
        json_schema: dict[str, Any],
        system_prompt: str = "You are an expert at extracting structured information from text.",
        user_instructions: str | None = None,
        temperature: float | None = None,
        max_retries: int | None = None,
        max_tokens: int | None = None,
        enable_quality_check: bool = True,
        metrics_collector: Any | None = None,
    ) -> dict[str, Any]:
        """Extract structured data from ``text`` matching ``json_schema``.

        Args:
            text: Input text to analyze.
            json_schema: JSON schema for the desired output structure.
            system_prompt: System instructions for the LLM.
            user_instructions: Additional per-call user instructions.
            temperature: LLM sampling temperature.
            max_retries: Maximum retry attempts.
            max_tokens: Maximum tokens in response.
            enable_quality_check: Whether to run the quality validator.
            metrics_collector: Optional per-call metrics collector.

        Returns:
            Extracted structured data matching ``json_schema``, with a
            ``_metadata`` sub-dict describing success/retries/timings.

        """
        ...
