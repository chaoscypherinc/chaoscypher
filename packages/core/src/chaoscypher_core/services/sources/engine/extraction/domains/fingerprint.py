# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Domain plugin content fingerprint.

A version-stable sha256 of a domain plugin's extraction-relevant content
(entity guidance + relationship guidance + node/edge templates + examples).
The SAME function is used at extraction time (to stamp the source row) and
at read time (to detect drift), so the two hashes are always comparable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


# ASCII record separator — keeps the four content blocks from colliding
# (e.g. guidance ending where templates begin) without appearing in content.
_SEP = "\x1e"


@dataclass(frozen=True)
class DomainFingerprint:
    """A domain plugin's identifying version + content hash."""

    version: str | None
    content_hash: str


def compute_domain_content_hash(domain: Any) -> str:
    """Return the sha256 hex fingerprint of ``domain``'s extraction content.

    Deterministic regardless of dict key ordering (``sort_keys=True``).
    Guidance accessors fall back to the combined ``guidance`` field, so a
    domain with only ``guidance`` hashes that text in both slots — still
    deterministic.
    """
    entity_guidance = domain.get_entity_guidance() or ""
    relationship_guidance = domain.get_relationship_guidance() or ""
    templates = json.dumps(
        domain.get_templates(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    examples = json.dumps(
        domain.get_examples(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    blob = _SEP.join([entity_guidance, relationship_guidance, templates, examples])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def resolve_domain_fingerprint(
    effective_domain: str | None,
    database_name: str,
) -> tuple[str | None, str | None]:
    """Resolve ``(version, content_hash)`` for a domain by name.

    Uses ``get_domain_registry(database_name=...)`` (no settings) so the
    write-time hash matches the read-time hash computed by the cortex/CLI
    surfaces. Returns ``(None, None)`` for an empty name, an unknown domain,
    or any registry failure — provenance is best-effort and must never block
    finalize.
    """
    if not effective_domain:
        return None, None
    try:
        from chaoscypher_core.services.sources.engine.extraction.domains import (
            get_domain_registry,
        )

        registry = get_domain_registry(database_name=database_name)
        fp = registry.get_domain_fingerprint(effective_domain)
    except Exception:  # pragma: no cover - defensive; provenance is best-effort
        import structlog

        structlog.get_logger(__name__).warning(
            "domain_fingerprint_resolution_failed", domain=effective_domain
        )
        return None, None
    if fp is None:
        return None, None
    return fp.version, fp.content_hash


__all__ = ["DomainFingerprint", "compute_domain_content_hash", "resolve_domain_fingerprint"]
