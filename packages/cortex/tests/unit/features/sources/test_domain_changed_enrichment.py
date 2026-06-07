# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

from chaoscypher_cortex.features.sources.mappers import enrich_domain_changed


def test_stale_when_hash_differs():
    sources = [{"extraction_domain": "technical", "domain_content_hash": "old"}]
    enrich_domain_changed(sources, {"technical": "new"})
    assert sources[0]["domain_changed_since_extraction"] is True


def test_not_stale_when_hash_matches():
    sources = [{"extraction_domain": "technical", "domain_content_hash": "same"}]
    enrich_domain_changed(sources, {"technical": "same"})
    assert sources[0]["domain_changed_since_extraction"] is False


def test_not_stale_when_no_stored_hash():
    sources = [{"extraction_domain": "technical", "domain_content_hash": None}]
    enrich_domain_changed(sources, {"technical": "new"})
    assert sources[0]["domain_changed_since_extraction"] is False


def test_not_stale_when_plugin_missing():
    sources = [{"extraction_domain": "gone", "domain_content_hash": "old"}]
    enrich_domain_changed(sources, {})
    assert sources[0]["domain_changed_since_extraction"] is False
