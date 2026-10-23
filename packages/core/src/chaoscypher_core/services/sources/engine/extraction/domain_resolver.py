# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Domain resolver protocol and standalone implementation.

Provides domain-specific configuration for entity deduplication:
the name rule's conventions (title words, gendered titles, generation
words, name forms), symmetric relationships and inverse relationship
mappings. Each is read off the source's :class:`DomainStack` (the floor plus
its packs), so the floor's tables hold whatever packs are chosen; a graph
whose sources chose different packs reads the union of their packs
(:func:`conventions_of_sources`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DEFAULT_NAME_CONVENTIONS,
    UNIVERSAL_ROLE_WORDS,
    NameConventions,
    NameTables,
    vocabulary_words,
)
from chaoscypher_core.services.sources.engine.extraction.domains.packs import (
    first_domain_name,
    source_packs,
    stored_name_packs,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        RelationVocabulary,
    )
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class DomainResolverProtocol(Protocol):
    """Protocol for resolving domain-specific configuration.

    Implemented by :class:`DomainResolver` (standalone) and
    :class:`ExtractionService` (full-stack).
    """

    def get_domain_title_words(self, domain_name: str | None) -> frozenset[str] | None:
        """Get title words for a domain.

        Args:
            domain_name: Name of the domain.

        Returns:
            Frozenset of lowercase title words, or None.

        """
        ...

    def get_domain_name_conventions(self, domain_name: str | None) -> NameConventions | None:
        """Get the name rule's conventions for a domain's stack (:func:`name_conventions_of_packs`).

        Args:
            domain_name: Name of the domain.

        Returns:
            The domain's conventions, or None when the domain is unknown.

        """
        ...

    def get_domain_symmetric_relationships(self, domain_name: str | None) -> list[str]:
        """Get symmetric relationship types for a domain.

        Args:
            domain_name: Name of the domain.

        Returns:
            List of symmetric relationship type names.

        """
        ...

    def get_domain_inverse_relationships(self, domain_name: str | None) -> dict[str, str]:
        """Get inverse relationship mappings for a domain.

        Args:
            domain_name: Name of the domain.

        Returns:
            Mapping of edge type to inverse edge type.

        """
        ...


class DomainResolver:
    """Standalone domain resolver using DomainRegistry.

    Used by ``extract_entities_from_groups()`` and other standalone
    callers that don't have an ``ExtractionService`` instance.
    """

    def __init__(self, settings: EngineSettings) -> None:
        """Initialize domain resolver.

        Args:
            settings: Settings instance for domain registry lookup.

        """
        self.settings = settings
        self._stacks: dict[str | None, DomainStack | None] = {}

    def _resolve_domain(self, domain_name: str | None) -> Any | None:
        """The ``DomainStack`` a domain name or the packs' label names (:meth:`_resolve_stack`).

        What the standalone path's structural filter and normalization read,
        the same object the service's ``_resolve_domain`` returns.

        Args:
            domain_name: Name of the domain or the packs' label.

        Returns:
            The stack, or None for no name or an unreadable registry.

        """
        if not domain_name:
            return None
        return self._resolve_stack(domain_name)

    def get_domain_title_words(self, domain_name: str | None) -> frozenset[str] | None:
        """Get title words from domain config.

        Args:
            domain_name: Name of the domain.

        Returns:
            Frozenset of lowercase title words, or None.

        """
        domain = self._resolve_domain(domain_name)
        if not domain:
            return None
        try:
            words = domain.get_title_words()
            return frozenset(w.lower() for w in words) if words else None
        except Exception:
            logger.debug("domain_title_words_unavailable", domain=domain_name)
            return None

    def _resolve_stack(
        self, domain_name: str | None, *, strict: bool = False
    ) -> DomainStack | None:
        """The stack a source of ``domain_name`` extracts with (:func:`domain_stack`), cached.

        ``strict`` (a forced choice) raises on an unregistered pack or an
        unreadable registry, and a failure is not cached, so a retry looks
        again; a lenient miss is cached as None.
        """
        cached = self._stacks.get(domain_name)
        if domain_name in self._stacks and (cached is not None or not strict):
            return cached
        self._stacks[domain_name] = domain_stack(domain_name, self.settings, strict=strict)
        return self._stacks[domain_name]

    def get_domain_name_conventions(self, domain_name: str | None) -> NameConventions | None:
        """Get the name rule's conventions of the domain's stack (the floor's and the pack's tables).

        Args:
            domain_name: Name of the domain; None or ``generic`` is the floor alone.

        Returns:
            The stack's conventions (:func:`name_conventions_of_packs`), or
            None when the registry cannot be read.

        """
        stack = self._resolve_stack(domain_name)
        return name_conventions_of_packs(stack) if stack is not None else None

    def get_domain_symmetric_relationships(self, domain_name: str | None) -> list[str]:
        """Get the symmetric relationship types of the domain's stack (the floor's and the pack's).

        Args:
            domain_name: Name of the domain; None or ``generic`` is the floor alone.

        Returns:
            List of symmetric relationship type names.

        """
        return stack_symmetric_relationships(self._resolve_stack(domain_name))

    def get_domain_inverse_relationships(self, domain_name: str | None) -> dict[str, str]:
        """Get the inverse relationship map of the domain's stack (the floor's and the pack's).

        Args:
            domain_name: Name of the domain; None or ``generic`` is the floor alone.

        Returns:
            Mapping of edge type to inverse edge type.

        """
        return stack_inverse_relationships(self._resolve_stack(domain_name))


def domain_stack(
    domain_name: str | None, settings: EngineSettings | None, *, strict: bool = False
) -> DomainStack | None:
    """The stack a source of ``domain_name`` extracts with, or None when the registry cannot be read.

    ``domain_name`` is one name or the packs' label (``historical+literary``,
    :func:`~chaoscypher_core.services.sources.engine.extraction.domains.packs.packs_label`):
    None and ``generic`` are the floor alone, a pack's name is the floor plus
    that pack, a label the floor plus its packs, a name no longer registered
    is left out (:func:`packs_stack`). With ``strict`` (a user's forced
    choice) an unregistered pack or an unreadable registry raises instead.
    The one lookup both resolvers make (:class:`DomainResolver`, which
    ``ExtractionService`` delegates to for every stack it reads).
    """
    return packs_stack(stored_name_packs(domain_name), settings, strict=strict)


def packs_stack(
    packs: Iterable[str], settings: EngineSettings | None, *, strict: bool = False
) -> DomainStack | None:
    """The floor plus ``packs`` as stored (``DomainStack.from_stored``), or None when the registry cannot be read.

    Order and repeats do not matter; the floor's name is no pack, and a pack
    no longer registered is left out (logged). With ``strict`` the packs are
    read with ``DomainStack.resolve``: an unregistered pack, or a registry
    failure, raises (a forced choice must not run silently as the floor).
    """
    chosen = sorted(set(packs))
    try:
        from chaoscypher_core.services.sources.engine.extraction.domains import (
            get_domain_registry,
        )
        from chaoscypher_core.services.sources.engine.extraction.domains.stack import (
            DomainStack,
        )

        registry = get_domain_registry(settings)
        if strict:
            return DomainStack.resolve(registry, chosen)
        return DomainStack.from_stored(registry, chosen)
    except Exception:
        if strict:
            raise
        logger.debug("domain_stack_unavailable", domains=chosen)
        return None


def quality_config_of(
    domain_name: str | None,
    *,
    settings: EngineSettings | None = None,
    database_name: str = "default",
) -> dict[str, Any]:
    """The quality scoring config of the stack a source extracted with: the one lookup every scorer makes.

    ``domain_name`` is the source's ``extraction_domain`` (one pack's name or
    the packs' label; ``generic`` is the floor alone): the floor's scores per
    core type and core label, the packs' per subtype and extra label
    (``DomainStack.get_quality_scoring``). No name gives ``{}`` (the scorer's
    defaults), as before; an unreadable registry logs and gives ``{}``.
    Used by the extraction-time cache (``orchestration.cache_quality_scores``),
    Cortex (the source mappers and the quality service), the Neuron rescore
    handler and the CLI.
    """
    if not domain_name:
        return {}
    try:
        from chaoscypher_core.services.sources.engine.extraction.domains import (
            get_domain_registry,
        )
        from chaoscypher_core.services.sources.engine.extraction.domains.stack import (
            DomainStack,
        )

        registry = get_domain_registry(settings=settings, database_name=database_name)
        return DomainStack.from_domain_name(registry, domain_name).get_quality_scoring()
    except Exception as exc:
        logger.warning(
            "failed_to_load_domain_quality_config",
            domain=domain_name,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return {}


def stack_symmetric_relationships(stack: DomainStack | None) -> list[str]:
    """:meth:`DomainStack.symmetric_relationships`, or none for no stack or an unreadable member."""
    if stack is None:
        return []
    try:
        return stack.symmetric_relationships()
    except Exception:
        logger.debug("symmetric_relationships_unavailable", domains=stack.names)
        return []


def stack_inverse_relationships(stack: DomainStack | None) -> dict[str, str]:
    """:meth:`DomainStack.inverse_relationships`, or none for no stack or an unreadable member."""
    if stack is None:
        return {}
    try:
        return stack.inverse_relationships()
    except Exception:
        logger.debug("domain_inverse_relationships_unavailable", domains=stack.names)
        return {}


def source_domain_name(row: Mapping[str, Any] | None) -> str | None:
    """The one domain name of a source row: its first pack, or ``generic``; None when it names none.

    A shim for one release, for callers that still read one name: a row
    carries a list of packs (:func:`source_packs`), and this is the first of
    them. None when the row names no domain at all (never forced, never
    extracted).
    """
    if not row:
        return None
    named = (
        row.get("forced_domain")
        or row.get("extraction_domain")
        or row.get("forced_packs") is not None
        or row.get("extraction_packs")
    )
    if not named:
        return None
    return first_domain_name(source_packs(row))


def sources_packs(rows: Iterable[Mapping[str, Any] | None]) -> tuple[str, ...]:
    """The union of the packs ``rows`` extract with (:func:`source_packs`), sorted."""
    return tuple(sorted({p for row in rows for p in source_packs(row)}))


def conventions_of_packs(
    packs: Iterable[str], settings: EngineSettings | None = None
) -> NameConventions:
    """The conventions names are read by under the floor and ``packs``: their tables' union.

    The one rule the post-commit identity resolver and retrieval's name
    matcher share for a graph's sources (:func:`conventions_of_sources`):
    sources that chose different packs are read by the union of their packs'
    tables over the floor's (spec 2026-10-07 § 6: a pack cannot contradict
    the floor, so the union is coherent), never skipped nor defaulted. A pack
    no longer registered is left out (:func:`packs_stack`); an unreadable
    registry is the universal conventions.
    """
    stack = packs_stack(packs, settings)
    conventions = name_conventions_of_packs(stack) if stack is not None else None
    return conventions or DEFAULT_NAME_CONVENTIONS


def conventions_of_sources(
    rows: Iterable[Mapping[str, Any] | None], settings: EngineSettings | None = None
) -> NameConventions:
    """The conventions a graph of these source rows reads names by: :func:`conventions_of_packs` of their packs."""
    return conventions_of_packs(sources_packs(rows), settings)


def vocabulary_of_sources(
    rows: Iterable[Mapping[str, Any] | None], settings: EngineSettings | None = None
) -> RelationVocabulary | None:
    """The relation vocabulary a graph of these source rows reads: the floor's and their packs' together.

    The stack :func:`conventions_of_sources` reads names by (the floor plus
    the union of the rows' packs, :func:`packs_stack`), read for its
    relations (``DomainStack.relation_vocabulary``). None when the rows name
    packs and the stack cannot be built (``packs_stack`` logs a registry
    failure at debug only), so the reader records its fault instead of
    reading the universal vocabulary as if it were the packs'; the universal
    vocabulary when they name none and the registry cannot be read.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        UNIVERSAL_VOCABULARY,
    )

    packs = sources_packs(rows)
    stack = packs_stack(packs, settings)
    if stack is not None:
        return stack.relation_vocabulary()
    return None if packs else UNIVERSAL_VOCABULARY


def _optional_table(domain: Any, getter: str) -> Any:
    """A table a domain object may not implement (a plugin written before the table existed)."""
    method = getattr(domain, getter, None)
    return method() if callable(method) else None


def name_tables_of_domain(domain: Any) -> NameTables:
    """The naming tables one domain object (the floor or a pack) declares.

    The one reading of a domain's naming tables (``title_words``,
    ``feminine_titles``, ``masculine_titles``, ``generation_words``,
    ``name_forms``, ``title_equivalents``, the relation and role words
    that make a name a descriptor: the ``relation_vocabulary`` words and
    ``role_words``, and the types whose names name a person, its node
    templates and subtypes marked ``"names_a_person": true``). A table the
    domain leaves empty declares nothing (:meth:`NameConventions.build`
    falls back to the universal one when no member declares it).
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        domain_relation_config,
    )

    return NameTables(
        titles=tuple(domain.get_title_words() or ()),
        feminine_titles=tuple(domain.get_feminine_titles() or ()),
        masculine_titles=tuple(domain.get_masculine_titles() or ()),
        generation_words=domain.get_generation_words() or None,
        name_forms=domain.get_name_forms() or None,
        # The universal kin words and role words, extended by the domain's (one source).
        relation_words=(
            *vocabulary_words(domain_relation_config(domain)),
            *sorted(UNIVERSAL_ROLE_WORDS),
            *(_optional_table(domain, "get_role_words") or []),
        ),
        title_equivalents=tuple(_optional_table(domain, "get_title_equivalents") or ()),
        person_types=_optional_table(domain, "get_person_types") or None,
    )


def name_conventions_of_packs(stack: DomainStack) -> NameConventions | None:
    """The name rule's conventions of a stack: the floor's tables unioned with every pack's.

    ``NameConventions.build(floor, *packs)`` over :func:`name_tables_of_domain`
    of each member, so one pack's conventions are the floor's plus its own
    and two packs' are the floor's plus both (a pack cannot contradict the
    floor). Both resolvers (:class:`DomainResolver`, ``ExtractionService``)
    answer ``get_domain_name_conventions`` with it, and so do the
    post-commit resolver and retrieval for a graph's sources
    (:func:`conventions_of_sources`). None when a member's tables cannot be
    read.
    """
    try:
        return NameConventions.build(*(name_tables_of_domain(m) for m in stack.members))
    except Exception:
        logger.debug("domain_name_conventions_unavailable", domains=stack.names)
        return None


def domain_name_conventions(domain_name: str | None, resolver: Any) -> NameConventions:
    """The conventions the name rule uses for a source of ``domain_name``: its stack's.

    The floor's tables with the pack's (None and ``generic`` are the floor
    alone: every source has the floor), or the universal ones when the
    resolver can read none. The lookup the pre-commit deduplication
    (``run_deduplication``) makes; the post-commit identity resolver reads the
    union over its graph's sources (:func:`conventions_of_sources`), the same
    tables for a graph of one source. ``resolver`` answers
    ``get_domain_name_conventions`` (:class:`DomainResolverProtocol`).
    """
    return resolver.get_domain_name_conventions(domain_name) or DEFAULT_NAME_CONVENTIONS


def domain_title_words(domain_name: str | None, resolver: Any) -> frozenset[str]:
    """The title words of :func:`domain_name_conventions` (the domain's, or the universal ones)."""
    return domain_name_conventions(domain_name, resolver).titles


def registered_name_conventions(settings: EngineSettings | None = None) -> list[NameConventions]:
    """The universal conventions, the floor's, and the floor plus each pack's, each distinct set once.

    What a recipe runs the name rule's samples under (the extraction recipe,
    the resolver's and the retrieval recipe's naming part), so a guard that
    only fires on a pack's tables (gendered titles, generation words, name
    forms, relation words) moves the recipe whichever packs the caller names.
    The sets are the floor alone and each pack's floor plus pack, not every
    combination of packs: the conventions of several packs are the union of
    these (:meth:`NameConventions.build`), every entry of which is in the
    single-pack set of the pack that declares it, so a changed table of any
    pack changes one of these sets. Ordered by content, so the order is the
    same in every process.
    """
    import json

    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack

    registry = get_domain_registry(settings)
    packs = sorted(
        str(info["name"])
        for info in registry.list_domain_info()
        if getattr(registry.get_domain(str(info["name"])), "role", None) == "pack"
    )
    stacks = [
        DomainStack.resolve(registry, []),
        *(DomainStack.resolve(registry, [p]) for p in packs),
    ]
    found: dict[str, NameConventions] = {}
    for conv in [DEFAULT_NAME_CONVENTIONS, *(name_conventions_of_packs(s) for s in stacks)]:
        if conv is not None:
            found.setdefault(json.dumps(conv.as_dict(), sort_keys=True), conv)
    return [found[key] for key in sorted(found)]
