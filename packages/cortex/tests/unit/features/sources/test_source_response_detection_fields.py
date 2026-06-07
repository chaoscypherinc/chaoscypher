# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SourceResponse / SourceSummaryResponse expose detection_ranking /
detection_confidence / detection_low_confidence / proposed_extraction_options
sourced from the detection_proposal blob, and map the awaiting_confirmation
status.  Both the detail model (SourceResponse) and the list model
(SourceSummaryResponse) are covered here to guarantee parity.
"""

from __future__ import annotations

from datetime import UTC, datetime

from chaoscypher_core.models import SourceStatus
from chaoscypher_cortex.features.sources.models import SourceResponse, SourceSummaryResponse


def _base_kwargs(**extra):
    now = datetime.now(UTC)
    return {
        "id": "src_1",
        "database_name": "default",
        "filename": "doc.pdf",
        "status": SourceStatus.AWAITING_CONFIRMATION,
        "created_at": now,
        "updated_at": now,
        **extra,
    }


def _summary_base_kwargs(**extra):
    now = datetime.now(UTC)
    return {
        "id": "src_1",
        "database_name": "default",
        "filename": "doc.pdf",
        "status": SourceStatus.AWAITING_CONFIRMATION,
        "created_at": now,
        "updated_at": now,
        **extra,
    }


# ============================================================================
# SourceResponse — detection fields
# ============================================================================


def test_detection_fields_default_empty_when_no_proposal() -> None:
    resp = SourceResponse(**_base_kwargs())
    assert resp.detection_ranking == []
    assert resp.detection_confidence is None
    assert resp.detection_low_confidence is None
    assert resp.proposed_extraction_options is None


def test_detection_fields_sourced_from_proposal() -> None:
    proposal = {
        "ranking": [
            {"domain": "medical", "score": 4.2},
            {"domain": "legal", "score": 1.1},
        ],
        "confidence": 4.2,
        "detected_domain": "medical",
        "low_confidence": False,
    }
    resp = SourceResponse(**_base_kwargs(detection_proposal=proposal))
    assert resp.detection_ranking == [
        {"domain": "medical", "score": 4.2},
        {"domain": "legal", "score": 1.1},
    ]
    assert resp.detection_confidence == 4.2
    assert resp.detection_low_confidence is False
    assert resp.proposed_extraction_options == proposal


def test_detection_low_confidence_true_from_proposal() -> None:
    proposal = {
        "ranking": [],
        "confidence": 0.1,
        "detected_domain": "generic",
        "low_confidence": True,
    }
    resp = SourceResponse(**_base_kwargs(detection_proposal=proposal))
    assert resp.detection_low_confidence is True
    assert resp.detection_ranking == []


def test_no_text_flag_survives_into_proposed_extraction_options() -> None:
    """Cross-step seam (wizard §3.1 no-text): the indexing handler's no_text
    proposal carries an extra ``no_text`` key beyond the 4-key blob. It must
    appear VERBATIM in ``proposed_extraction_options`` so the UI's
    ``proposal?.no_text === true`` branch fires (the "not enough text — pick a
    domain" copy). Uses the REAL handler blob builder so a shape change there
    breaks this test rather than silently producing ``undefined`` in the UI.
    """
    from chaoscypher_core.operations.importing.indexing_handler import _no_text_proposal

    blob = _no_text_proposal()
    # The image-only VISION_PENDING source the wizard reviews pre-vision.
    resp = SourceResponse(
        **_base_kwargs(status=SourceStatus.VISION_PENDING, detection_proposal=blob)
    )
    assert resp.proposed_extraction_options is not None
    # The whole blob (incl. no_text) is surfaced verbatim, not just the 4 keys.
    assert resp.proposed_extraction_options.get("no_text") is True
    assert resp.proposed_extraction_options.get("detected_domain") == "generic"
    # The no-text doc is always low-confidence so the UI seeds __auto__.
    assert resp.detection_low_confidence is True
    # Survives JSON serialization (the actual wire the UI reads).
    dumped = resp.model_dump()
    assert dumped["proposed_extraction_options"]["no_text"] is True


def test_awaiting_confirmation_status_serializes() -> None:
    resp = SourceResponse(**_base_kwargs())
    dumped = resp.model_dump()
    assert dumped["status"] == "awaiting_confirmation"
    # progress must resolve (not raise) for the new status
    assert resp.progress is not None


# ============================================================================
# SourceSummaryResponse — detection fields (parity with SourceResponse)
# ============================================================================


def test_summary_detection_fields_default_when_no_proposal() -> None:
    summary = SourceSummaryResponse(**_summary_base_kwargs())
    assert summary.detection_ranking == []
    assert summary.detection_confidence is None
    assert summary.detection_low_confidence is None
    assert summary.proposed_extraction_options is None
    assert summary.confirmation_required is False
    assert summary.extraction_confirmed_at is None


def test_summary_detection_fields_sourced_from_proposal() -> None:
    proposal = {
        "ranking": [
            {"domain": "science", "score": 3.5},
            {"domain": "general", "score": 1.0},
        ],
        "confidence": 3.5,
        "detected_domain": "science",
        "low_confidence": False,
    }
    now = datetime.now(UTC)
    summary = SourceSummaryResponse(
        **_summary_base_kwargs(
            detection_proposal=proposal,
            confirmation_required=True,
            extraction_confirmed_at=now,
        )
    )
    assert summary.detection_ranking == [
        {"domain": "science", "score": 3.5},
        {"domain": "general", "score": 1.0},
    ]
    assert summary.detection_confidence == 3.5
    assert summary.detection_low_confidence is False
    assert summary.proposed_extraction_options == proposal
    assert summary.confirmation_required is True
    assert summary.extraction_confirmed_at == now


def test_summary_detection_low_confidence_true() -> None:
    proposal = {
        "ranking": [],
        "confidence": 0.05,
        "detected_domain": "generic",
        "low_confidence": True,
    }
    summary = SourceSummaryResponse(**_summary_base_kwargs(detection_proposal=proposal))
    assert summary.detection_low_confidence is True
    assert summary.detection_ranking == []


def test_summary_non_parked_source_has_no_detection_fields() -> None:
    """A committed (non-parked) source must have all detection fields at default."""
    now = datetime.now(UTC)
    summary = SourceSummaryResponse(
        id="src_2",
        database_name="default",
        filename="report.pdf",
        status=SourceStatus.COMMITTED,
        created_at=now,
        updated_at=now,
    )
    assert summary.detection_proposal is None
    assert summary.detection_ranking == []
    assert summary.detection_confidence is None
    assert summary.detection_low_confidence is None
    assert summary.proposed_extraction_options is None
    assert summary.confirmation_required is False
    assert summary.extraction_confirmed_at is None


def test_summary_proposal_excluded_from_json() -> None:
    """detection_proposal (raw blob) must not appear in the serialized JSON."""
    proposal = {
        "ranking": [],
        "confidence": 0.1,
        "detected_domain": "generic",
        "low_confidence": True,
    }
    summary = SourceSummaryResponse(**_summary_base_kwargs(detection_proposal=proposal))
    dumped = summary.model_dump()
    assert "detection_proposal" not in dumped
    # but the public fields are present
    assert "detection_low_confidence" in dumped
    assert "detection_ranking" in dumped
