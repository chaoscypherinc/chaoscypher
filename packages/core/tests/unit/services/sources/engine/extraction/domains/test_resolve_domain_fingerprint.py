# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

from chaoscypher_core.services.sources.engine.extraction.domains.factory import (
    clear_domain_registry_cache,
)
from chaoscypher_core.services.sources.engine.extraction.domains.fingerprint import (
    resolve_domain_fingerprint,
)


def test_resolve_builtin_domain():
    clear_domain_registry_cache()
    version, content_hash = resolve_domain_fingerprint("technical", "default")
    assert version == "1.9.0"  # use the real technical.jsonld version
    assert content_hash is not None and len(content_hash) == 64


def test_resolve_unknown_domain_returns_none_pair():
    clear_domain_registry_cache()
    assert resolve_domain_fingerprint("nope-not-real", "default") == (None, None)


def test_resolve_empty_domain_returns_none_pair():
    assert resolve_domain_fingerprint(None, "default") == (None, None)
