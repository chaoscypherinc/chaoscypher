# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Stable CCX IRI minting for Chaos Cypher entities (pure; no I/O).

These helpers are the single source of truth for how a local entity id
becomes a durable CCX 3.0 IRI. They are intentionally dependency-free
(stdlib only) so the exporter, importer, and mapping layers can all mint
and resolve IRIs identically — re-imports upsert by IRI, so the rule for
"what is this entity's IRI" must be deterministic everywhere.
"""

from __future__ import annotations


BASE_IRI = "urn:ccx:chaoscypher:"


def mint_iri(kind: str, local_id: str) -> str:
    """Deterministic IRI for a local entity.

    Args:
        kind: Entity kind, one of ``{node, rel, source, template}``.
        local_id: The entity's local (database) id.

    Returns:
        ``urn:ccx:chaoscypher:<kind>/<local_id>``.
    """
    return f"{BASE_IRI}{kind}/{local_id}"


def resolve_iri(kind: str, record: dict) -> str:
    """Return the entity's durable IRI.

    A persisted foreign ``ccx_iri`` (set by a prior cross-package import)
    wins, so re-exports keep the entity anchored to its original identity.
    Otherwise the IRI is minted from the local id.

    Args:
        kind: Entity kind passed through to :func:`mint_iri`.
        record: A domain dict carrying at least ``id`` and optionally
            ``ccx_iri``.

    Returns:
        The persisted ``ccx_iri`` if truthy, else a freshly minted IRI.
    """
    persisted = record.get("ccx_iri")
    return persisted if persisted else mint_iri(kind, record["id"])


def local_id_from_iri(iri: str) -> str | None:
    """Recover the local id from an IRI iff we minted it.

    Returns ``None`` for foreign IRIs (those outside our namespace) so the
    importer can tell an entity that originated here from one merged in
    from another CCX package.

    Args:
        iri: The IRI to inspect.

    Returns:
        The remainder after the ``<kind>/`` segment for a minted IRI, else
        ``None``. This is the exact inverse of :func:`mint_iri`, so a local
        id containing ``/`` round-trips verbatim.
    """
    if not iri.startswith(BASE_IRI):
        return None
    rest = iri[len(BASE_IRI) :]  # e.g. "node/a/b"
    parts = rest.split("/", 1)
    return parts[1] if len(parts) == 2 else None
