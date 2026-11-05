# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the one shared document-tag rule.

Every surface (MCP, REST, CLI, package import) validates tags through
``chaoscypher_core.services.sources.tags``; these tests pin the rule's
shape, its limits and its refusal wording character for character.
"""

import pydantic
import pytest
from pydantic import BaseModel

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.policy import TAG_NAME_MAX_LENGTH, TAGS_PER_SOURCE_MAX
from chaoscypher_core.services.sources.tags import (
    TAG_NAME_PATTERN,
    TagMatch,
    TagName,
    TagNames,
    TagRuleError,
    ensure_tag_count,
    normalize_tag,
    validate_tag,
    validate_tags,
)


def _refusal(tag: str) -> str:
    return (
        f"tag \"{tag}\" is not allowed: use a-z, 0-9, ':', '_' or '-', "
        "starting with a letter or digit, at most 64 characters"
    )


def test_limits_are_owner_fixed() -> None:
    assert TAG_NAME_MAX_LENGTH == 64
    assert TAGS_PER_SOURCE_MAX == 32
    assert TAG_NAME_PATTERN.pattern == "^[a-z0-9][a-z0-9:_-]{0,63}$"


def test_tag_match_values() -> None:
    assert TagMatch.ANY.value == "any"
    assert TagMatch.ALL.value == "all"
    assert TagMatch("any") is TagMatch.ANY
    assert TagMatch("all") is TagMatch.ALL


@pytest.mark.parametrize(
    "raw", ["Work Docs", "-lead", ":x", "_x", "a" * 65, "", "   ", "naïve", "a/b"]
)
def test_bad_tags_are_refused_naming_the_tag(raw: str) -> None:
    with pytest.raises(TagRuleError) as e:
        validate_tag(raw)
    assert e.value.message == _refusal(raw.strip())


def test_refusal_is_a_tags_validation_error() -> None:
    with pytest.raises(ValidationError) as e:
        validate_tag("Work Docs")
    assert e.value.field == "tags"
    assert e.value.code == "VALIDATION_ERROR"


def test_tags_normalise() -> None:
    assert normalize_tag("  Research ") == "research"
    assert validate_tag("  Research ") == "research"


@pytest.mark.parametrize("raw", ["project:alpha", "a_b-c", "q3", "x" * 64])
def test_allowed_shapes(raw: str) -> None:
    assert validate_tag(raw) == raw


def test_duplicates_dropped_after_normalising() -> None:
    assert validate_tags(["Work", "work", " WORK "]) == ["work"]


def test_first_seen_order_kept() -> None:
    assert validate_tags(["beta", "Alpha", "beta", "gamma"]) == ["beta", "alpha", "gamma"]


def test_32_tags_allowed() -> None:
    tags = [f"t{i}" for i in range(32)]
    assert validate_tags(tags) == tags
    ensure_tag_count(32)


def test_33_tags_refused_naming_the_count() -> None:
    with pytest.raises(TagRuleError) as e:
        validate_tags([f"t{i}" for i in range(33)])
    assert e.value.message == "a document can carry at most 32 tags; this would give it 33"


def test_count_refusal_names_the_real_count() -> None:
    with pytest.raises(TagRuleError) as e:
        ensure_tag_count(40)
    assert e.value.message == "a document can carry at most 32 tags; this would give it 40"


def test_duplicates_do_not_count_toward_the_cap() -> None:
    tags = [f"t{i}" for i in range(32)]
    assert validate_tags([*tags, "T0", " t1 "]) == tags


def test_non_text_tag_refused() -> None:
    with pytest.raises(TagRuleError) as e:
        validate_tag(3)
    assert e.value.message == "a tag is text, not int"


def test_non_text_tag_in_a_list_refused() -> None:
    with pytest.raises(TagRuleError) as e:
        validate_tags(["ok", None])
    assert e.value.message == "a tag is text, not NoneType"


class _OneTag(BaseModel):
    name: TagName


class _ManyTags(BaseModel):
    tags: TagNames


def test_pydantic_types_carry_the_rule_message() -> None:
    with pytest.raises(pydantic.ValidationError) as e:
        _OneTag(name="Bad Tag")
    error = e.value.errors()[0]
    assert error["type"] == "tag_rule"
    assert error["msg"] == _refusal("Bad Tag")

    with pytest.raises(pydantic.ValidationError) as e:
        _ManyTags(tags=[f"t{i}" for i in range(33)])
    error = e.value.errors()[0]
    assert error["type"] == "tag_rule"
    assert error["msg"] == "a document can carry at most 32 tags; this would give it 33"


def test_pydantic_types_normalise_and_dedupe() -> None:
    assert _OneTag(name="  Research ").name == "research"
    assert _ManyTags(tags=["Work", "work", "q3"]).tags == ["work", "q3"]


def test_pydantic_message_keeps_braces_in_the_tag() -> None:
    with pytest.raises(pydantic.ValidationError) as e:
        _OneTag(name="a{reason}b")
    assert e.value.errors()[0]["msg"] == _refusal("a{reason}b")


def test_non_ascii_lookalike_refused_not_folded() -> None:
    # The Kelvin sign lower-cases to an ASCII "k"; the rule must refuse it
    # rather than silently store a different character.
    kelvin = "\u212a"
    assert kelvin.lower() == "k"
    with pytest.raises(TagRuleError) as e:
        validate_tag(f" {kelvin}elvin ")
    assert e.value.message == _refusal(f"{kelvin}elvin")


@pytest.mark.parametrize(
    ("raw", "type_name"),
    [("work", "str"), (b"work", "bytes"), (None, "NoneType"), ({"Alpha": 1}, "dict"), (3, "int")],
)
def test_tag_list_must_be_a_list(raw: object, type_name: str) -> None:
    with pytest.raises(TagRuleError) as e:
        validate_tags(raw)  # type: ignore[arg-type]
    assert e.value.message == f"tags are a list of text, not {type_name}"


def test_tag_list_accepts_any_non_text_iterable() -> None:
    assert validate_tags(("Alpha", "beta")) == ["alpha", "beta"]
    assert validate_tags(t for t in ["q3"]) == ["q3"]


def test_pydantic_types_refuse_non_text_with_the_rule_message() -> None:
    with pytest.raises(pydantic.ValidationError) as e:
        _OneTag(name=b"Work")  # type: ignore[arg-type]
    error = e.value.errors()[0]
    assert (error["type"], error["msg"]) == ("tag_rule", "a tag is text, not bytes")

    with pytest.raises(pydantic.ValidationError) as e:
        _ManyTags(tags=[b"Work"])  # type: ignore[list-item]
    error = e.value.errors()[0]
    assert (error["type"], error["msg"]) == ("tag_rule", "a tag is text, not bytes")

    with pytest.raises(pydantic.ValidationError) as e:
        _ManyTags(tags="work")  # type: ignore[arg-type]
    error = e.value.errors()[0]
    assert (error["type"], error["msg"]) == ("tag_rule", "tags are a list of text, not str")


def test_pydantic_types_validate_json_input() -> None:
    assert _ManyTags.model_validate_json('{"tags": ["Work", "q3", "work"]}').tags == ["work", "q3"]
    with pytest.raises(pydantic.ValidationError) as e:
        _ManyTags.model_validate_json('{"tags": "research"}')
    assert e.value.errors()[0]["msg"] == "tags are a list of text, not str"
