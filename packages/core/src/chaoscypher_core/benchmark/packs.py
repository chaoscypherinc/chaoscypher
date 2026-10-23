# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The packs a benchmark corpus is extracted with, and the one string that names them.

A dataset manifest lists its packs (``packs: [literary, historical]``) or
names one domain (``domain: literary``, read as a one-element list;
``domain: generic`` is the floor alone, ``packs: []``). Every extraction gets
the floor, so it is never listed (spec 2026-10-07 § 6, benchmark identity).

Pack manifests, the graph cache's slot record and result rows keep recording
one string in their ``extraction_domain`` field: :func:`packs_label`, the
packs sorted and joined with ``+``, or ``generic`` for the floor alone. One
pack's label is that pack's name and the floor's is the floor's name, so
every pack and slot recorded before packs existed keeps its identity and the
comparability rule (``results.COMPARABILITY_FIELDS``) is unchanged.

The label and the pack-name rules are the product's own
(:mod:`chaoscypher_core.services.sources.engine.extraction.domains.packs`):
a source row records the same label in ``sources.extraction_domain``, so a
benchmark slot and the sources it was extracted from read one string.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from chaoscypher_core.services.sources.engine.extraction.domains.core_types import FLOOR_NAME
from chaoscypher_core.services.sources.engine.extraction.domains.packs import (
    PACKS_SEPARATOR,
    first_domain_name,
    normalized_packs,
    packs_label,
    packs_of_label,
)


def manifest_packs(
    data: Mapping[str, Any], *, where: str, default: Iterable[str] | None = None
) -> tuple[str, ...]:
    """The packs a dataset manifest names: its ``packs`` list, else its ``domain`` as one element.

    Args:
        data: The parsed manifest.
        where: The manifest's path, for the error messages.
        default: The packs of a manifest naming neither; None makes one an error.

    Raises:
        ValueError: When the manifest names both keys, neither (without a
            ``default``), ``packs`` is not a list, or a name is not a pack's.
    """
    if "packs" in data and "domain" in data:
        msg = f"{where}: name the packs once, with 'packs' or 'domain', not both"
        raise ValueError(msg)
    if "packs" in data:
        raw = data["packs"]
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            msg = f"{where}: 'packs' must be a list of pack names, got {raw!r}"
            raise ValueError(msg)
        return normalized_packs(raw, where=where)
    if "domain" in data:
        name = data["domain"]
        if name == FLOOR_NAME:
            return ()
        return normalized_packs([name], where=where)
    if default is not None:
        return normalized_packs(default, where=where)
    msg = f"{where}: extraction dataset missing 'packs' (or 'domain')"
    raise ValueError(msg)


__all__ = [
    "PACKS_SEPARATOR",
    "first_domain_name",
    "manifest_packs",
    "normalized_packs",
    "packs_label",
    "packs_of_label",
]
