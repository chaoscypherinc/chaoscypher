# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pure synchronous logic tests for StructuredExtractor.

These tests instantiate ``StructuredExtractor(MagicMock())`` (a MagicMock
provider_factory) and exercise the framework-agnostic helper methods
directly. No async / no provider calls are involved here.

Covers:
- ``_repair_json`` (markdown fences, prose extraction, unquoted values,
  missing commas, trailing commas, truncation, incomplete strings).
- ``_extract_json_object`` (object/array selection, strings ignored,
  unbalanced fallthrough).
- ``_extract_complete_items_from_truncated`` (item recovery, fences,
  bare arrays, malformed-item skip).
- ``_alias_field`` (all four tiers + skip/no-op/False).
- ``_add_default_fields`` (flat items, nodes->entities, defaults,
  entity_types dict, string templates).
- ``_check_entity_quality`` / ``_auto_fix_entity_descriptions``.
- ``_build_validation_guidance`` / ``_validate_schema``.
- ``_strip_previous_retry_messages`` / ``_build_*_prompt`` /
  ``_build_result_output``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import jsonschema
import pytest

from chaoscypher_core.adapters.llm.schema.extractor import StructuredExtractor


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_extractor() -> StructuredExtractor:
    """StructuredExtractor backed by a MagicMock provider_factory.

    The pure helpers under test never touch the factory, so a bare
    MagicMock is sufficient. ExtractionSettings defaults are used.
    """
    return StructuredExtractor(MagicMock())


# ---------------------------------------------------------------------------
# _repair_json
# ---------------------------------------------------------------------------


def test_repair_json_strips_json_fence() -> None:
    text = '```json\n{"a": 1}\n```'
    repaired, truncated = _make_extractor()._repair_json(text)
    assert json.loads(repaired) == {"a": 1}
    assert truncated is False


def test_repair_json_strips_bare_fence() -> None:
    text = '```\n{"a": 1}\n```'
    repaired, truncated = _make_extractor()._repair_json(text)
    assert json.loads(repaired) == {"a": 1}
    assert truncated is False


def test_repair_json_extracts_embedded_in_prose() -> None:
    text = 'Here is the answer: {"a": 1} -- hope that helps!'
    repaired, _ = _make_extractor()._repair_json(text)
    assert json.loads(repaired) == {"a": 1}


def test_repair_json_quotes_unquoted_value() -> None:
    text = '{"name": Alice}'
    repaired, _ = _make_extractor()._repair_json(text)
    assert json.loads(repaired) == {"name": "Alice"}


def test_repair_json_skips_true_false_null_and_numbers() -> None:
    text = '{"a": true, "b": false, "c": null, "d": 12.5}'
    repaired, _ = _make_extractor()._repair_json(text)
    assert json.loads(repaired) == {"a": True, "b": False, "c": None, "d": 12.5}


def test_repair_json_does_not_quote_nested_object() -> None:
    text = '{"a": {"b": 1}}'
    repaired, _ = _make_extractor()._repair_json(text)
    assert json.loads(repaired) == {"a": {"b": 1}}


def test_repair_json_inserts_missing_comma_between_objects() -> None:
    text = '[{"a": 1}\n{"b": 2}]'
    repaired, _ = _make_extractor()._repair_json(text)
    assert json.loads(repaired) == [{"a": 1}, {"b": 2}]


def test_repair_json_removes_trailing_comma() -> None:
    text = '{"a": 1,}'
    repaired, _ = _make_extractor()._repair_json(text)
    assert json.loads(repaired) == {"a": 1}


def test_repair_json_detects_truncation_and_appends_closing_brace() -> None:
    # Truncated mid nested object: braces are unbalanced, the last line is
    # a complete property, so the missing closing brace is appended and the
    # result round-trips.
    text = '{\n"a": 1,\n"b": {\n"c": 2}'
    repaired, truncated = _make_extractor()._repair_json(text)
    assert truncated is True
    assert json.loads(repaired) == {"a": 1, "b": {"c": 2}}


def test_repair_json_detects_truncation_and_appends_closing_bracket() -> None:
    text = '[{"a": 1}, {"b": 2}'
    repaired, truncated = _make_extractor()._repair_json(text)
    assert truncated is True
    assert json.loads(repaired) == [{"a": 1}, {"b": 2}]


def test_repair_json_closes_odd_quote_incomplete_string() -> None:
    # Truncated mid-string: the open brace count exceeds close, and the
    # final line has an odd number of quotes. The closing-quote branch fires
    # (info log "closing_incomplete_string"), then the incomplete-property
    # branch drops the dangling line, leaving an empty object after the
    # missing brace is appended. We assert it round-trips to a dict.
    text = '{\n"description": "An incomplete sentence'
    repaired, truncated = _make_extractor()._repair_json(text)
    assert truncated is True
    parsed = json.loads(repaired)
    assert parsed == {}


def test_repair_json_drops_dangling_incomplete_property() -> None:
    # Final line is a key with no value terminator -> the dangling "b": line
    # is dropped (the trailing-comma removal already ran earlier in the pass,
    # so the re-exposed comma is not stripped again -- this asserts the actual
    # behavior of the incomplete-property branch, not idealized output).
    text = '{\n"a": 1,\n"b":'
    repaired, truncated = _make_extractor()._repair_json(text)
    assert truncated is True
    assert '"b"' not in repaired
    assert '"a": 1' in repaired


def test_repair_json_no_braces_returns_input_unchanged_balanced() -> None:
    text = "just some prose"
    repaired, truncated = _make_extractor()._repair_json(text)
    assert repaired == "just some prose"
    assert truncated is False


# ---------------------------------------------------------------------------
# _extract_json_object
# ---------------------------------------------------------------------------


def test_extract_json_object_no_brace_returns_input() -> None:
    assert _make_extractor()._extract_json_object("no json here") == "no json here"


def test_extract_json_object_object_only() -> None:
    text = 'prefix {"a": 1} suffix'
    assert _make_extractor()._extract_json_object(text) == '{"a": 1}'


def test_extract_json_object_array_only() -> None:
    text = "prefix [1, 2, 3] suffix"
    assert _make_extractor()._extract_json_object(text) == "[1, 2, 3]"


def test_extract_json_object_picks_earlier_of_brace_and_bracket() -> None:
    # '[' appears before '{' so the array is chosen as the start.
    text = '[1, {"a": 2}]'
    assert _make_extractor()._extract_json_object(text) == '[1, {"a": 2}]'


def test_extract_json_object_picks_object_when_brace_earlier() -> None:
    text = '{"a": [1, 2]} trailing'
    assert _make_extractor()._extract_json_object(text) == '{"a": [1, 2]}'


def test_extract_json_object_ignores_braces_inside_strings() -> None:
    text = '{"a": "has } brace"}'
    assert _make_extractor()._extract_json_object(text) == '{"a": "has } brace"}'


def test_extract_json_object_unbalanced_returns_text_from_start() -> None:
    text = 'noise {"a": 1'
    # Never balances, so returns text[start:].
    assert _make_extractor()._extract_json_object(text) == '{"a": 1'


# ---------------------------------------------------------------------------
# _extract_complete_items_from_truncated
# ---------------------------------------------------------------------------


def test_extract_items_recovers_complete_before_cut() -> None:
    text = '{"items": [{"a": 1}, {"b": 2}, {"c":'
    items, count, truncated = _make_extractor()._extract_complete_items_from_truncated(text)
    assert items == [{"a": 1}, {"b": 2}]
    assert count == 2
    assert truncated is True


def test_extract_items_strips_markdown_fences() -> None:
    text = '```json\n{"items": [{"a": 1}]}\n```'
    items, count, truncated = _make_extractor()._extract_complete_items_from_truncated(text)
    assert items == [{"a": 1}]
    assert count == 1
    assert truncated is False


def test_extract_items_bare_array_without_items_key() -> None:
    text = '[{"a": 1}, {"b": 2}]'
    items, count, _ = _make_extractor()._extract_complete_items_from_truncated(text)
    assert items == [{"a": 1}, {"b": 2}]
    assert count == 2


def test_extract_items_no_array_returns_empty_tuple() -> None:
    text = '{"foo": "bar"}'.replace("[", "")
    # No '[' present at all.
    assert "[" not in text
    result = _make_extractor()._extract_complete_items_from_truncated(text)
    assert result == ([], 0, False)


def test_extract_items_skips_malformed_object_and_continues() -> None:
    # Middle object has a duplicate key collision that is still valid JSON,
    # so build one with genuinely unparseable content via an embedded raw
    # control structure. Use a non-object token between valid items to hit
    # the "not an object" skip path instead.
    text = '{"items": [{"a": 1}, 42, {"b": 2}]}'
    items, count, _ = _make_extractor()._extract_complete_items_from_truncated(text)
    # The bare 42 is skipped char-by-char; the two objects are recovered.
    assert {"a": 1} in items
    assert {"b": 2} in items
    assert count == 2


# ---------------------------------------------------------------------------
# _alias_field
# ---------------------------------------------------------------------------


def test_alias_field_noop_when_target_present() -> None:
    data: dict[str, Any] = {"entities": [{"x": 1}]}
    assert _make_extractor()._alias_field(data, "entities", ["nodes"], ["entit"]) is True
    assert data == {"entities": [{"x": 1}]}


def test_alias_field_tier1_exact_alias() -> None:
    data: dict[str, Any] = {"nodes": [{"x": 1}]}
    assert _make_extractor()._alias_field(data, "entities", ["nodes"], ["entit"]) is True
    assert data == {"entities": [{"x": 1}]}


def test_alias_field_tier2_partial_name_match() -> None:
    data: dict[str, Any] = {"my_entities_list": [{"x": 1}]}
    assert _make_extractor()._alias_field(data, "entities", [], ["entit"]) is True
    assert data == {"entities": [{"x": 1}]}


def test_alias_field_tier3_shape_match() -> None:
    data: dict[str, Any] = {"things": [{"name": "A", "type": "Person"}]}
    found = _make_extractor()._alias_field(
        data,
        "entities",
        [],
        [],
        shape_fields=[["name"], ["type"]],
    )
    assert found is True
    assert data == {"entities": [{"name": "A", "type": "Person"}]}


def test_alias_field_tier3_string_list_when_allowed() -> None:
    data: dict[str, Any] = {"things": ["Alpha", "Beta"]}
    found = _make_extractor()._alias_field(
        data,
        "suggested_templates",
        [],
        [],
        shape_fields=[["name"]],
        allow_string_list=True,
    )
    assert found is True
    assert data == {"suggested_templates": ["Alpha", "Beta"]}


def test_alias_field_tier4_fallback_to_only_array() -> None:
    data: dict[str, Any] = {"some_blob": [{"foo": 1}]}
    found = _make_extractor()._alias_field(
        data,
        "suggested_templates",
        [],
        [],
        shape_fields=None,
        fallback_to_only_array=True,
    )
    assert found is True
    assert data == {"suggested_templates": [{"foo": 1}]}


def test_alias_field_skip_fields_excludes_candidate() -> None:
    data: dict[str, Any] = {"entities": [{"name": "A", "type": "P"}]}
    # entities skipped, no other array -> not found.
    found = _make_extractor()._alias_field(
        data,
        "relationships",
        ["relations"],
        ["relat"],
        shape_fields=[["source"], ["target"]],
        skip_fields=["entities"],
    )
    assert found is False
    assert "relationships" not in data


def test_alias_field_false_when_nothing_matches() -> None:
    data: dict[str, Any] = {"unrelated": "not even a list"}
    found = _make_extractor()._alias_field(data, "entities", ["nodes"], ["entit"])
    assert found is False
    assert data == {"unrelated": "not even a list"}


# ---------------------------------------------------------------------------
# _add_default_fields
# ---------------------------------------------------------------------------


def test_add_default_fields_non_dict_noop() -> None:
    data: Any = ["not", "a", "dict"]
    _make_extractor()._add_default_fields(data)
    assert data == ["not", "a", "dict"]


def test_add_default_fields_flat_items_short_circuits() -> None:
    data: dict[str, Any] = {"items": [{"a": 1}]}
    _make_extractor()._add_default_fields(data)
    # No entities/relationships/templates added.
    assert data == {"items": [{"a": 1}]}


def test_add_default_fields_nodes_to_entities_and_default_relationships() -> None:
    data: dict[str, Any] = {"nodes": [{"name": "A", "type": "P"}]}
    _make_extractor()._add_default_fields(data)
    assert data["entities"] == [{"name": "A", "type": "P"}]
    assert data["relationships"] == []


def test_add_default_fields_entity_types_dict_to_templates() -> None:
    data: dict[str, Any] = {"entity_types": {"concept": 56, "place": 0, "person": 3}}
    _make_extractor()._add_default_fields(data)
    names = {t["name"] for t in data["suggested_templates"]}
    # Zero-count entries are filtered out; names are capitalized.
    assert names == {"Concept", "Person"}
    assert data["primary_domain"] == "General"
    assert data["document_type"] == "reference"


def test_add_default_fields_string_template_array_to_objects() -> None:
    data: dict[str, Any] = {"suggested_templates": ["Alpha", "Beta"]}
    _make_extractor()._add_default_fields(data)
    assert data["suggested_templates"] == [{"name": "Alpha"}, {"name": "Beta"}]
    assert data["primary_domain"] == "General"
    assert data["document_type"] == "reference"


def test_add_default_fields_defaults_preserved_when_present() -> None:
    data: dict[str, Any] = {
        "suggested_templates": [{"name": "X"}],
        "primary_domain": "Science",
        "document_type": "paper",
    }
    _make_extractor()._add_default_fields(data)
    assert data["primary_domain"] == "Science"
    assert data["document_type"] == "paper"


# ---------------------------------------------------------------------------
# _check_entity_quality
# ---------------------------------------------------------------------------


def test_check_entity_quality_clean_returns_empty() -> None:
    entities = [{"name": "Alice", "type": "Person", "description": "A well-described person here."}]
    assert _make_extractor()._check_entity_quality(entities) == []


def test_check_entity_quality_empty_description() -> None:
    entities = [{"name": "Bob", "type": "Person", "description": ""}]
    issues = _make_extractor()._check_entity_quality(entities)
    assert any("empty description" in i for i in issues)


def test_check_entity_quality_short_description() -> None:
    entities = [{"name": "Bob", "type": "Person", "description": "short."}]
    issues = _make_extractor()._check_entity_quality(entities)
    assert any("very short description" in i for i in issues)


def test_check_entity_quality_missing_punctuation() -> None:
    # >20 chars and no end punctuation triggers incomplete-description issue.
    long_no_punct = "This is a long enough description without punctuation"
    entities = [{"name": "Bob", "type": "Person", "description": long_no_punct}]
    issues = _make_extractor()._check_entity_quality(entities)
    assert any("incomplete description" in i for i in issues)


def test_check_entity_quality_missing_name_and_type() -> None:
    entities = [{"description": "A long enough complete description here."}]
    issues = _make_extractor()._check_entity_quality(entities)
    assert any("missing 'name'" in i for i in issues)
    assert any("missing 'type'" in i for i in issues)


def test_check_entity_quality_skips_non_dict() -> None:
    entities: list[Any] = ["not a dict", 42]
    assert _make_extractor()._check_entity_quality(entities) == []


# ---------------------------------------------------------------------------
# _auto_fix_entity_descriptions
# ---------------------------------------------------------------------------


def test_auto_fix_adds_period_over_threshold() -> None:
    desc = "This is a sufficiently long description with no trailing punctuation"
    entities = [{"name": "A", "type": "P", "description": desc}]
    fixes = _make_extractor()._auto_fix_entity_descriptions(entities)
    assert fixes == 1
    assert entities[0]["description"].endswith(".")


def test_auto_fix_leaves_already_punctuated_alone() -> None:
    desc = "This description already ends with a period."
    entities = [{"name": "A", "type": "P", "description": desc}]
    fixes = _make_extractor()._auto_fix_entity_descriptions(entities)
    assert fixes == 0
    assert entities[0]["description"] == desc


def test_auto_fix_leaves_short_description_alone() -> None:
    # <= 20 chars is below the incomplete threshold -> untouched.
    desc = "tiny"
    entities = [{"name": "A", "type": "P", "description": desc}]
    fixes = _make_extractor()._auto_fix_entity_descriptions(entities)
    assert fixes == 0
    assert entities[0]["description"] == "tiny"


def test_auto_fix_skips_non_dict_and_non_string_desc() -> None:
    entities: list[Any] = ["nope", {"name": "A", "description": 123}, {"name": "B"}]
    fixes = _make_extractor()._auto_fix_entity_descriptions(entities)
    assert fixes == 0


def test_auto_fix_counts_multiple_fixes() -> None:
    desc = "A sufficiently long description without ending punctuation here"
    entities = [
        {"name": "A", "type": "P", "description": desc},
        {"name": "B", "type": "P", "description": desc},
    ]
    fixes = _make_extractor()._auto_fix_entity_descriptions(entities)
    assert fixes == 2


# ---------------------------------------------------------------------------
# _build_validation_guidance
# ---------------------------------------------------------------------------


def _ve(message: str) -> jsonschema.ValidationError:
    return jsonschema.ValidationError(message)


def test_validation_guidance_required_property() -> None:
    g = _make_extractor()._build_validation_guidance(
        _ve("'name' is a required property"), "root", "'name' is a required property"
    )
    assert any("required 'name' field" in s for s in g)


def test_validation_guidance_wrong_type() -> None:
    g = _make_extractor()._build_validation_guidance(
        _ve("'x' is not of type 'string'"), "entities.0", "'x' is not of type 'string'"
    )
    assert any("must be type 'string'" in s for s in g)


def test_validation_guidance_not_one_of() -> None:
    g = _make_extractor()._build_validation_guidance(
        _ve("'z' is not one of ['a', 'b']"), "entities.0.type", "msg"
    )
    assert any("allowed values" in s for s in g)


def test_validation_guidance_additional_properties() -> None:
    g = _make_extractor()._build_validation_guidance(
        _ve("Additional properties are not allowed ('extra' was unexpected)"),
        "root",
        "msg",
    )
    assert any("Remove extra fields" in s for s in g)


def test_validation_guidance_fallback_and_relationship_reminder() -> None:
    g = _make_extractor()._build_validation_guidance(
        _ve("some unexpected error"),
        "relationships",
        "error about relationships",
    )
    assert any("Review the schema carefully" in s for s in g)
    assert any("source' and 'target'" in s for s in g)


# ---------------------------------------------------------------------------
# _validate_schema
# ---------------------------------------------------------------------------


_SIMPLE_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
}


def test_validate_schema_valid_returns_none() -> None:
    errors: list[str] = []
    result = _make_extractor()._validate_schema(
        {"name": "Alice"}, _SIMPLE_SCHEMA, errors, attempt=0, max_retries=3
    )
    assert result is None
    assert errors == []


def test_validate_schema_invalid_with_retries_returns_guidance() -> None:
    errors: list[str] = []
    result = _make_extractor()._validate_schema(
        {}, _SIMPLE_SCHEMA, errors, attempt=0, max_retries=3
    )
    assert result is not None
    assert result != "return_with_errors"
    assert "VALIDATION ERROR" in result
    assert errors  # appended


def test_validate_schema_invalid_final_returns_with_errors() -> None:
    errors: list[str] = []
    result = _make_extractor()._validate_schema(
        {}, _SIMPLE_SCHEMA, errors, attempt=3, max_retries=3
    )
    assert result == "return_with_errors"
    assert errors


# ---------------------------------------------------------------------------
# _strip_previous_retry_messages
# ---------------------------------------------------------------------------


def test_strip_retry_messages_removes_all_and_is_idempotent() -> None:
    base = "Extract structured information.\n\n<document>\nhi\n</document>"
    polluted = (
        base
        + "\n\n[Previous attempt failed: boom. Please fix and try again.]"
        + "\n\n[IMPORTANT: Previous response was incomplete/truncated. Focus...]"
        + "\n\n[Previous response had quality issues: bad. Please ensure...]"
        + "\n\n[VALIDATION ERROR - Attempt 1/4]\nSchema validation failed\n\n"
    )
    ext = _make_extractor()
    cleaned = ext._strip_previous_retry_messages(polluted)
    assert "Previous attempt failed" not in cleaned
    assert "IMPORTANT: Previous response" not in cleaned
    assert "quality issues" not in cleaned
    assert "VALIDATION ERROR" not in cleaned
    # Idempotent: stripping again is a no-op.
    assert ext._strip_previous_retry_messages(cleaned) == cleaned


# ---------------------------------------------------------------------------
# _build_system_prompt / _build_user_prompt
# ---------------------------------------------------------------------------


def test_build_system_prompt_adds_quality_section_for_entities() -> None:
    schema = {"properties": {"entities": {}}}
    prompt = _make_extractor()._build_system_prompt("Base.", schema, enable_quality_check=True)
    assert "Quality Requirements:" in prompt
    assert "ExtractStructuredData tool" in prompt


def test_build_system_prompt_no_quality_section_when_disabled() -> None:
    schema = {"properties": {"entities": {}}}
    prompt = _make_extractor()._build_system_prompt("Base.", schema, enable_quality_check=False)
    assert "Quality Requirements:" not in prompt
    assert "ExtractStructuredData tool" in prompt


def test_build_system_prompt_no_quality_section_without_entities() -> None:
    schema = {"properties": {"templates": {}}}
    prompt = _make_extractor()._build_system_prompt("Base.", schema, enable_quality_check=True)
    assert "Quality Requirements:" not in prompt


def test_build_user_prompt_without_instructions() -> None:
    prompt = _make_extractor()._build_user_prompt("the text", None)
    assert "<document>\nthe text\n</document>" in prompt
    assert "<instructions>" not in prompt


def test_build_user_prompt_with_instructions() -> None:
    prompt = _make_extractor()._build_user_prompt("the text", "be terse")
    assert "<instructions>\nbe terse\n</instructions>" in prompt
    assert "<document>\nthe text\n</document>" in prompt


# ---------------------------------------------------------------------------
# _build_result_output
# ---------------------------------------------------------------------------


def test_build_result_output_dict_merges_and_adds_metadata() -> None:
    out = _make_extractor()._build_result_output(
        {"entities": [1]}, "gpt-x", ["err"], 2, 100, 50, success=True
    )
    assert out["entities"] == [1]
    meta = out["_metadata"]
    assert meta["model"] == "gpt-x"
    assert meta["validation_errors"] == ["err"]
    assert meta["attempts"] == 2
    assert meta["input_tokens"] == 100
    assert meta["output_tokens"] == 50
    assert meta["success"] is True


def test_build_result_output_non_dict_wraps_in_data() -> None:
    out = _make_extractor()._build_result_output(["a", "b"], "m", [], 1, 0, 0, success=False)
    assert out["data"] == ["a", "b"]
    assert out["_metadata"]["success"] is False


# ---------------------------------------------------------------------------
# _build_error_retry_prompt
# ---------------------------------------------------------------------------


def test_error_retry_prompt_non_actionable_returns_empty() -> None:
    prompt = _make_extractor()._build_error_retry_prompt(ValueError("empty response from model"))
    assert prompt == ""


def test_error_retry_prompt_truncated_focus_message() -> None:
    err = ValueError("bad json")
    err.was_truncated = True  # type: ignore[attr-defined]
    prompt = _make_extractor()._build_error_retry_prompt(err)
    assert "incomplete/truncated" in prompt


def test_error_retry_prompt_actionable_includes_error() -> None:
    prompt = _make_extractor()._build_error_retry_prompt(ValueError("schema mismatch"))
    assert "Previous attempt failed" in prompt
    assert "schema mismatch" in prompt


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
