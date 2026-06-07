# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workstream 1: ``SourceResponse.upload_options`` exposes upload-time choices.

The model validator on ``SourceResponse`` reads the row-level columns
(``auto_analyze``, ``enable_vision``, ``filtering_mode``, ...) and
assembles them into a nested ``UploadOptions`` object so the public API
contract is one well-named object rather than scattered top-level fields.
"""

from __future__ import annotations

from datetime import UTC, datetime

from chaoscypher_core.models import SourceStatus
from chaoscypher_cortex.features.sources.models import SourceResponse, UploadOptions


def _minimal_row(**overrides: object) -> dict[str, object]:
    """Build a minimal source-row dict acceptable to ``SourceResponse``."""
    base: dict[str, object] = {
        "id": "src_x",
        "database_name": "default",
        "filename": "doc.txt",
        "status": SourceStatus.INDEXED,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return base


def test_source_response_assembles_upload_options_from_row_columns() -> None:
    row = _minimal_row(
        extraction_depth="full",
        forced_domain="technical",
        auto_analyze=False,
        enable_normalization=False,
        enable_vision=False,
        content_filtering=False,
        filtering_mode="strict",
    )

    response = SourceResponse(**row)

    assert isinstance(response.upload_options, UploadOptions)
    assert response.upload_options.auto_analyze is False
    assert response.upload_options.extraction_depth == "full"
    assert response.upload_options.forced_domain == "technical"
    assert response.upload_options.enable_normalization is False
    assert response.upload_options.enable_vision is False
    assert response.upload_options.content_filtering is False
    assert response.upload_options.filtering_mode == "strict"


def test_source_response_upload_options_uses_defaults_when_row_omits() -> None:
    """A row missing the upload-setting columns yields default UploadOptions."""
    response = SourceResponse(**_minimal_row())

    assert response.upload_options is not None
    assert response.upload_options.auto_analyze is True
    assert response.upload_options.enable_vision is True
    assert response.upload_options.content_filtering is True
    assert response.upload_options.filtering_mode == "balanced"
    assert response.upload_options.enable_normalization is None


def test_upload_settings_only_appear_under_upload_options() -> None:
    """The seven sibling fields are excluded from the serialized payload.

    ``upload_options`` is the sole public surface; the row-level columns
    hydrate the model via ``from_attributes`` but never appear in the
    JSON output, eliminating the duplicate-shape drift risk.
    """
    row = _minimal_row(
        forced_domain="technical",
        auto_analyze=False,
        enable_normalization=True,
        enable_vision=False,
        content_filtering=False,
        filtering_mode="strict",
    )

    response = SourceResponse.model_validate(row, from_attributes=True)
    dumped = response.model_dump()

    # Nested object is the public surface.
    assert "upload_options" in dumped
    assert dumped["upload_options"]["filtering_mode"] == "strict"
    assert dumped["upload_options"]["forced_domain"] == "technical"
    assert dumped["upload_options"]["auto_analyze"] is False
    assert dumped["upload_options"]["enable_normalization"] is True
    assert dumped["upload_options"]["enable_vision"] is False
    assert dumped["upload_options"]["content_filtering"] is False

    # The seven sibling fields must NOT appear at the top level.
    for hidden in (
        "forced_domain",
        "auto_analyze",
        "enable_normalization",
        "enable_vision",
        "content_filtering",
        "filtering_mode",
    ):
        assert hidden not in dumped, (
            f"{hidden} must only appear under upload_options, "
            f"not as a top-level sibling on SourceResponse."
        )
