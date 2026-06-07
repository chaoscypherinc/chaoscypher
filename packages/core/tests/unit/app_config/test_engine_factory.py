# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Specification tests for build_engine_settings().

Pins the cross-group legacy mappings, the explicit-target-wins guard, the
rerank cache-dir derivation, and the copy-isolation contract. These were
written against the now-deleted reflection bridge and survive as the
factory's standalone spec.
"""

from pathlib import Path

from chaoscypher_core.app_config import Settings
from chaoscypher_core.app_config.engine_factory import build_engine_settings


def _customized(tmp_path) -> Settings:
    return Settings(
        current_database="proj",
        paths={"data_dir": str(tmp_path)},
        llm={"chat_provider": "openai", "openai_chat_model": "gpt-x"},
        timeouts={"llm_stream_chunk_timeout": 77.0, "sqlite_connection": 11},
        backoff={"max_seconds": 99, "sqlite_base_delay": 2.5},
        retries={"sqlite_max_attempts": 9},
        batching={"sqlite_cache_size_kb": 1234, "discovery_batch": 55},
        extraction={"domain_detection_sample_count": 8},
    )


def test_cross_group_mappings_apply(tmp_path):
    e = build_engine_settings(_customized(tmp_path))
    assert e.current_database == "proj"
    assert e.llm.stream_chunk_timeout == 77.0
    assert e.database.connection_timeout_secs == 11
    assert e.database.cache_size_kb == 1234
    assert e.database.commit_max_retries == 9
    assert e.database.commit_base_delay_secs == 2.5
    assert e.extraction.llm_backoff_max_seconds == 99
    assert e.extraction.domain_detection_sample_count == 8  # union flows through
    assert Path(e.search.rerank_cache_dir) == Path(e.paths.data_dir) / "cache" / "rerankers"
    # moved groups ride along
    assert e.backoff.max_seconds == 99


def test_explicit_target_field_beats_cross_group_mapping(tmp_path):
    """Explicit target value wins over the legacy cross-group mapping.

    extraction.llm_backoff_max_seconds is yaml-settable now; an explicit
    value must NOT be clobbered by the legacy backoff.max_seconds mapping.
    """
    backend = Settings(
        paths={"data_dir": str(tmp_path)},
        backoff={"max_seconds": 99},
        extraction={"llm_backoff_max_seconds": 14},
    )
    e = build_engine_settings(backend)
    assert e.extraction.llm_backoff_max_seconds == 14


def test_factory_returns_isolated_copies(tmp_path):
    backend = Settings(paths={"data_dir": str(tmp_path)})
    e = build_engine_settings(backend)
    e.llm.ollama_chat_model = "mutated"
    assert backend.llm.ollama_chat_model != "mutated"
