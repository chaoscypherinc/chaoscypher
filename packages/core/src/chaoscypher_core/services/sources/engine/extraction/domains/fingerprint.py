# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Domain plugin content fingerprint.

A version-stable sha256 of a domain plugin's content: for a configured
(``.jsonld``) domain, the whole validated config (``DomainConfigModel``,
defaults filled, so an absent table and an empty one hash alike) without
the presentation keys (:data:`PRESENTATION_KEYS`: icons, colours, author,
version, the domain's own description). Until 2026-10-06 it hashed the
guidance, templates, examples and naming tables only, so the entity
exclusions, extraction limits, strict types, filtering and evidence modes,
type aliases, content exclusions and the extraction and normalizer
overrides changed a graph without changing the hash (audit, domain audit).
A domain object without a config (a Python plugin) hashes what its
accessors return. Either way the universal tables every domain falls back
to or extends (:func:`universal_tables`: the universal naming conventions,
the universal relation vocabulary, role words, articles and function words)
join the hash, so editing one moves every domain's fingerprint (until audit
wave 5 they were read by every domain and hashed by none). The SAME function
is used at extraction time (to stamp the source row) and at read time (to
detect drift), so the two hashes are always comparable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
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


PRESENTATION_KEYS = frozenset({"@context", "@type", "@id", "author", "builtin", "version", "icon"})
"""Top-level config keys that only present a domain (the version is the fingerprint's other half)."""

NESTED_PRESENTATION_KEYS = frozenset({"icon", "color"})
"""Keys inside templates and other blocks that only present them."""


def _without_presentation(value: Any, *, top: bool = False) -> Any:
    """``value`` with the presentation keys left out, recursively."""
    if isinstance(value, dict):
        skip = PRESENTATION_KEYS | {"description"} if top else NESTED_PRESENTATION_KEYS
        return {k: _without_presentation(v) for k, v in value.items() if k not in skip}
    if isinstance(value, list):
        return [_without_presentation(v) for v in value]
    return value


def domain_config_content(config: dict[str, Any]) -> dict[str, Any]:
    """What a configured domain's fingerprint hashes: the validated config, presentation left out."""
    from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
        DomainConfigModel,
    )

    validated = DomainConfigModel.model_validate(config).model_dump(mode="json")
    content = _without_presentation(validated, top=True)
    return content if isinstance(content, dict) else {}


def universal_tables() -> dict[str, Any]:
    """The tables a domain inherits without declaring them, as sorted JSON values.

    The universal naming conventions (``DEFAULT_NAME_CONVENTIONS``), the
    universal relation vocabulary, the role words, the articles that open a
    description and the function words that are never relation evidence.
    """
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        ARTICLES,
        DEFAULT_NAME_CONVENTIONS,
        UNIVERSAL_RELATION_VOCABULARY,
        UNIVERSAL_ROLE_WORDS,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        FUNCTION_WORDS,
    )

    return {
        "conventions": DEFAULT_NAME_CONVENTIONS.as_dict(),
        "relation_vocabulary": UNIVERSAL_RELATION_VOCABULARY,
        "role_words": sorted(UNIVERSAL_ROLE_WORDS),
        "articles": sorted(ARTICLES),
        "function_words": sorted(FUNCTION_WORDS),
    }


def _universal_blob() -> str:
    """:func:`universal_tables` as canonical JSON."""
    return json.dumps(universal_tables(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_domain_content_hash(domain: Any) -> str:
    """Return the sha256 hex fingerprint of ``domain``'s extraction content.

    A configured domain (one with a ``config`` dict) hashes
    :func:`domain_config_content`; any other domain its guidance,
    templates, examples and naming tables; both with the
    :func:`universal_tables`. Deterministic regardless of dict key ordering
    (``sort_keys=True``).
    """
    config = getattr(domain, "config", None)
    if isinstance(config, dict):
        blob = json.dumps(
            domain_config_content(config), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return hashlib.sha256(f"{blob}{_SEP}{_universal_blob()}".encode()).hexdigest()
    entity_guidance = domain.get_entity_guidance() or ""
    relationship_guidance = domain.get_relationship_guidance() or ""
    templates = json.dumps(
        domain.get_templates(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    examples = json.dumps(
        domain.get_examples(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    parts = [entity_guidance, relationship_guidance, templates, examples]
    # The naming and relation tables shape the graph too (the dedup, the
    # resolver, the evidence validator read them). Hashed only when declared,
    # so a domain without them keeps the hash it had before they existed.
    get_tables = getattr(domain, "get_identity_tables", None)
    tables = get_tables() if callable(get_tables) else {}
    if tables:
        parts.append(json.dumps(tables, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    parts.append(_universal_blob())
    blob = _SEP.join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def stack_content_hash(hashes: Mapping[str, str], packs: Iterable[str]) -> str | None:
    """The content hash of a pack choice: one member's own hash, or one hash over several.

    ``hashes`` maps each registered domain's name to its content hash
    (:func:`compute_domain_content_hash`); ``packs`` is the choice (order and
    repeats do not matter; none is the floor alone). One pack (or the floor)
    keeps its own hash, so a single-pack source's stored hash is unchanged;
    several packs hash their names and hashes, sorted, so the source is stale
    when any of them changes. None when a member's hash is unknown (a plugin
    since removed): "plugin gone" is not stale.
    """
    from chaoscypher_core.services.sources.engine.extraction.domains.core_types import (
        FLOOR_NAME,
    )

    names = sorted({p for p in packs if p and p != FLOOR_NAME}) or [FLOOR_NAME]
    if any(n not in hashes for n in names):
        return None
    if len(names) == 1:
        return hashes[names[0]]
    blob = _SEP.join(f"{n}={hashes[n]}" for n in names)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def resolve_domain_fingerprint(
    effective_domain: str | None,
    database_name: str,
) -> tuple[str | None, str | None]:
    """Resolve ``(version, content_hash)`` for a domain by name or for the packs' label.

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


__all__ = [
    "NESTED_PRESENTATION_KEYS",
    "PRESENTATION_KEYS",
    "DomainFingerprint",
    "compute_domain_content_hash",
    "domain_config_content",
    "resolve_domain_fingerprint",
    "stack_content_hash",
    "universal_tables",
]
