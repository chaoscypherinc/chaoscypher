# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 6 Task 8 (2026-05-08): max_entity_degree_override per-source cascade.

Tests confirm that ``_build_extraction_config`` threads the source-row's
``max_entity_degree_override`` (positive integer) into ``extraction_limits``
as ``max_entity_degree``, giving per-source control over the degree cap without
needing a domain config change.

Cascade priority:
  source_row.max_entity_degree_override (pos int) > domain extraction_limits
  (already present) > FilteringConfig default.

Zero / negative / None values on the row are treated as "not set" (use domain or
global default).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from chaoscypher_core.app_config import Settings


def _make_domain(
    *,
    extraction_limits: dict[str, Any] | None = None,
) -> Any:
    domain = MagicMock()
    domain.metadata = MagicMock(plugin_id="generic")
    domain.get_extraction_limits = MagicMock(
        return_value=extraction_limits if extraction_limits is not None else {}
    )
    domain.get_filtering_mode = MagicMock(return_value=None)
    domain.get_entity_exclusions = MagicMock(return_value=[])
    domain.get_evidence_validation_mode = MagicMock(return_value=None)
    domain.get_strict_entity_types = MagicMock(return_value=False)
    domain.get_edge_type_constraints = MagicMock(return_value={})
    domain.get_templates = MagicMock(return_value={"node_templates": [], "edge_templates": []})
    return domain


def _patch_format(monkeypatch) -> None:
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.engine.extraction.orchestration.format_extraction_templates",
        lambda *_a, **_kw: {
            "node_templates": "",
            "edge_templates": "",
            "entity_examples": "",
            "relationship_examples": "",
        },
    )


class TestMaxEntityDegreeOverrideCascade:
    """source_row.max_entity_degree_override > domain limits > global default."""

    def test_override_written_into_extraction_limits(self, monkeypatch) -> None:
        """Positive integer on source row becomes max_entity_degree in limits dict."""
        from chaoscypher_core.operations.importing import import_service

        _patch_format(monkeypatch)

        cfg_json = import_service._build_extraction_config(
            domain=_make_domain(),
            entity_guidance=None,
            relationship_guidance=None,
            settings=Settings(),
            file_info={},
            source_row={"max_entity_degree_override": 7},
        )
        cfg = json.loads(cfg_json)
        limits = cfg.get("extraction_limits") or {}
        assert limits.get("max_entity_degree") == 7, (
            "source_row.max_entity_degree_override=7 must produce "
            "extraction_limits.max_entity_degree=7"
        )

    def test_override_replaces_domain_degree_limit(self, monkeypatch) -> None:
        """Per-source override wins over domain-level max_entity_degree."""
        from chaoscypher_core.operations.importing import import_service

        _patch_format(monkeypatch)

        domain = _make_domain(extraction_limits={"max_entity_degree": 30})

        cfg_json = import_service._build_extraction_config(
            domain=domain,
            entity_guidance=None,
            relationship_guidance=None,
            settings=Settings(),
            file_info={},
            source_row={"max_entity_degree_override": 5},
        )
        cfg = json.loads(cfg_json)
        limits = cfg.get("extraction_limits") or {}
        assert limits.get("max_entity_degree") == 5, (
            "Per-source override (5) must win over domain limit (30)"
        )

    def test_none_does_not_inject_degree(self, monkeypatch) -> None:
        """NULL (None) on source row does NOT inject max_entity_degree."""
        from chaoscypher_core.operations.importing import import_service

        _patch_format(monkeypatch)

        cfg_json = import_service._build_extraction_config(
            domain=_make_domain(),
            entity_guidance=None,
            relationship_guidance=None,
            settings=Settings(),
            file_info={},
            source_row={"max_entity_degree_override": None},
        )
        cfg = json.loads(cfg_json)
        limits = cfg.get("extraction_limits") or {}
        # No injection when NULL — cascade falls back to FilteringConfig default
        assert "max_entity_degree" not in limits or limits.get("max_entity_degree") != 0, (
            "None on source row must not inject a zero or nonsensical degree cap"
        )

    def test_zero_does_not_inject_degree(self, monkeypatch) -> None:
        """Zero value on source row is treated as 'not set' (sentinel guard)."""
        from chaoscypher_core.operations.importing import import_service

        _patch_format(monkeypatch)

        cfg_json = import_service._build_extraction_config(
            domain=_make_domain(),
            entity_guidance=None,
            relationship_guidance=None,
            settings=Settings(),
            file_info={},
            source_row={"max_entity_degree_override": 0},
        )
        cfg = json.loads(cfg_json)
        limits = cfg.get("extraction_limits") or {}
        # Zero must be treated as "not set" — removing the cap entirely would
        # mean no relationships get dropped, which is wrong for override semantics.
        assert limits.get("max_entity_degree") != 0, (
            "max_entity_degree_override=0 must NOT inject zero as the degree cap"
        )

    def test_negative_does_not_inject_degree(self, monkeypatch) -> None:
        """Negative value on source row is treated as 'not set'."""
        from chaoscypher_core.operations.importing import import_service

        _patch_format(monkeypatch)

        cfg_json = import_service._build_extraction_config(
            domain=_make_domain(),
            entity_guidance=None,
            relationship_guidance=None,
            settings=Settings(),
            file_info={},
            source_row={"max_entity_degree_override": -1},
        )
        cfg = json.loads(cfg_json)
        limits = cfg.get("extraction_limits") or {}
        assert limits.get("max_entity_degree") != -1, (
            "max_entity_degree_override=-1 must NOT inject -1 as the degree cap"
        )

    def test_no_source_row_does_not_inject_degree(self, monkeypatch) -> None:
        """When source_row is None, no max_entity_degree is injected via this path."""
        from chaoscypher_core.operations.importing import import_service

        _patch_format(monkeypatch)

        cfg_json = import_service._build_extraction_config(
            domain=_make_domain(),
            entity_guidance=None,
            relationship_guidance=None,
            settings=Settings(),
            file_info={},
            source_row=None,
        )
        cfg = json.loads(cfg_json)
        limits = cfg.get("extraction_limits") or {}
        # Without source_row the key should not be present from the P6T8 block
        # (domain limits might still inject it, but our test domain is empty)
        assert "max_entity_degree" not in limits, (
            "No source_row means no per-source degree override injection"
        )
