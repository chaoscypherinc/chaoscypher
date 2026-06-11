# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Referential-integrity lint for every builtin domain plugin (.jsonld).

``DomainConfigModel`` validates shape, not semantics. Each invariant
pinned here fails *silently at extraction time* when broken:

- an edge template ``source_types``/``target_types`` entry that names a
  non-existent node template is an unsatisfiable constraint — every
  relationship of that type is dropped (found live in design 1.9.0 and
  educational 1.11.0, fixed 2026-06-09);
- a ``type_aliases`` target that isn't a node template re-types entities
  to an invalid type, which strict-type domains then drop;
- an ``absorbs_types`` entry that names a real template is dead config —
  type rescue only processes invalid types (found live in reference 1.9.0);
- a detection regex that doesn't compile is skipped with only a runtime
  WARNING, silently weakening detection;
- an unknown ``extraction_filtering_mode`` raises at extraction time, and
  unknown ``extraction_limits`` keys are logged-and-dropped.

The companion script ``internal/scripts/audit_domain_configs.py`` runs
these checks plus interface icon-registry parity (which a core test
cannot reach across package boundaries).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.extraction.content_categories import (
    CONTENT_CATEGORIES,
    validate_custom_patterns,
)
from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
    VALID_PRESETS,
    FilteringConfig,
)


_PLUGINS_DIR = Path(__file__).resolve().parents[7] / (
    "src/chaoscypher_core/services/sources/engine/extraction/domains/plugins"
)

_PLUGIN_PATHS = sorted(_PLUGINS_DIR.glob("*.jsonld"))

_VALID_EVIDENCE_MODES = {"off", "relaxed", "narrative", "standard", "strict"}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _node_names(cfg: dict[str, Any]) -> set[str]:
    templates = cfg.get("templates", {})
    return {t["name"] for t in templates.get("node_templates", []) if t.get("name")}


def test_builtin_plugin_set_discovered() -> None:
    """Path-layout guard: an empty glob would vacuously pass everything."""
    assert len(_PLUGIN_PATHS) >= 19, f"only found {len(_PLUGIN_PATHS)} plugins in {_PLUGINS_DIR}"


@pytest.mark.parametrize("path", _PLUGIN_PATHS, ids=lambda p: p.stem)
def test_edge_type_constraints_reference_real_node_templates(path: Path) -> None:
    cfg = _load(path)
    nodes = _node_names(cfg)
    bad: list[str] = []
    for tmpl in cfg.get("templates", {}).get("edge_templates", []):
        for field in ("source_types", "target_types"):
            unknown = [t for t in tmpl.get(field, []) if t not in nodes]
            if unknown:
                bad.append(f"{tmpl.get('name')}.{field}: {unknown}")
    assert not bad, (
        f"{path.stem}: edge type constraints referencing non-existent node "
        f"templates (relationships of these types are silently dropped): {bad}"
    )


@pytest.mark.parametrize("path", _PLUGIN_PATHS, ids=lambda p: p.stem)
def test_type_aliases_map_to_real_node_templates(path: Path) -> None:
    cfg = _load(path)
    nodes = _node_names(cfg)
    aliases = cfg.get("type_aliases") or {}
    bad_targets = {a: c for a, c in aliases.items() if c not in nodes}
    shadowing = [a for a in aliases if a in nodes]
    assert not bad_targets, f"{path.stem}: alias targets are not node templates: {bad_targets}"
    assert not shadowing, f"{path.stem}: alias keys shadow real node templates: {shadowing}"


@pytest.mark.parametrize("path", _PLUGIN_PATHS, ids=lambda p: p.stem)
def test_absorbs_types_do_not_collide_with_real_templates(path: Path) -> None:
    cfg = _load(path)
    nodes = _node_names(cfg)
    bad: list[str] = []
    for tmpl in cfg.get("templates", {}).get("node_templates", []):
        for prop in tmpl.get("properties", []):
            collisions = [a for a in prop.get("absorbs_types", []) if a in nodes]
            if collisions:
                bad.append(f"{tmpl.get('name')}.{prop.get('name')}: {collisions}")
    assert not bad, (
        f"{path.stem}: absorbs_types naming real node templates are dead "
        f"config (rescue only sees invalid types): {bad}"
    )


@pytest.mark.parametrize("path", _PLUGIN_PATHS, ids=lambda p: p.stem)
def test_no_duplicate_template_names_or_ids(path: Path) -> None:
    cfg = _load(path)
    templates = cfg.get("templates", {})
    for kind in ("node_templates", "edge_templates"):
        for field in ("name", "id"):
            values = [t.get(field) for t in templates.get(kind, []) if t.get(field)]
            dupes = {v for v in values if values.count(v) > 1}
            assert not dupes, f"{path.stem}: duplicate {kind} {field}(s): {sorted(dupes)}"


@pytest.mark.parametrize("path", _PLUGIN_PATHS, ids=lambda p: p.stem)
def test_detection_regexes_compile(path: Path) -> None:
    cfg = _load(path)
    for spec in cfg.get("detection", {}).get("patterns", []):
        regex = spec.get("regex")
        assert regex, f"{path.stem}: detection pattern with empty regex"
        try:
            re.compile(regex)
        except re.error as exc:  # pragma: no cover - failure path
            pytest.fail(f"{path.stem}: detection regex does not compile: {regex!r} ({exc})")


@pytest.mark.parametrize("path", _PLUGIN_PATHS, ids=lambda p: p.stem)
def test_filtering_settings_are_valid(path: Path) -> None:
    cfg = _load(path)
    mode = cfg.get("extraction_filtering_mode")
    if mode:
        assert mode in VALID_PRESETS, f"{path.stem}: unknown filtering mode {mode!r}"
    valid_fields = set(FilteringConfig.__dataclass_fields__)
    bad_keys = [k for k in cfg.get("extraction_limits", {}) if k not in valid_fields]
    assert not bad_keys, f"{path.stem}: extraction_limits keys not on FilteringConfig: {bad_keys}"
    ev_mode = cfg.get("evidence_validation_mode")
    if ev_mode:
        assert ev_mode in _VALID_EVIDENCE_MODES, (
            f"{path.stem}: unknown evidence_validation_mode {ev_mode!r}"
        )


@pytest.mark.parametrize("path", _PLUGIN_PATHS, ids=lambda p: p.stem)
def test_content_exclusions_are_registered(path: Path) -> None:
    cfg = _load(path)
    exclusions = cfg.get("content_exclusions") or {}
    unknown = [c for c in exclusions.get("categories", []) if c not in CONTENT_CATEGORIES]
    assert not unknown, f"{path.stem}: unknown content_exclusions categories: {unknown}"
    errors = validate_custom_patterns(exclusions.get("custom_patterns", []))
    assert not errors, f"{path.stem}: invalid content_exclusions custom_patterns: {errors}"


@pytest.mark.parametrize("path", _PLUGIN_PATHS, ids=lambda p: p.stem)
def test_inverse_and_symmetric_are_mutually_exclusive(path: Path) -> None:
    cfg = _load(path)
    bad = [
        t.get("name")
        for t in cfg.get("templates", {}).get("edge_templates", [])
        if t.get("inverse") and t.get("symmetric") is True
    ]
    assert not bad, f"{path.stem}: edges declaring both inverse and symmetric: {bad}"
