# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The ccx import's mapping of a bundle onto the core entity types (spec 2026-10-07 § 6).

A database from before the core types is not migrated in place: it is
exported with the version it was made with, the product is updated, and the
export is imported. The import moves the old vocabulary onto the core one,
with the one mapping table and the one placing the commit uses
(``type_rescue.core_typed``):

* every knowledge node whose ``entity_type`` is not a core type is stored
  under its core type, the old name kept as ``properties.entity_subtype``
  (unless the node carries one); the table is read with the packs the
  bundle's sources were extracted with (their pack-qualified rows first,
  :func:`bundle_packs`), then plain. A name the table does not hold is kept
  as ``Concept`` flagged ``unplaced_type`` with ``written_type``;
* a node template of the bundle named by an old type (``Character``) is not
  created: its nodes take their core type's template (one per core type,
  ``commit.template.template_from_suggestion`` over the floor's
  suggestion). The product's own templates (``system_*``, the importer's
  ``Imported Entity`` default) and the core types' templates are imported
  as before;
* edges are untouched.

A bundle already on the core types maps nothing. :class:`CoreTypeMapping`
records what it moved (:meth:`CoreTypeMapping.as_dict`), which the import
reports in ``ImportStats.core_types_mapped`` and its log line.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from chaoscypher_core.constants import SYSTEM_TEMPLATE_IDS
from chaoscypher_core.services.export import ccx_identity
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    UNPLACED_TYPE_KEY,
)
from chaoscypher_core.services.sources.engine.extraction.domains.core_types import is_core_type
from chaoscypher_core.services.sources.engine.extraction.domains.packs import (
    ordered_packs,
    stored_packs,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import core_typed
from chaoscypher_core.templates.default_templates import get_all_default_templates


SOURCE_TYPE = "ccx:Source"
"""The ``@type`` of a source record in ``sources.jsonl``."""

PACKS_KEY = "extractionPacks"
"""A source record's packs, in stack order (written since the core types)."""

DOMAIN_KEY = "extractionDomain"
"""A source record's domain name or packs label (every bundle that has one)."""

DEFAULT_NODE_TEMPLATE_NAME = "Imported Entity"
"""The importer's own node template for a node whose ``@type`` matches no imported template."""

_SYSTEM_ID_PREFIX = "system_"

APP_GRAPH_TEMPLATE_NAMES: Final[frozenset[str]] = frozenset(
    str(t["name"]) for t in get_all_default_templates() if t["id"] in SYSTEM_TEMPLATE_IDS
)
"""The app graphs' node templates (``Lens``, ``Workflow``, ``Workflow Step``): never an entity type.

Kept by name as well as by id: a ``Replace``-mode import stores every
template under a fresh ``template_*`` id, so a database loaded from a
package exports its ``Lens`` without the ``system_lens`` id, and the
next import would otherwise re-point it as an old type."""


def bundle_packs(records: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """The packs a bundle's sources were extracted with: each source's in its order, each pack once.

    A source's ``extractionPacks`` (stack order), else its ``extractionDomain``
    read as a name or a label (a bundle from before the packs: ``literary``);
    the floor and blanks are dropped. ``()`` when no source names one.
    """
    names: list[str] = []
    for record in records:
        if record.get("@type") != SOURCE_TYPE:
            continue
        names.extend(stored_packs(record, list_key=PACKS_KEY, name_key=DOMAIN_KEY) or ())
    return ordered_packs(names)


def is_old_type_template(member: Mapping[str, Any]) -> bool:
    """Whether a bundle's template member is a node template named by an old type (not created; re-pointed).

    A node template whose name is not a core type, unless it is one of the
    product's own (a ``system_*`` id: Note, Item, Lens, ...; the importer's
    ``Imported Entity`` default), which every database has, or an app
    graph's under any id (:data:`APP_GRAPH_TEMPLATE_NAMES`).
    """
    if (member.get("template_type") or "node") != "node":
        return False
    name = str(member.get("name") or "")
    if (
        not name
        or is_core_type(name)
        or name == DEFAULT_NODE_TEMPLATE_NAME
        or name in APP_GRAPH_TEMPLATE_NAMES
    ):
        return False
    local_id = ccx_identity.local_id_from_iri(str(member.get("@id") or ""))
    return not (local_id or "").startswith(_SYSTEM_ID_PREFIX)


@dataclass
class CoreTypeMapping:
    """One import's mapping onto the core types: the bundle's packs, its re-pointed templates, the counts.

    Attributes:
        packs: The bundle's packs (:func:`bundle_packs`), read first.
        old_template_names: Template IRI -> name of the bundle's node
            templates named by an old type (:func:`is_old_type_template`).
        mapped: Old ``entity_type`` -> ``{"core_type", "count"}`` (and
            ``"unplaced": True`` for a name the table does not hold).
        templates: Old template name -> the core type its nodes take.
    """

    packs: tuple[str, ...] = ()
    old_template_names: dict[str, str] = field(default_factory=dict)
    mapped: dict[str, dict[str, Any]] = field(default_factory=dict)
    templates: dict[str, str] = field(default_factory=dict)

    def skip_template(self, member: Mapping[str, Any]) -> bool:
        """Record ``member`` as re-pointed when it is named by an old type; whether it is."""
        if not is_old_type_template(member) or not member.get("@id"):
            return False
        name = str(member["name"])
        self.old_template_names[str(member["@id"])] = name
        self.templates[name] = str(core_typed({"type": name}, self.packs)["type"])
        return True

    def place(self, node_kwargs: Mapping[str, Any]) -> dict[str, Any]:
        """``node_kwargs`` (``jsonld_entity_to_node``) with the node on its core type; the move counted.

        The node's ``entity_type`` and ``properties`` as ``core_typed`` places
        them with the bundle's packs. A node on a core type, or with no type,
        comes back as it was.
        """
        written = node_kwargs.get("entity_type")
        if not written or is_core_type(str(written)):
            return dict(node_kwargs)
        placed = core_typed(
            {"type": written, "properties": node_kwargs.get("properties") or {}}, self.packs
        )
        core = str(placed["type"])
        row = self.mapped.setdefault(str(written), {"core_type": core, "count": 0})
        row["count"] += 1
        if placed["properties"].get(UNPLACED_TYPE_KEY) is True:
            row["unplaced"] = True
        return {**node_kwargs, "entity_type": core, "properties": placed["properties"]}

    def core_template_type(
        self, node_kwargs: Mapping[str, Any], resolvable_iris: Mapping[str, str]
    ) -> str | None:
        """The core type whose template a placed node takes, or None when its own template stands.

        A node referencing a re-pointed template (by ``cc:template``, or, when
        that reference does not resolve, by its ``@type`` naming one) takes
        its core type's template: its placed ``entity_type``, else what the
        template's name maps to.
        """
        iri = node_kwargs.get("template_iri")
        name = self.old_template_names.get(iri) if isinstance(iri, str) else None
        if name is None and (not iri or iri not in resolvable_iris):
            term = node_kwargs.get("type_term")
            if term in self.templates:
                name = str(term)
        if name is None:
            return None
        entity_type = node_kwargs.get("entity_type")
        return str(entity_type) if entity_type else self.templates[name]

    def as_dict(self) -> dict[str, dict[str, Any]]:
        """The count table: old type -> ``{"core_type", "count"[, "unplaced"]}``, by old type."""
        return {old: dict(row) for old, row in sorted(self.mapped.items())}


__all__ = [
    "APP_GRAPH_TEMPLATE_NAMES",
    "DEFAULT_NODE_TEMPLATE_NAME",
    "CoreTypeMapping",
    "bundle_packs",
    "is_old_type_template",
]
