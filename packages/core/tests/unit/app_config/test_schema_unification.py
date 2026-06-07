# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tier 2 schema unification: one class per settings group.

Pins (a) that previously engine-only fields are now settable from
settings.yaml groups, (b) that previously app-only fields exist on the
engine classes, (c) that defaults survived the unions, and (d) that the
moved web/backoff/retries/quality groups exist on EngineSettings.
"""

import pytest
import yaml

from chaoscypher_core.app_config import Settings
from chaoscypher_core.settings import (
    AnalysisSettings,
    BatchingSettings,
    CLISettings,
    DatabaseSettings,
    EngineSettings,
    ExtractionSettings,
)


def _load(tmp_path, data) -> Settings:
    p = tmp_path / "settings.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return Settings.load_from_yaml(p)


def test_single_class_per_group_identity():
    """app_config groups and EngineSettings groups are the SAME classes."""
    s = Settings()
    e = EngineSettings()
    assert type(s.extraction) is type(e.extraction) is ExtractionSettings
    assert type(s.database) is type(e.database) is DatabaseSettings
    assert type(s.analysis) is type(e.analysis) is AnalysisSettings
    assert type(s.cli) is type(e.cli) is CLISettings
    assert type(s.batching) is type(e.batching) is BatchingSettings
    assert type(s.export) is type(e.export)
    # moved app-only groups now exist on EngineSettings too
    assert type(s.web) is type(e.web)
    assert type(s.backoff) is type(e.backoff)
    assert type(s.retries) is type(e.retries)
    assert type(s.quality) is type(e.quality)


def test_extraction_union_settable_from_yaml(tmp_path):
    s = _load(
        tmp_path,
        {
            "extraction": {
                # previously app-only:
                "domain_detection_sample_count": 9,
                # previously engine-only (NOT yaml-settable before Tier 2):
                "quality_issue_threshold": 0.7,
            }
        },
    )
    assert s.extraction.domain_detection_sample_count == 9
    assert s.extraction.quality_issue_threshold == 0.7


def test_database_union_keeps_both_sides(tmp_path):
    s = _load(tmp_path, {"database": {"strict_schema_drift": False, "pool_size": 5}})
    assert s.database.strict_schema_drift is False
    assert s.database.pool_size == 5


def test_batching_union_engine_fields_now_yaml_settable(tmp_path):
    s = _load(tmp_path, {"batching": {"embedding_batch_size": 128, "max_upload_files": 7}})
    assert s.batching.embedding_batch_size == 128  # was silently ignored pre-Tier-2
    assert s.batching.max_upload_files == 7


def test_union_defaults_unchanged():
    """Spot-pin defaults from BOTH sides of each union."""
    e = ExtractionSettings()
    assert e.quality_issue_threshold == 0.3  # core side
    assert e.domain_detection_sample_count == 5  # app side
    d = DatabaseSettings()
    assert d.pool_size == 20 and d.strict_schema_drift is True
    b = BatchingSettings()
    assert b.embedding_batch_size == 512 and b.max_upload_files == 20
    c = CLISettings()
    assert c.api_port == 8081 and c.list_page_size == 50
    a = AnalysisSettings()
    assert a.quick_sample_size == 5 and a.extraction_max_input_chars == 8000


def test_unknown_nested_key_now_errors(tmp_path):
    # Pydantic's extra=forbid error names the rejecting class (ExtractionSettings)
    # and the offending field — not the lowercase yaml key. Match the class name,
    # which proves the error originated in the extraction group's strict schema.
    with pytest.raises(Exception, match="ExtractionSettings"):
        _load(tmp_path, {"extraction": {"definitely_not_a_field": 1}})
