# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

from chaoscypher_core.services.sources.engine.extraction.domains.factory import (
    clear_domain_registry_cache,
    get_domain_registry,
)
from chaoscypher_core.services.sources.engine.extraction.domains.fingerprint import (
    compute_domain_content_hash,
)


def test_get_domain_fingerprint_returns_version_and_hash():
    clear_domain_registry_cache()
    registry = get_domain_registry(database_name="default")
    fp = registry.get_domain_fingerprint("technical")
    assert fp is not None
    assert fp.version == "1.9.0"  # <-- use the real technical.jsonld version
    assert len(fp.content_hash) == 64
    domain = registry.get_domain("technical")
    assert fp.content_hash == compute_domain_content_hash(domain)


def test_get_domain_fingerprint_unknown_returns_none():
    clear_domain_registry_cache()
    registry = get_domain_registry(database_name="default")
    assert registry.get_domain_fingerprint("does-not-exist") is None


def test_list_domain_info_includes_content_hash():
    clear_domain_registry_cache()
    registry = get_domain_registry(database_name="default")
    info = {d["name"]: d for d in registry.list_domain_info()}
    assert len(info["technical"]["content_hash"]) == 64
