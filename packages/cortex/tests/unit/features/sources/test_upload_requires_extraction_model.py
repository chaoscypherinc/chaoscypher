# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""POST /api/v1/sources must 409 when configured models aren't pulled.

Tests verify that require_extraction_ready raises the correct exception
depending on the LLM health state — both the EXTRACTION_MODEL_MISSING
path (verified=True but missing models) and the ordering rule (LLM_NOT_VERIFIED
fires first when both conditions are present).
"""

from __future__ import annotations

import pytest

from chaoscypher_core.exceptions import ExtractionModelMissingError, LLMNotVerifiedError
from chaoscypher_core.services.llm.health import LLMHealth


@pytest.mark.asyncio
async def test_upload_409s_when_extraction_model_missing(
    monkeypatch,
) -> None:
    """Mock get_llm_health to return verified=True but missing_models non-empty.
    require_extraction_ready must raise ExtractionModelMissingError with code
    EXTRACTION_MODEL_MISSING and the missing list in details so the frontend
    can render an actionable prompt.
    """
    from chaoscypher_core.services.llm.health import require_extraction_ready

    async def fake_health(_settings):
        return LLMHealth(
            provider="ollama",
            configured=True,
            verified=True,
            last_verified_at_iso="2026-05-21T20:00:00+00:00",
            missing_models=("qwen3:30b-instruct",),
        )

    monkeypatch.setattr(
        "chaoscypher_core.services.llm.health.get_llm_health",
        fake_health,
    )

    with pytest.raises(ExtractionModelMissingError) as exc_info:
        await require_extraction_ready(object())  # type: ignore[arg-type] — settings not used; mocked

    exc = exc_info.value
    assert exc.code == "EXTRACTION_MODEL_MISSING"
    assert exc.details["missing_models"] == ["qwen3:30b-instruct"]
    assert exc.details["provider"] == "ollama"


@pytest.mark.asyncio
async def test_upload_still_409s_when_unverified_first(
    monkeypatch,
) -> None:
    """If both verified=False and missing_models non-empty, the unverified
    error fires first (different operator problem, different Settings pane).
    """
    from chaoscypher_core.services.llm.health import require_extraction_ready

    async def fake_unverified(_settings):
        return LLMHealth(
            provider="ollama",
            configured=True,
            verified=False,
            last_verified_at_iso=None,
            missing_models=("qwen3:30b-instruct",),
        )

    monkeypatch.setattr(
        "chaoscypher_core.services.llm.health.get_llm_health",
        fake_unverified,
    )

    with pytest.raises(LLMNotVerifiedError) as exc_info:
        await require_extraction_ready(object())  # type: ignore[arg-type] — settings not used; mocked

    assert exc_info.value.code == "LLM_NOT_VERIFIED"


@pytest.mark.asyncio
async def test_no_exception_when_verified_and_no_missing_models(
    monkeypatch,
) -> None:
    """When verified=True and missing_models is empty, the gate passes silently."""
    from chaoscypher_core.services.llm.health import require_extraction_ready

    async def fake_healthy(_settings):
        return LLMHealth(
            provider="ollama",
            configured=True,
            verified=True,
            last_verified_at_iso="2026-05-21T20:00:00+00:00",
            missing_models=(),
        )

    monkeypatch.setattr(
        "chaoscypher_core.services.llm.health.get_llm_health",
        fake_healthy,
    )

    # Must not raise.
    await require_extraction_ready(object())  # type: ignore[arg-type]
