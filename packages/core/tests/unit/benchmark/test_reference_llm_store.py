# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A reference pack's LLM store: the manifest fields, their checks, the sha256; the rows' cache facts."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chaoscypher_core.benchmark.reference import (
    LLM_STORE_FIELDS,
    LLM_STORE_FILE,
    OPTIONAL_MANIFEST_FIELDS,
    ReferencePackError,
    load_pack,
    reference_root,
    write_manifest,
)
from chaoscypher_core.benchmark.results import (
    COMPARABILITY_FIELDS,
    INFORMATIONAL_RUN_SETTINGS,
    BenchmarkResult,
    cache_run_setting,
    row_comparability,
    stamp_cache_run_setting,
)
from chaoscypher_core.cache.export import file_sha256
from chaoscypher_core.settings import CacheSettings, CacheStoreSettings


_BUILD = "ollama:gemma4:31b:abc123@0.12.1"


def _pack(data_dir: Path, name: str = "book1", **over: str) -> Path:
    """A pack with a snapshot, a fixture and a store file (named in the manifest by ``over``)."""
    root = reference_root(data_dir) / name
    root.mkdir(parents=True)
    with closing(sqlite3.connect(root / "app.db")) as conn:
        conn.execute("CREATE TABLE sources (id TEXT, database_name TEXT)")
        conn.commit()
    (root / "queries.yaml").write_text(
        'version: "1.0"\nqueries:\n  - id: q1\n    band: factual_single_hop\n'
        '    question: "Who?"\n    gold_entities: ["A"]\n    gold_answer: A.\n',
        encoding="utf-8",
    )
    (root / LLM_STORE_FILE).write_bytes(b"the recorded completions")
    write_manifest(
        root,
        {
            "name": name,
            "fixture_id": "war_and_peace_book1",
            "fixture_version": "1.0",
            "corpus_id": "war_and_peace_book1",
            "extractor": "ollama/gemma4:31b",
            "extractor_label": "Gemma 4 31B",
            "embedder": "ollama/qwen3-embedding:0.6b",
            "dedup_embedder": "exact",
            "graph_cache_key": "0" * 16,
            "created": "2026-10-06T00:00:00+00:00",
            "snapshot_file": "app.db",
            "queries_file": "queries.yaml",
            **over,
        },
    )
    return root


def _store_fields(root: Path) -> dict[str, str]:
    return {
        "llm_store": LLM_STORE_FILE,
        "llm_store_sha256": file_sha256(root / LLM_STORE_FILE),
        "model_build": _BUILD,
    }


def test_the_store_fields_are_optional_manifest_fields() -> None:
    assert set(LLM_STORE_FIELDS) <= set(OPTIONAL_MANIFEST_FIELDS)


def test_a_pack_with_a_store_loads_its_fields_and_verifies_its_bytes(tmp_path: Path) -> None:
    root = _pack(tmp_path)
    write_manifest(root, {**load_pack(tmp_path, "book1").manifest, **_store_fields(root)})
    pack = load_pack(tmp_path, "book1")
    assert pack.llm_store == LLM_STORE_FILE and pack.model_build == _BUILD
    assert pack.llm_store_path == root / LLM_STORE_FILE
    assert pack.verify_llm_store() == root / LLM_STORE_FILE
    (root / LLM_STORE_FILE).write_bytes(b"another store")
    with pytest.raises(ReferencePackError, match="does not match the manifest's llm_store_sha256"):
        pack.verify_llm_store()


def test_a_pack_without_a_store_has_none(tmp_path: Path) -> None:
    _pack(tmp_path)
    pack = load_pack(tmp_path, "book1")
    assert pack.llm_store_path is None and pack.verify_llm_store() is None


@pytest.mark.parametrize("missing", LLM_STORE_FIELDS)
def test_a_store_needs_all_three_fields(tmp_path: Path, missing: str) -> None:
    root = _pack(tmp_path)
    fields = {k: v for k, v in _store_fields(root).items() if k != missing}
    with pytest.raises(ReferencePackError, match=f"missing {missing}"):
        write_manifest(root, {**load_pack(tmp_path, "book1").manifest, **fields})


def test_a_store_named_but_absent_is_refused(tmp_path: Path) -> None:
    root = _pack(tmp_path)
    fields = _store_fields(root)
    (root / LLM_STORE_FILE).unlink()
    with pytest.raises(ReferencePackError, match="missing from the pack"):
        write_manifest(root, {**load_pack(tmp_path, "book1").manifest, **fields})


# ------------------------------------------------------------------ run settings


def _row(extras: dict) -> BenchmarkResult:
    return BenchmarkResult(
        model_id="ollama/m",
        model_label="M",
        dataset_id="d",
        dataset_kind="chat",
        dataset_version="1",
        dataset_source="builtin",
        config_name=None,
        headline_score=0.0,
        metrics={},
        latency_ms_total=0,
        latency_ms_per_chunk_p50=0,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        success=True,
        error=None,
        timestamp=datetime.now(tz=UTC),
        benchmark_version="1",
        scorer_version="1",
        seed=42,
        temperature=0.0,
        thinking=True,
        thinking_honoured=None,
        chunks_truncated=0,
        chunks_aborted_by_loop=0,
        extras=extras,
    )


def test_a_replayed_row_compares_as_the_recorded_one() -> None:
    """The cache facts are informational: no comparability field reads them."""
    assert not INFORMATIONAL_RUN_SETTINGS & set(COMPARABILITY_FIELDS)
    replay = CacheSettings(
        completions=CacheStoreSettings(require_hit=True),
        embeddings=CacheStoreSettings(require_hit=True),
    )
    recorded, replayed = {"run_settings": {"num_ctx": 8192}}, {"run_settings": {"num_ctx": 8192}}
    stamp_cache_run_setting(recorded, CacheSettings())
    stamp_cache_run_setting(replayed, replay)
    assert replayed["run_settings"]["cache"] == {
        "completions_enabled": True,
        "embeddings_enabled": True,
        "replay": True,
    }
    assert recorded["run_settings"]["cache"]["replay"] is False
    assert row_comparability(_row(recorded)) == row_comparability(_row(replayed))


def test_a_row_without_run_settings_gets_the_cache_facts_beside_them() -> None:
    """A row that records no run settings must not gain them (their absence is a finding)."""
    extras: dict = {}
    stamp_cache_run_setting(extras, CacheSettings(embeddings=CacheStoreSettings(enabled=False)))
    assert "run_settings" not in extras
    assert extras["cache"] == cache_run_setting(
        CacheSettings(embeddings=CacheStoreSettings(enabled=False))
    )
    assert extras["cache"]["embeddings_enabled"] is False
