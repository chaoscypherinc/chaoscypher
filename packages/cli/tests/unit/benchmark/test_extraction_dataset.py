# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Structural tests for ExtractionDataset (no LLM)."""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_cli.benchmark.dataset import BenchmarkDataset
from chaoscypher_cli.benchmark.extraction_dataset import ExtractionDataset
from chaoscypher_cli.benchmark.scorers.v7 import V7ExtractionScorer


def test_extraction_dataset_satisfies_protocol(tmp_path: Path):
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("Pierre Bezukhov visited Moscow in 1812.")
    ds = ExtractionDataset(
        id="t",
        version="1.0",
        corpus_path=corpus,
        domain="literary",
    )
    assert isinstance(ds, BenchmarkDataset)
    assert ds.kind == "extraction"
    assert isinstance(ds.scorer, V7ExtractionScorer)
    assert ds.fixture is None  # fixture used by chat in v2
    assert ds.source == "builtin"  # default


def test_extraction_dataset_loads_corpus(tmp_path: Path):
    corpus = tmp_path / "corpus.txt"
    text = "Pierre Bezukhov visited Moscow in 1812."
    corpus.write_text(text)
    ds = ExtractionDataset(id="t", version="1.0", corpus_path=corpus, domain="literary")
    assert ds.corpus_text == text


def test_extraction_dataset_missing_corpus_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ExtractionDataset(
            id="t",
            version="1.0",
            corpus_path=tmp_path / "missing.txt",
            domain="literary",
        )


def test_extraction_dataset_keep_db_defaults_to_false(tmp_path: Path):
    """Default behavior cleans up temp DBs."""
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("x")
    ds = ExtractionDataset(id="t", version="1.0", corpus_path=corpus, domain="literary")
    assert ds.keep_db is False


def test_extraction_dataset_keep_db_can_be_enabled(tmp_path: Path):
    """Opt-in path preserves temp DBs."""
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("x")
    ds = ExtractionDataset(
        id="t",
        version="1.0",
        corpus_path=corpus,
        domain="literary",
        keep_db=True,
    )
    assert ds.keep_db is True


def test_extraction_dataset_source_set_user(tmp_path: Path):
    """Source is settable for user-overlay datasets."""
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("x")
    ds = ExtractionDataset(
        id="t",
        version="1.0",
        corpus_path=corpus,
        domain="literary",
        source="user",
    )
    assert ds.source == "user"
