# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests: ExtractionSettings is the sole source of truth for
filtering-config field defaults after SourceProcessingSettings duplicates
were deleted (Phase 3 Task 1).
"""

import pytest

from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
    FilteringConfig,
)
from chaoscypher_core.settings import ExtractionSettings


@pytest.mark.unit
@pytest.mark.core
class TestExtractionSettingsDefaults:
    """ExtractionSettings carries the canonical defaults consumed by FilteringConfig."""

    def test_max_relationship_ratio_default(self) -> None:
        """ExtractionSettings.max_relationship_ratio defaults to 8.0 (the safety-net value)."""
        assert ExtractionSettings.model_fields["max_relationship_ratio"].default == 8.0

    def test_max_entity_degree_default(self) -> None:
        """ExtractionSettings.max_entity_degree defaults to 25."""
        assert ExtractionSettings.model_fields["max_entity_degree"].default == 25

    def test_max_same_source_type_default(self) -> None:
        """ExtractionSettings.max_same_source_type defaults to 12."""
        assert ExtractionSettings.model_fields["max_same_source_type"].default == 12

    def test_evidence_validation_mode_default(self) -> None:
        """ExtractionSettings.evidence_validation_mode defaults to 'standard'."""
        assert ExtractionSettings.model_fields["evidence_validation_mode"].default == "standard"


@pytest.mark.unit
@pytest.mark.core
class TestExtractionSettingsMatchFilteringConfigDefaults:
    """ExtractionSettings defaults must stay in sync with FilteringConfig dataclass defaults."""

    def test_max_relationship_ratio_matches_filtering_config(self) -> None:
        """ExtractionSettings and FilteringConfig share the same max_relationship_ratio default."""
        extraction_default = ExtractionSettings.model_fields["max_relationship_ratio"].default
        filtering_default = FilteringConfig().max_relationship_ratio
        assert extraction_default == filtering_default, (
            f"ExtractionSettings default {extraction_default!r} != "
            f"FilteringConfig default {filtering_default!r}"
        )

    def test_max_entity_degree_matches_filtering_config(self) -> None:
        """ExtractionSettings and FilteringConfig share the same max_entity_degree default."""
        extraction_default = ExtractionSettings.model_fields["max_entity_degree"].default
        filtering_default = FilteringConfig().max_entity_degree
        assert extraction_default == filtering_default, (
            f"ExtractionSettings default {extraction_default!r} != "
            f"FilteringConfig default {filtering_default!r}"
        )

    def test_max_same_source_type_matches_filtering_config(self) -> None:
        """ExtractionSettings and FilteringConfig share the same max_same_source_type default."""
        extraction_default = ExtractionSettings.model_fields["max_same_source_type"].default
        filtering_default = FilteringConfig().max_same_source_type
        assert extraction_default == filtering_default, (
            f"ExtractionSettings default {extraction_default!r} != "
            f"FilteringConfig default {filtering_default!r}"
        )

    def test_evidence_validation_mode_matches_filtering_config(self) -> None:
        """ExtractionSettings and FilteringConfig share the same evidence_validation_mode default."""
        extraction_default = ExtractionSettings.model_fields["evidence_validation_mode"].default
        filtering_default = FilteringConfig().evidence_validation_mode
        assert extraction_default == filtering_default, (
            f"ExtractionSettings default {extraction_default!r} != "
            f"FilteringConfig default {filtering_default!r}"
        )
