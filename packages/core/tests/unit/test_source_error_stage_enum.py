# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: SourceErrorStage values match historical wire strings."""

from __future__ import annotations


def test_enum_values_match_persisted_strings() -> None:
    from chaoscypher_core.models import SourceErrorStage

    assert SourceErrorStage.INDEXING.value == "indexing"
    assert SourceErrorStage.EXTRACTION.value == "extraction"
    assert SourceErrorStage.COMMIT.value == "commit"
    assert SourceErrorStage.URL_FETCH.value == "url_fetch"
    assert SourceErrorStage.RECOVERY_EXHAUSTED.value == "recovery_exhausted"


def test_enum_is_str_compatible() -> None:
    from chaoscypher_core.models import SourceErrorStage

    assert SourceErrorStage.COMMIT == "commit"


def test_enum_round_trips_through_str() -> None:
    from chaoscypher_core.models import SourceErrorStage

    member = SourceErrorStage("extraction")
    assert member is SourceErrorStage.EXTRACTION


def test_enum_exported_from_package_barrel() -> None:
    from chaoscypher_core import SourceErrorStage as Re_exported
    from chaoscypher_core.models import SourceErrorStage

    assert Re_exported is SourceErrorStage
