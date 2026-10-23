# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The ``packs`` / ``domain`` request fields every sources endpoint accepts, read once.

A source extracts with the floor every source has plus the packs it chooses
(spec 2026-10-07 § 5). An endpoint takes ``packs: list[str] | None`` and keeps
``domain: str | None`` as its one-element form, so the single-select UI that
sends ``domain`` keeps working: ``"__auto__"`` (or neither) detects,
``"generic"`` is the floor alone. :func:`forced_packs` is the one reading; the
rule itself is Core's
(:func:`~chaoscypher_core.services.sources.engine.extraction.domains.packs.requested_packs`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from chaoscypher_core.services.sources.engine.extraction.domains.packs import requested_packs


if TYPE_CHECKING:
    from collections.abc import Sequence


PACKS_DESCRIPTION = (
    "The packs to extract with, on the floor every source has (e.g. "
    '["historical", "literary"]; [] is the floor alone). Omit (with no domain) '
    "to detect them."
)
DOMAIN_DESCRIPTION = (
    "One pack, the single-element form of 'packs' ('generic' is the floor alone, "
    "'__auto__' detects). Do not send both."
)


def forced_packs(packs: Sequence[str] | None, domain: str | None) -> tuple[str, ...] | None:
    """The packs a request forces, or None to detect.

    Raises:
        InvalidPacksError: (a ``ValidationError``, HTTP 400, ``field="packs"``)
            When both fields name packs, the auto sentinel is mixed with
            packs, or a name is not a pack name.
    """
    return requested_packs(packs, domain)


class PacksFields(BaseModel):
    """The ``packs`` field and its one-element ``domain`` form, for a JSON request body."""

    packs: list[str] | None = Field(default=None, description=PACKS_DESCRIPTION)
    domain: str | None = Field(default=None, description=DOMAIN_DESCRIPTION)

    def forced_packs(self) -> tuple[str, ...] | None:
        """The packs this request forces, or None to detect (:func:`forced_packs`)."""
        return forced_packs(self.packs, self.domain)


__all__ = ["DOMAIN_DESCRIPTION", "PACKS_DESCRIPTION", "PacksFields", "forced_packs"]
