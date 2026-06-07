# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for module-level helpers in ai_entities.

Targets the pure resolver/builder helpers and the _StreamLoopDetector branch
logic. The big async streaming orchestrators (extract_single_chunk,
_extract_relationships, extract_from_chunks) are integration-shaped and are
intentionally NOT covered here.

Covered:
- _resolve_chunk_filtering_config (passthrough / resolve / evidence override)
- _resolve_loop_max_entity_count (domain / slider / fallback precedence)
- _resolve_minimum_alias_length (domain / slider / fallback precedence)
- _build_entity_prompt / _build_relationship_prompt (placeholder substitution)
- _StreamLoopDetector.check_line branches (entity cap / streaks / OOB)
- _safe_type_aliases (defensive accessor handling)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    _build_entity_prompt,
    _build_relationship_prompt,
    _resolve_chunk_filtering_config,
    _resolve_loop_max_entity_count,
    _resolve_minimum_alias_length,
    _safe_type_aliases,
    _StreamLoopDetector,
)
from chaoscypher_core.settings import ExtractionSettings


# Minimal templates exercising exactly the placeholders the build helpers fill.
_ENTITY_TEMPLATE = (
    "SENTENCES:\n{numbered_sentences}\n"
    "NODES:\n{node_templates}\n"
    "EXCLUSIONS:\n{entity_exclusions}\n"
    "STRICT:{strict_type_instruction}"
)
_REL_TEMPLATE = (
    "SENTENCES:\n{numbered_sentences}\n"
    "ENTITIES:\n{entity_list}\n"
    "MAX:{max_entity_index}\n"
    "EDGES:\n{edge_templates}"
)


# ---------------------------------------------------------------------------
# _resolve_chunk_filtering_config
# ---------------------------------------------------------------------------
class TestResolveChunkFilteringConfig:
    def test_preresolved_config_passthrough(self) -> None:
        sentinel = MagicMock(name="preresolved_config")
        out = _resolve_chunk_filtering_config(
            filtering_config=sentinel,
            filtering_mode="strict",
            domain_limits={},
            extraction_cfg=ExtractionSettings(),
            evidence_validation_mode=None,
        )
        assert out is sentinel

    def test_resolves_from_mode_when_none(self) -> None:
        out = _resolve_chunk_filtering_config(
            filtering_config=None,
            filtering_mode="strict",
            domain_limits={},
            extraction_cfg=ExtractionSettings(),
            evidence_validation_mode=None,
        )
        # Resolved a real FilteringConfig with the strict preset's cap.
        assert out.loop_max_entity_count == 35

    def test_falls_back_to_extraction_cfg_mode(self) -> None:
        cfg = ExtractionSettings()  # extraction_filtering_mode default = balanced
        out = _resolve_chunk_filtering_config(
            filtering_config=None,
            filtering_mode=None,
            domain_limits={},
            extraction_cfg=cfg,
            evidence_validation_mode=None,
        )
        # balanced preset cap.
        assert out.loop_max_entity_count == 50

    def test_evidence_override_applied(self) -> None:
        out = _resolve_chunk_filtering_config(
            filtering_config=None,
            filtering_mode="balanced",
            domain_limits={},
            extraction_cfg=ExtractionSettings(),
            evidence_validation_mode="off",
        )
        assert out.evidence_validation_mode == "off"

    def test_domain_overrides_applied(self) -> None:
        out = _resolve_chunk_filtering_config(
            filtering_config=None,
            filtering_mode="balanced",
            domain_limits={"loop_max_entity_count": 7},
            extraction_cfg=ExtractionSettings(),
            evidence_validation_mode=None,
        )
        assert out.loop_max_entity_count == 7


# ---------------------------------------------------------------------------
# _resolve_loop_max_entity_count
# ---------------------------------------------------------------------------
class TestResolveLoopMaxEntityCount:
    def test_domain_limit_takes_precedence(self) -> None:
        fc = SimpleNamespace(loop_max_entity_count=35)
        resolved = _resolve_loop_max_entity_count(
            filtering_config=fc,
            domain_limits={"loop_max_entity_count": 12},
            extraction_cfg=ExtractionSettings(),
        )
        assert resolved == 12

    def test_domain_limit_logs_when_slider_differs(self) -> None:
        # Slider differs from domain → debug log branch executes; domain wins.
        fc = SimpleNamespace(loop_max_entity_count=99)
        resolved = _resolve_loop_max_entity_count(
            filtering_config=fc,
            domain_limits={"loop_max_entity_count": 5},
            extraction_cfg=ExtractionSettings(),
        )
        assert resolved == 5

    def test_slider_value_used_when_no_domain_limit(self) -> None:
        fc = SimpleNamespace(loop_max_entity_count=33)
        resolved = _resolve_loop_max_entity_count(
            filtering_config=fc,
            domain_limits={},
            extraction_cfg=ExtractionSettings(),
        )
        assert resolved == 33

    def test_falls_back_to_extraction_cfg(self) -> None:
        # filtering_config None and no domain limit → extraction default.
        resolved = _resolve_loop_max_entity_count(
            filtering_config=None,
            domain_limits={},
            extraction_cfg=ExtractionSettings(),
        )
        assert resolved == 50

    def test_falls_back_when_slider_attr_missing(self) -> None:
        fc = SimpleNamespace()  # no loop_max_entity_count attribute
        resolved = _resolve_loop_max_entity_count(
            filtering_config=fc,
            domain_limits={},
            extraction_cfg=ExtractionSettings(),
        )
        assert resolved == 50


# ---------------------------------------------------------------------------
# _resolve_minimum_alias_length
# ---------------------------------------------------------------------------
class TestResolveMinimumAliasLength:
    def test_domain_limit_precedence(self) -> None:
        fc = SimpleNamespace(minimum_alias_length=2)
        resolved = _resolve_minimum_alias_length(
            filtering_config=fc,
            domain_limits={"minimum_alias_length": 4},
            extraction_cfg=ExtractionSettings(),
        )
        assert resolved == 4

    def test_domain_limit_logs_when_slider_differs(self) -> None:
        fc = SimpleNamespace(minimum_alias_length=9)
        resolved = _resolve_minimum_alias_length(
            filtering_config=fc,
            domain_limits={"minimum_alias_length": 3},
            extraction_cfg=ExtractionSettings(),
        )
        assert resolved == 3

    def test_slider_value_used(self) -> None:
        fc = SimpleNamespace(minimum_alias_length=5)
        resolved = _resolve_minimum_alias_length(
            filtering_config=fc,
            domain_limits={},
            extraction_cfg=ExtractionSettings(),
        )
        assert resolved == 5

    def test_falls_back_to_extraction_cfg(self) -> None:
        resolved = _resolve_minimum_alias_length(
            filtering_config=None,
            domain_limits={},
            extraction_cfg=ExtractionSettings(),
        )
        assert resolved == 2


# ---------------------------------------------------------------------------
# _build_entity_prompt
# ---------------------------------------------------------------------------
class TestBuildEntityPrompt:
    def test_basic_substitution_no_extras(self) -> None:
        prompt = _build_entity_prompt(
            template=_ENTITY_TEMPLATE,
            numbered_text="1. Hello",
            node_templates_formatted="NODE_T",
            entity_exclusions=None,
            strict_entity_types=False,
            entity_guidance=None,
            entity_examples=None,
        )
        assert "1. Hello" in prompt
        assert "NODE_T" in prompt
        # No strict instruction, no exclusion lines.
        assert "ONLY use the entity types" not in prompt
        assert "SKIP these" not in prompt

    def test_strict_and_exclusions_and_extras(self) -> None:
        rule = MagicMock()
        rule.as_prompt_text.return_value = "no dates"
        prompt = _build_entity_prompt(
            template=_ENTITY_TEMPLATE,
            numbered_text="1. Hi",
            node_templates_formatted="NT",
            entity_exclusions=[rule],
            strict_entity_types=True,
            entity_guidance="GUIDE",
            entity_examples="EXAMPLES",
        )
        assert "SKIP these" in prompt
        assert "no dates" in prompt
        assert "ONLY use the entity types" in prompt
        assert "Additional guidance:\nGUIDE" in prompt
        assert prompt.endswith("EXAMPLES")


# ---------------------------------------------------------------------------
# _build_relationship_prompt
# ---------------------------------------------------------------------------
class TestBuildRelationshipPrompt:
    def test_basic_substitution(self) -> None:
        prompt = _build_relationship_prompt(
            template=_REL_TEMPLATE,
            numbered_sentences="1. S",
            entity_list="0: A (Person)",
            max_entity_index=5,
            edge_templates="EDGE_T",
            relationship_guidance=None,
            relationship_examples=None,
        )
        assert "1. S" in prompt
        assert "0: A (Person)" in prompt
        assert "MAX:5" in prompt
        assert "EDGE_T" in prompt

    def test_guidance_and_examples_appended(self) -> None:
        prompt = _build_relationship_prompt(
            template=_REL_TEMPLATE,
            numbered_sentences="s",
            entity_list="e",
            max_entity_index="N",
            edge_templates="et",
            relationship_guidance="RGUIDE",
            relationship_examples="REX",
        )
        assert "Additional guidance:\nRGUIDE" in prompt
        assert prompt.endswith("REX")
        assert "MAX:N" in prompt


# ---------------------------------------------------------------------------
# _StreamLoopDetector.check_line branches
# ---------------------------------------------------------------------------
class TestStreamLoopDetector:
    def _detector(self, **overrides: object) -> _StreamLoopDetector:
        cfg = ExtractionSettings(**overrides)  # type: ignore[arg-type]
        return _StreamLoopDetector(extraction_cfg=cfg)

    def test_non_matching_line_returns_false(self) -> None:
        det = self._detector()
        assert det.check_line("plain text line", 10) is False

    def test_entity_count_cap_aborts(self) -> None:
        det = self._detector()
        det._max_entity_count = 3
        line = "E|Foo|Person|||S1|desc"
        aborted_at = None
        for i in range(1, 10):
            if det.check_line(line, len(line)):
                aborted_at = i
                break
        assert aborted_at == 3
        assert det.aborted is True

    def test_relationship_ignored_without_entities(self) -> None:
        # R| lines are not checked when entity_count == 0.
        det = self._detector()
        assert det.entity_count == 0
        assert det.check_line("R|0|1|RELATES_TO", 20) is False
        # relationship_count stays 0 because the branch was skipped.
        assert det.relationship_count == 0

    def test_out_of_bounds_streak_aborts(self) -> None:
        det = self._detector(loop_max_out_of_bounds=3)
        det.entity_count = 2  # indices >= 2 are out of bounds
        aborted = False
        for _ in range(5):
            aborted = det.check_line("R|9|9|REL", 20)
            if aborted:
                break
        assert aborted is True
        assert det.aborted is True

    def test_relationship_count_cap_aborts(self) -> None:
        # entity cap small → relationship cap = entity_cap * multiplier.
        det = self._detector()
        det.entity_count = 5
        det._max_relationship_count = 3
        aborted_at = None
        # Use valid in-bounds varying pairs so only the count cap can fire.
        for i in range(1, 10):
            src, tgt = (i % 5), ((i + 1) % 5)
            if det.check_line(f"R|{src}|{tgt}|T{i}", 20):
                aborted_at = i
                break
        assert aborted_at == 3
        assert det.aborted is True

    def test_repeating_property_key_aborts(self) -> None:
        det = self._detector(loop_max_property_repeat=3)
        line = "P|0|color|red"
        aborted_at = None
        for i in range(1, 10):
            if det.check_line(line, len(line)):
                aborted_at = i
                break
        assert aborted_at == 3
        assert det.aborted is True

    def test_same_pair_cap_aborts(self) -> None:
        det = self._detector(loop_max_same_pair=3)
        det.entity_count = 10  # in-bounds so OOB doesn't interfere
        line = "R|0|1|REL"
        aborted_at = None
        for i in range(1, 10):
            if det.check_line(line, len(line)):
                aborted_at = i
                break
        assert aborted_at == 3
        assert det.aborted is True

    def test_malformed_relationship_line_ignored(self) -> None:
        # Fewer than 4 parts → returns False without aborting.
        det = self._detector()
        det.entity_count = 3
        assert det.check_line("R|0|1", 10) is False
        assert det.aborted is False

    def test_non_integer_relationship_indices_ignored(self) -> None:
        det = self._detector()
        det.entity_count = 3
        assert det.check_line("R|x|y|REL", 10) is False
        assert det.aborted is False


# ---------------------------------------------------------------------------
# _safe_type_aliases
# ---------------------------------------------------------------------------
class TestSafeTypeAliases:
    def test_no_accessor_returns_empty(self) -> None:
        domain = SimpleNamespace()  # no get_type_aliases
        assert _safe_type_aliases(domain) == {}

    def test_non_callable_accessor_returns_empty(self) -> None:
        domain = SimpleNamespace(get_type_aliases="not callable")
        assert _safe_type_aliases(domain) == {}

    def test_accessor_raising_returns_empty(self) -> None:
        def _raise() -> dict[str, str]:
            raise RuntimeError("boom")

        domain = SimpleNamespace(get_type_aliases=_raise)
        assert _safe_type_aliases(domain) == {}

    def test_non_dict_return_returns_empty(self) -> None:
        domain = SimpleNamespace(get_type_aliases=lambda: ["not", "a", "dict"])
        assert _safe_type_aliases(domain) == {}

    def test_valid_dict_coerced_to_str(self) -> None:
        domain = SimpleNamespace(
            get_type_aliases=lambda: {"Person": "Human", "Org": "Organization"}
        )
        assert _safe_type_aliases(domain) == {
            "Person": "Human",
            "Org": "Organization",
        }

    def test_filters_non_str_and_empty_pairs(self) -> None:
        domain = SimpleNamespace(
            get_type_aliases=lambda: {
                "Good": "Value",
                "": "skip-empty-key",
                "skip-empty-val": "",
                123: "skip-non-str-key",
            }
        )
        assert _safe_type_aliases(domain) == {"Good": "Value"}
