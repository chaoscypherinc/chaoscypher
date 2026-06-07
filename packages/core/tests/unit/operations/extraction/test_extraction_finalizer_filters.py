# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Defensive-path tests for ``_apply_post_dedup_filters``.

The helper wraps ``apply_cross_chunk_relationship_filters`` for the
distributed finalizer. It reads ``edge_type_constraints`` and
``extraction_limits`` from the persisted job's ``extraction_config``
JSON column, so the parsing/merging branches are worth exercising
directly. We import the underscore-prefixed helper through the module
namespace (``extraction_finalizer._apply_post_dedup_filters``); calling
it through the public ``_finalize_extraction_inner`` would require
spinning up a queue + adapter just to verify a parser branch.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from chaoscypher_core.operations.extraction import extraction_finalizer


def _fake_engine_settings() -> SimpleNamespace:
    """Minimal EngineSettings stand-in for the helper.

    Only ``extraction.extraction_filtering_mode`` is read.
    """
    return SimpleNamespace(extraction=SimpleNamespace(extraction_filtering_mode="balanced"))


@pytest.mark.unit
class TestApplyPostDedupFilters:
    """Cover the defensive paths in ``_apply_post_dedup_filters``."""

    def test_job_record_none_runs_filter_with_empty_constraints(self) -> None:
        """job_record=None - filter still runs with empty constraints, returns inputs.

        The helper resolves a default FilteringConfig from
        ``engine_settings.extraction.extraction_filtering_mode``. With no
        edge_type_constraints the type-constraint pass is a no-op; the
        relationship-limit pass still runs and emits a log stage with
        ``removed_count=0`` for the trivial payload.
        """
        entities: list[dict[str, Any]] = [
            {"id": "e0", "name": "Alice", "type": "Person"},
            {"id": "e1", "name": "AcmeCorp", "type": "Organization"},
        ]
        relationships: list[dict[str, Any]] = [
            {"source": 0, "target": 1, "type": "works_at"},
        ]

        out_entities, out_rels, out_log, out_config = (
            extraction_finalizer._apply_post_dedup_filters(
                entities=entities,
                relationships=relationships,
                engine_settings=_fake_engine_settings(),
                job_record=None,
                existing_filtering_log=None,
            )
        )

        assert out_entities == entities
        assert out_rels == relationships
        # The filter emits a log even with no drops; the wrapper merges it.
        assert out_log is not None
        assert out_log["total_removed"] == 0
        assert {s["stage"] for s in out_log["stages"]} == {"relationship_limit_enforcement"}
        # Helper now also returns the resolved FilteringConfig so the
        # downstream structural-filter step can gate on it without
        # re-parsing the extraction config.
        assert out_config.enable_structural_filter is True

    def test_extraction_config_as_dict_is_accepted(self) -> None:
        """extraction_config can be a raw dict, not only a JSON string."""
        entities: list[dict[str, Any]] = [
            {"id": "e0", "name": "Alice", "type": "Person"},
            {"id": "e1", "name": "AcmeCorp", "type": "Organization"},
        ]
        relationships: list[dict[str, Any]] = [
            {"source": 0, "target": 1, "type": "works_at"},
        ]
        job_record: dict[str, Any] = {
            "id": "job-1",
            "extraction_config": {
                "edge_type_constraints": {
                    "works_at": {
                        "source_types": ["Person"],
                        "target_types": ["Organization"],
                    }
                },
                "extraction_limits": {},
                "filtering_mode": "balanced",
            },
        }

        out_entities, out_rels, _out_log, _out_config = (
            extraction_finalizer._apply_post_dedup_filters(
                entities=entities,
                relationships=relationships,
                engine_settings=_fake_engine_settings(),
                job_record=job_record,
                existing_filtering_log=None,
            )
        )

        # Edge has matching source/target types, so it survives.
        assert out_entities == entities
        assert len(out_rels) == 1
        assert out_rels[0]["type"] == "works_at"

    def test_malformed_json_string_raises_data_integrity_error(self) -> None:
        """Phase 1 (2026-05-08): corrupt extraction_config raises DataIntegrityError.

        Pre-2026-05-08 the helper silently fell back to {} and the user's
        filtering choice was lost without any surface signal. Now it raises
        DataIntegrityError matching the F47 schema-validation pattern in the
        same file: corrupt persisted state is data-integrity, not recoverable.
        """
        from chaoscypher_core.exceptions import DataIntegrityError

        entities: list[dict[str, Any]] = [{"id": "e0", "name": "Alice", "type": "Person"}]
        relationships: list[dict[str, Any]] = [{"source": 0, "target": 0, "type": "knows"}]
        job_record: dict[str, Any] = {
            "id": "job-2",
            "extraction_config": "{not valid json",
        }

        with pytest.raises(DataIntegrityError):
            extraction_finalizer._apply_post_dedup_filters(
                entities=entities,
                relationships=relationships,
                engine_settings=_fake_engine_settings(),
                job_record=job_record,
                existing_filtering_log=None,
            )

    def test_existing_filtering_log_is_extended_in_place(self) -> None:
        """A populated existing log gets new filter stages appended.

        Exercises the JSON-string parse branch (extraction_config is a
        ``json.dumps`` blob) and the in-place merge: the returned merged
        log is the same object as the input ``existing_filtering_log``,
        with new stages appended and ``total_removed`` summed.
        """
        existing_log: dict[str, Any] = {
            "stages": [{"name": "dedup", "removed": 3}],
            "total_removed": 3,
        }
        entities: list[dict[str, Any]] = [
            {"id": "e0", "name": "Alice", "type": "Person"},
            {"id": "e1", "name": "Bob", "type": "Person"},
        ]
        relationships: list[dict[str, Any]] = [
            {"source": 0, "target": 1, "type": "knows"},
        ]

        # JSON-string form of extraction_config exercises the json.loads branch.
        job_record: dict[str, Any] = {
            "id": "job-3",
            "extraction_config": json.dumps({}),
        }

        _entities, _rels, merged, _config = extraction_finalizer._apply_post_dedup_filters(
            entities=entities,
            relationships=relationships,
            engine_settings=_fake_engine_settings(),
            job_record=job_record,
            existing_filtering_log=existing_log,
        )

        # The existing log object is returned (mutated in-place via setdefault
        # + extend / += on total_removed).
        assert merged is existing_log
        # Pre-existing dedup stage is still there; filter pass appended at least
        # the relationship_limit_enforcement stage.
        assert len(merged["stages"]) >= 2
        assert merged["stages"][0] == {"name": "dedup", "removed": 3}
        assert any(s.get("stage") == "relationship_limit_enforcement" for s in merged["stages"][1:])
        # Filter dropped nothing on this trivial payload, so the running total
        # stays at the pre-existing dedup count.
        assert merged["total_removed"] == 3


@pytest.mark.unit
class TestResolveFinalizerFilteringConfig:
    """Cover the defensive paths in ``_resolve_finalizer_filtering_config``."""

    def test_corrupt_extraction_config_raises_data_integrity_error(self) -> None:
        """Phase 1 (2026-05-08): corrupt extraction_config raises DataIntegrityError.

        Pre-2026-05-08 the helper silently fell back to {} and the user's
        filtering choice (e.g. aggressive) was silently discarded. Match
        the F47 schema-validation pattern in the same file.
        """
        from chaoscypher_core.exceptions import DataIntegrityError

        bad_job: dict[str, Any] = {"id": "j1", "extraction_config": "{not valid json"}

        with pytest.raises(DataIntegrityError):
            extraction_finalizer._resolve_finalizer_filtering_config(
                engine_settings=_fake_engine_settings(),
                job_record=bad_job,
            )

    def test_valid_json_extraction_config_resolves_filtering_mode(self) -> None:
        """A valid extraction_config JSON string is parsed and its filtering mode applied."""
        job_record: dict[str, Any] = {
            "id": "j2",
            "extraction_config": json.dumps({"extraction_limits": {}, "filtering_mode": "strict"}),
        }

        result = extraction_finalizer._resolve_finalizer_filtering_config(
            engine_settings=_fake_engine_settings(),
            job_record=job_record,
        )

        # strict mode enables structural filter and strict edge-type constraints.
        assert result.enable_structural_filter is True
        assert result.strict_edge_type_constraints is True

    def test_job_record_none_returns_default_config(self) -> None:
        """job_record=None resolves the engine-settings default filtering mode."""
        result = extraction_finalizer._resolve_finalizer_filtering_config(
            engine_settings=_fake_engine_settings(),
            job_record=None,
        )

        # Balanced (the _fake_engine_settings default) enables structural filter.
        assert result.enable_structural_filter is True
