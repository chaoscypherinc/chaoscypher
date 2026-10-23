# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The domain stack: the floor plus the packs a source extracts with (spec 2026-10-07 § 4).

Every extraction gets the floor (``generic.jsonld``: the eight core types, the
core labels, the shared rules); a pack only adds to it. :class:`DomainStack`
is the one object the extraction plan reads its prompt inputs from: the node
lines are the floor's core types with each pack's subtypes folded under their
core type, the edge lines are the floor's labels plus each pack's, and the
guidance, exclusions and examples are the floor's followed by each pack's.
Nothing a pack declares replaces something of the floor's.

:meth:`DomainStack.resolve` is the constructor; :meth:`DomainStack.from_stored`
reads packs back from storage, and :meth:`DomainStack.from_domain_name` is the
shim for callers that still hold one domain name or the packs' label.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

import structlog

from chaoscypher_core.exceptions import OperationError
from chaoscypher_core.services.sources.engine.extraction.domains.core_types import (
    CORE_TYPES,
    FLOOR_NAME,
)
from chaoscypher_core.services.sources.engine.extraction.domains.packs import (
    InvalidPacksError,
    packs_label,
    stored_name_packs,
)
from chaoscypher_core.services.sources.engine.extraction.utils.template_formatter import (
    format_edge_line,
    format_entity_examples,
    format_node_line,
    format_relationship_examples,
    template_name_to_snake_case,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        NameConventions,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
        ExclusionRule,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
        ConfigurableDomain,
        SubtypeSpec,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        RelationVocabulary,
    )
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


PACK_CONTRIBUTION_REPORT_CHARS: Final[int] = 1200
"""A report mark, enforced nowhere: a pack whose rendered contribution to either pass exceeds
this many characters is marked in the contribution report (:func:`format_contribution_table`,
the converter's report). The prompt budget is asserted on whole rendered prompts instead
(``test_domain_stack``); pruning a pack is a separate decision (spec decision 6)."""

PASSES: Final[tuple[str, str]] = ("pass1", "pass2")
"""The two prompts a stack renders: pass 1 (entities) and pass 2 (relationships)."""


def _role(domain: Any) -> str | None:
    """A registered domain's role (``floor``/``pack``), or None for anything else."""
    role = getattr(domain, "role", None)
    return role if isinstance(role, str) else None


@dataclass(frozen=True)
class DomainStack:
    """The floor and the chosen packs, the one source of the extraction prompt's inputs.

    Attributes:
        floor: The floor domain (``role == "floor"``).
        packs: The packs, sorted by name, each at most once.
    """

    floor: ConfigurableDomain
    packs: tuple[ConfigurableDomain, ...] = ()

    # ------------------------------------------------------------------ #
    #  Construction
    # ------------------------------------------------------------------ #

    @classmethod
    def resolve(cls, registry: Any, packs: Sequence[str]) -> DomainStack:
        """The floor of ``registry`` plus the packs named in ``packs``.

        Args:
            registry: A ``DomainRegistry`` (anything with ``get_domain(name)``).
            packs: Pack names; order and repeats do not matter. Empty is the
                floor alone.

        Returns:
            The stack, its packs sorted by name.

        Raises:
            OperationError: When the registry has no floor (the built-in
                ``generic`` plugin did not load).
            InvalidPacksError: When a name is not a registered pack (the
                floor's own name included).
        """
        floor = registry.get_domain(FLOOR_NAME)
        if _role(floor) != "floor":
            msg = f"the domain registry has no floor named {FLOOR_NAME!r}"
            raise OperationError(msg, operation="resolve_domain_stack")
        chosen: dict[str, ConfigurableDomain] = {}
        for name in packs:
            domain = registry.get_domain(name)
            if _role(domain) != "pack":
                msg = f"{name!r} is not a registered pack"
                raise InvalidPacksError(msg)
            chosen[str(domain.name)] = cast("ConfigurableDomain", domain)
        return cls(
            floor=cast("ConfigurableDomain", floor),
            packs=tuple(chosen[name] for name in sorted(chosen)),
        )

    @classmethod
    def from_domain_name(cls, registry: Any, name: str | None) -> DomainStack:
        """The stack for a caller that holds one domain name or the packs' label.

        ``None`` and the floor's name are the floor alone; a pack's name is
        that pack on the floor; a label (``historical+literary``,
        :func:`~chaoscypher_core.services.sources.engine.extraction.domains.packs.packs_label`)
        is its packs on the floor. A name the registry does not hold as a pack
        (a plugin since removed) is left out, as a missing domain always fell
        back to generic; it is logged (:meth:`from_stored`).
        """
        return cls.from_stored(registry, stored_name_packs(name))

    @classmethod
    def from_stored(cls, registry: Any, packs: Sequence[str]) -> DomainStack:
        """The stack for pack names read back from storage (a source row, a graph's sources).

        :meth:`resolve`, except that a name the registry no longer holds as a
        pack (a plugin since removed) is left out and logged
        (``domain_stack_unknown_domain_floor_only``) rather than refused: what
        was stored cannot be re-chosen, and the floor always applies.
        """
        known: list[str] = []
        for name in packs:
            if not name or name == FLOOR_NAME:
                continue  # the floor is always there
            if _role(registry.get_domain(name)) == "pack":
                known.append(name)
            else:
                logger.warning("domain_stack_unknown_domain_floor_only", domain=name)
        return cls.resolve(registry, known)

    def without(self, pack: str) -> DomainStack:
        """This stack less the pack named ``pack``."""
        return DomainStack(self.floor, tuple(p for p in self.packs if p.name != pack))

    # ------------------------------------------------------------------ #
    #  Identity
    # ------------------------------------------------------------------ #

    @property
    def names(self) -> tuple[str, ...]:
        """``("generic",)`` for the floor alone, else the pack names, sorted."""
        if not self.packs:
            return (FLOOR_NAME,)
        return tuple(p.name for p in self.packs)

    @property
    def members(self) -> tuple[ConfigurableDomain, ...]:
        """The floor, then the packs."""
        return (self.floor, *self.packs)

    # ------------------------------------------------------------------ #
    #  Prompt inputs
    # ------------------------------------------------------------------ #

    def node_lines(self) -> list[str]:
        """One line per core type: the floor's template, with the packs' subtypes and properties.

        ``- Person: An individual human being (subtypes: author, character,
        narrator — write Person/author) (properties: title=Title/Role, ...)``:
        the subtype values (``entity_subtype``) every pack offers under that
        core type, sorted, as the ``Core/subtype`` the model writes
        (``prompts.subtype_clause``; the type rescue places it), and the
        floor's properties followed by the packs' ``core_properties`` for it.
        """
        lines = []
        for template in self.floor.get_templates()["node_templates"]:
            core = str(template.get("name", ""))
            subtypes = sorted({s.key for p in self.packs for s in p.subtypes_of(core)})
            extra = [prop for p in self.packs for prop in p.core_properties(core)]
            lines.append(format_node_line(template, subtypes=subtypes, extra_properties=extra))
        return lines

    def edge_lines(self) -> list[str]:
        """The floor's relationship types, then each pack's extra ones (a label once)."""
        return [format_edge_line(t) for t in self.get_templates()["edge_templates"]]

    def guidance(self) -> tuple[str, str]:
        """``(entity, relationship)`` guidance: the floor's text, then each pack's under ``Pack <name>:``."""
        entity = [self.floor.get_entity_guidance() or ""]
        relationship = [self.floor.get_relationship_guidance() or ""]
        for pack in self.packs:
            if text := pack.get_entity_guidance():
                entity.append(f"Pack {pack.name}:\n{text}")
            if text := pack.get_relationship_guidance():
                relationship.append(f"Pack {pack.name}:\n{text}")
        return (
            "\n\n".join(t for t in entity if t),
            "\n\n".join(t for t in relationship if t),
        )

    def exclusions(self) -> list[ExclusionRule]:
        """The floor's entity exclusions, then each pack's (a description once)."""
        seen: set[str] = set()
        out: list[ExclusionRule] = []
        for member in self.members:
            for rule in member.get_entity_exclusions():
                if rule.description not in seen:
                    seen.add(rule.description)
                    out.append(rule)
        return out

    def examples(self, max_chars: int | None = None) -> tuple[str, str]:
        """``(entity, relationship)`` examples: the floor's, then each pack's, each capped at ``max_chars``."""
        entity, relationship = [], []
        for member in self.members:
            examples = member.get_examples()
            if not examples:
                continue
            entity.append(format_entity_examples(examples, max_chars=max_chars))
            relationship.append(format_relationship_examples(examples, max_chars=max_chars))
        return (
            "\n\n".join(t for t in entity if t),
            "\n\n".join(t for t in relationship if t),
        )

    # ------------------------------------------------------------------ #
    #  Extraction settings
    # ------------------------------------------------------------------ #

    def limits(self) -> dict[str, Any]:
        """The ``extraction_limits``: the floor's, raised by the packs.

        A pack may only raise a limit (the schema refuses one below the
        floor's), so where two members set the same numeric limit the larger
        one holds; any other value keeps the first member's.
        """
        out: dict[str, Any] = {}
        for member in self.members:
            for key, value in member.get_extraction_limits().items():
                if key not in out or (
                    _is_number(value) and _is_number(out[key]) and value > out[key]
                ):
                    out[key] = value
        return out

    def filtering_mode(self) -> str | None:
        """The preset selector: the packs' when they name one, else the floor's.

        Packs that name different presets fall back to the floor's, so the
        choice does not depend on pack order.
        """
        modes = {m for p in self.packs if (m := p.get_filtering_mode())}
        if len(modes) == 1:
            return modes.pop()
        return self.floor.get_filtering_mode()

    def strict_entity_types(self) -> bool:
        """Whether the type list is exhaustive: the floor's switch (packs cannot set it)."""
        return self.floor.get_strict_entity_types() is True

    def core_type_names(self) -> list[str]:
        """The floor's entity type names, in order (the core types)."""
        return [
            str(t["name"]) for t in self.floor.get_templates()["node_templates"] if t.get("name")
        ]

    def named_referent_types(self) -> set[str]:
        """The core types whose entities must carry a proper name (``requires_named_referent``)."""
        return {
            str(t["name"])
            for t in self.floor.get_templates()["node_templates"]
            if t.get("name") and t.get("requires_named_referent")
        }

    def subtypes(self) -> list[SubtypeSpec]:
        """Every subtype the packs offer, pack by pack in name order."""
        return [s for p in self.packs for s in p.subtypes()]

    def normalization_rules(self) -> dict[str, list[str]]:
        """The type-rescue keywords of every member, by target type (keywords unioned, in order)."""
        out: dict[str, list[str]] = {}
        for member in self.members:
            for target, keywords in member.get_normalization_rules().items():
                merged = out.setdefault(target, [])
                merged.extend(k for k in keywords if k not in merged)
        return out

    def property_type_mapping(self) -> dict[str, dict[str, str]]:
        """Every member's ``absorbs_types`` mapping (the floor's entry wins on a clash)."""
        out: dict[str, dict[str, str]] = {}
        for member in self.members:
            for absorbed, entry in member.get_property_type_mapping().items():
                out.setdefault(absorbed, entry)
        return out

    def edge_type_constraints(self) -> dict[str, dict[str, list[str]]]:
        """Every member's ``source_types``/``target_types`` per label (types unioned on a shared label)."""
        out: dict[str, dict[str, list[str]]] = {}
        for member in self.members:
            for label, entry in member.get_edge_type_constraints().items():
                merged = out.setdefault(label, {})
                for side, types in entry.items():
                    have = merged.setdefault(side, [])
                    have.extend(t for t in types if t not in have)
        return out

    # ------------------------------------------------------------------ #
    #  The stack read as one domain (finalize, commit, quality, fingerprint)
    # ------------------------------------------------------------------ #
    # The accessors a finalize or commit path reads off a resolved domain,
    # under the same names, so every such path reads the stack the source
    # extracted with rather than one pack's object (Task 7, 2026-10-08).

    @property
    def name(self) -> str:
        """The packs' label (``historical+literary``; ``generic`` for the floor alone)."""
        return packs_label(p.name for p in self.packs)

    def get_templates(self) -> dict[str, list[dict[str, Any]]]:
        """The floor's node templates (the core types) and the edge templates: the floor's, then each pack's (a label once)."""
        floor = self.floor.get_templates()
        seen: set[str] = set()
        edges: list[dict[str, Any]] = []
        for template in [
            *floor["edge_templates"],
            *(t for p in self.packs for t in p.extra_edge_templates()),
        ]:
            slug = template_name_to_snake_case(str(template.get("name", "")))
            if slug and slug not in seen:
                seen.add(slug)
                edges.append(template)
        return {"node_templates": list(floor["node_templates"]), "edge_templates": edges}

    def get_normalization_rules(self) -> dict[str, list[str]]:
        """:meth:`normalization_rules`, under the domain accessor's name."""
        return self.normalization_rules()

    def get_title_words(self) -> list[str]:
        """Every member's declared ``title_words``, each once, the floor's first."""
        return list(dict.fromkeys(w for m in self.members for w in m.get_title_words() or ()))

    def get_type_aliases(self) -> dict[str, str]:
        """Every pack's ``type_aliases`` (the first pack in stack order wins on a shared alias)."""
        out: dict[str, str] = {}
        for pack in self.packs:
            for alias, target in pack.get_type_aliases().items():
                out.setdefault(alias, target)
        return out

    def get_content_exclusions(self) -> dict[str, Any]:
        """Every member's content exclusions: the categories and the custom patterns, each once, in member order."""
        categories: list[str] = []
        patterns: list[dict[str, Any]] = []
        for member in self.members:
            config = member.get_content_exclusions() or {}
            for name in config.get("categories") or []:
                if name not in categories:
                    categories.append(name)
            for pattern in config.get("custom_patterns") or []:
                if pattern not in patterns:
                    patterns.append(pattern)
        out: dict[str, Any] = {}
        if categories:
            out["categories"] = categories
        if patterns:
            out["custom_patterns"] = patterns
        return out

    def get_quality_scoring(self) -> dict[str, Any]:
        """The quality scores: the floor's per core type and core label, the packs' per subtype and extra label.

        ``entity_scores`` are the floor's (keyed by core type),
        ``subtype_scores`` every pack's (keyed by the ``entity_subtype``
        value, the first pack in stack order winning), ``relationship_scores``
        the floor's labels plus each pack's extra ones (a pack never
        re-declares a core label). The defaults are the floor's; a
        ``target_density`` is the first pack's that sets one, else the
        floor's. Spec 2026-10-07 § 6.
        """
        floor = self.floor.get_quality_scoring()
        subtype_scores: dict[str, Any] = {}
        relationship_scores = dict(floor.get("relationship_scores") or {})
        density = None
        for pack in self.packs:
            scoring = pack.get_quality_scoring()
            for key, score in (scoring.get("subtype_scores") or {}).items():
                subtype_scores.setdefault(key, score)
            for label, score in (scoring.get("relationship_scores") or {}).items():
                relationship_scores.setdefault(label, score)
            if density is None and "target_density" in scoring:
                density = scoring["target_density"]
        out: dict[str, Any] = {
            "entity_scores": dict(floor.get("entity_scores") or {}),
            "subtype_scores": subtype_scores,
            "relationship_scores": relationship_scores,
            "default_entity_score": floor.get("default_entity_score", 18),
            "default_relationship_score": floor.get("default_relationship_score", 15),
        }
        if density is None:
            density = floor.get("target_density")
        if density is not None:
            out["target_density"] = density
        return out

    def structural_filters(self) -> tuple[set[str], set[str]]:
        """``(structural_types, generic_types)``: the type names any member's node templates flag.

        A core type is never generic (the normalizer refuses to retype one,
        Task 4), so the floor's inert ``is_generic`` flags on Concept and
        Object are left out: an empty set lets the normalizer read its
        default generic names (``Unknown``, ``Thing``, ``Entity``).
        """
        structural: set[str] = set()
        generic: set[str] = set()
        for member in self.members:
            templates = (member.config.get("templates") or {}).get("node_templates") or []
            for template in templates:
                name = template.get("name") if isinstance(template, dict) else None
                if not name:
                    continue
                if template.get("is_structural"):
                    structural.add(str(name))
                if template.get("is_generic") and str(name) not in CORE_TYPES:
                    generic.add(str(name))
        return structural, generic

    def subtype_visuals(self) -> dict[str, dict[str, str | None]]:
        """``{entity_subtype: {"name", "of", "icon", "color"}}`` of every pack subtype (the first pack wins).

        What the UI may show for a subtype (its own icon) beside the core
        type's template, whose icon and colour a node is drawn with.
        """
        out: dict[str, dict[str, str | None]] = {}
        for spec in self.subtypes():
            out.setdefault(
                spec.key,
                {"name": spec.name, "of": spec.of, "icon": spec.icon, "color": spec.color},
            )
        return out

    # ------------------------------------------------------------------ #
    #  Naming conventions and relation vocabulary
    # ------------------------------------------------------------------ #

    def conventions(self) -> NameConventions:
        """The name rule's conventions for this stack: the floor's tables unioned with every pack's.

        ``domain_resolver.name_conventions_of_packs``; the universal ones when
        a member's tables cannot be read.
        """
        from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
            DEFAULT_NAME_CONVENTIONS,
        )
        from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
            name_conventions_of_packs,
        )

        return name_conventions_of_packs(self) or DEFAULT_NAME_CONVENTIONS

    def relation_vocabulary(self) -> RelationVocabulary:
        """The relation vocabulary the evidence validator reads: the universal one, every member's added."""
        from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
            domain_relation_vocabulary,
        )

        return domain_relation_vocabulary(self)

    def inverse_relationships(self) -> dict[str, str]:
        """Every member's inverse map (a symmetric label is its own inverse), the floor's first.

        The deduplication collapses an inverse pair to one direction and the
        commit writes the inverse edge from it: the floor's pairs
        (``parent_of``/``child_of``) hold whatever packs are chosen. A pack
        cannot contradict the floor (a guard test holds it), so a label keeps
        the first member's inverse.
        """
        out: dict[str, str] = {}
        for member in self.members:
            for label, inverse in member.get_inverse_relationships().items():
                out.setdefault(label, inverse)
        return out

    def symmetric_relationships(self) -> list[str]:
        """Every member's symmetric labels, the floor's first, each once."""
        return list(
            dict.fromkeys(label for m in self.members for label in m.get_symmetric_relationships())
        )

    # ------------------------------------------------------------------ #
    #  Budget
    # ------------------------------------------------------------------ #

    def rendered_sizes(self, settings: EngineSettings | None = None) -> tuple[int, int]:
        """Characters of the pass-1 and pass-2 prompts this stack renders, on an empty chunk.

        The prompts are the ones extraction sends (the plan's prompt inputs
        through :func:`render_plan_prompts`) with no sentences, no entity
        list and ``max_entity_index`` 0: the measure the 2026-10-07 survey
        took of today's prompts.
        """
        from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
            prompt_inputs,
        )
        from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
            render_plan_prompts,
        )
        from chaoscypher_core.settings import EngineSettings

        pass1, pass2 = render_plan_prompts(
            prompt_inputs(self, settings or EngineSettings()),
            numbered_text="",
            entity_list="",
            max_entity_index=0,
        )
        return len(pass1), len(pass2)

    def prompt_contribution(
        self, settings: EngineSettings | None = None
    ) -> dict[str, dict[str, int]]:
        """What each pack adds to each pass, in characters: the stack rendered with and without it.

        Returns:
            ``{pack name: {"pass1": chars, "pass2": chars}}``, one entry per pack.
        """
        full = self.rendered_sizes(settings)
        out: dict[str, dict[str, int]] = {}
        for pack in self.packs:
            less = self.without(pack.name).rendered_sizes(settings)
            out[pack.name] = {p: f - w for p, f, w in zip(PASSES, full, less, strict=True)}
        return out


def _is_number(value: Any) -> bool:
    """A real number (not a bool)."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def pack_contribution_table(
    registry: Any, settings: EngineSettings | None = None
) -> list[tuple[str, int, int]]:
    """``(pack, pass-1 chars, pass-2 chars)`` each registered pack adds to the floor, by name."""
    rows = []
    for name in registry.names():
        if _role(registry.get_domain(name)) != "pack":
            continue
        sizes = DomainStack.resolve(registry, [name]).prompt_contribution(settings)[name]
        rows.append((name, sizes["pass1"], sizes["pass2"]))
    return rows


def format_contribution_table(rows: Sequence[tuple[str, int, int]]) -> str:
    """The contribution rows as a Markdown table, a row above :data:`PACK_CONTRIBUTION_REPORT_CHARS` marked."""
    lines = ["| pack | pass 1 | pass 2 | above report mark |", "|---|---:|---:|---|"]
    for name, p1, p2 in rows:
        over = "yes" if max(p1, p2) > PACK_CONTRIBUTION_REPORT_CHARS else ""
        lines.append(f"| {name} | {p1} | {p2} | {over} |")
    return "\n".join(lines) + "\n"


__all__ = [
    "PACK_CONTRIBUTION_REPORT_CHARS",
    "PASSES",
    "DomainStack",
    "format_contribution_table",
    "pack_contribution_table",
]
