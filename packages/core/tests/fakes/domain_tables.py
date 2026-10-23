# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A shipped domain's naming conventions and relation vocabulary, read the way production reads them.

Not fakes: the real plugin tables through the product's own lookups
(``domain_resolver.domain_name_conventions`` and
``relation_vocabulary.domain_relation_vocabulary``), shared so every test
that needs "the literary domain's tables" reads them one way.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
    DomainResolver,
    domain_name_conventions,
)
from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
    RelationVocabulary,
    domain_relation_vocabulary,
)
from chaoscypher_core.settings import EngineSettings


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        NameConventions,
    )


@cache
def domain_conventions(name: str | None = "literary") -> NameConventions:
    """The name rule's conventions for domain ``name`` (the universal ones for None or unknown)."""
    return domain_name_conventions(name, DomainResolver(EngineSettings()))


@cache
def domain_vocabulary(name: str | None = "literary") -> RelationVocabulary:
    """The relation vocabulary domain ``name`` declares (empty for None or unknown)."""
    domain = get_domain_registry(EngineSettings()).get_domain(name) if name else None
    return domain_relation_vocabulary(domain)


__all__ = ["domain_conventions", "domain_vocabulary"]
