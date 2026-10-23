# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

from chaoscypher_core.services.sources.engine.extraction.domains.factory import (
    clear_domain_registry_cache,
)
from chaoscypher_core.services.sources.engine.extraction.domains.fingerprint import (
    resolve_domain_fingerprint,
)


def _technical_version() -> str:
    """The shipped technical plugin's version, read from its file (not pinned by hand)."""
    import json

    from chaoscypher_core.services.sources.engine.extraction.domains.registry import (
        DomainRegistry,
    )

    path = DomainRegistry.builtin_plugin_dir() / "technical.jsonld"
    return str(json.loads(path.read_text(encoding="utf-8"))["version"])


def test_resolve_builtin_domain():
    clear_domain_registry_cache()
    version, content_hash = resolve_domain_fingerprint("technical", "default")
    assert version == _technical_version()
    assert content_hash is not None and len(content_hash) == 64


def test_resolve_unknown_domain_returns_none_pair():
    clear_domain_registry_cache()
    assert resolve_domain_fingerprint("nope-not-real", "default") == (None, None)


def test_resolve_empty_domain_returns_none_pair():
    assert resolve_domain_fingerprint(None, "default") == (None, None)
