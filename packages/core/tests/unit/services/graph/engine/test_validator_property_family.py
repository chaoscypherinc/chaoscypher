# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for services/graph/engine/validator.py.

Exercises the PropertyValidator static-method family, the high-level
``validate_properties`` orchestration, and ``TemplateValidator.validate_not_system_prefix``.

These are pure pydantic/value-coercion paths with no I/O, so the tests construct
``PropertyDefinition`` models directly and assert on coerced return values or the
``PropertyValidationError`` / ``ValidationError`` raised at each branch.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from chaoscypher_core.exceptions import PropertyValidationError, ValidationError
from chaoscypher_core.models import PropertyDefinition, PropertyType
from chaoscypher_core.services.graph.engine.validator import (
    PropertyValidator,
    TemplateValidator,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pd(
    property_type: PropertyType,
    *,
    name: str = "prop",
    required: bool = False,
    default_value=None,
    enum_values=None,
    validation_pattern=None,
) -> PropertyDefinition:
    """Build a PropertyDefinition with sensible defaults for the given type."""
    return PropertyDefinition(
        name=name,
        display_name=name.title(),
        property_type=property_type,
        required=required,
        default_value=default_value,
        enum_values=enum_values,
        validation_pattern=validation_pattern,
    )


def _validate(value, prop_def: PropertyDefinition):
    """Run a single value through the private dispatch entrypoint."""
    return PropertyValidator._validate_property_value(prop_def.name, value, prop_def)


# ---------------------------------------------------------------------------
# TemplateValidator.validate_not_system_prefix
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateNotSystemPrefix:
    def test_accepts_normal_name(self) -> None:
        # Should not raise
        TemplateValidator.validate_not_system_prefix("My Template")

    def test_rejects_system_underscore(self) -> None:
        with pytest.raises(ValidationError):
            TemplateValidator.validate_not_system_prefix("system_workflow")

    def test_rejects_system_space(self) -> None:
        with pytest.raises(ValidationError):
            TemplateValidator.validate_not_system_prefix("system thing")

    def test_rejects_case_insensitive(self) -> None:
        with pytest.raises(ValidationError):
            TemplateValidator.validate_not_system_prefix("System_Mixed")


# ---------------------------------------------------------------------------
# None handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoneHandling:
    def test_none_optional_returns_none(self) -> None:
        prop_def = _pd(PropertyType.STRING, required=False)
        assert _validate(None, prop_def) is None

    def test_none_required_raises(self) -> None:
        prop_def = _pd(PropertyType.STRING, required=True)
        with pytest.raises(PropertyValidationError):
            _validate(None, prop_def)


# ---------------------------------------------------------------------------
# String / Text
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStringAndText:
    def test_string_happy(self) -> None:
        prop_def = _pd(PropertyType.STRING)
        assert _validate("hello", prop_def) == "hello"

    def test_string_non_string_raises(self) -> None:
        prop_def = _pd(PropertyType.STRING)
        with pytest.raises(PropertyValidationError):
            _validate(123, prop_def)

    def test_string_pattern_match(self) -> None:
        prop_def = _pd(PropertyType.STRING, validation_pattern=r"^[a-z]+$")
        assert _validate("abc", prop_def) == "abc"

    def test_string_pattern_mismatch_raises(self) -> None:
        prop_def = _pd(PropertyType.STRING, validation_pattern=r"^[a-z]+$")
        with pytest.raises(PropertyValidationError):
            _validate("ABC123", prop_def)

    def test_text_happy(self) -> None:
        prop_def = _pd(PropertyType.TEXT)
        assert _validate("multi\nline", prop_def) == "multi\nline"

    def test_text_non_string_raises(self) -> None:
        prop_def = _pd(PropertyType.TEXT)
        with pytest.raises(PropertyValidationError):
            _validate(["not", "a", "string"], prop_def)


# ---------------------------------------------------------------------------
# Integer
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInteger:
    def test_integer_happy(self) -> None:
        prop_def = _pd(PropertyType.INTEGER)
        assert _validate(42, prop_def) == 42

    def test_integer_rejects_bool(self) -> None:
        prop_def = _pd(PropertyType.INTEGER)
        with pytest.raises(PropertyValidationError):
            _validate(True, prop_def)

    def test_integer_coerces_numeric_string(self) -> None:
        prop_def = _pd(PropertyType.INTEGER)
        assert _validate("17", prop_def) == 17

    def test_integer_non_numeric_raises(self) -> None:
        prop_def = _pd(PropertyType.INTEGER)
        with pytest.raises(PropertyValidationError):
            _validate("not-a-number", prop_def)


# ---------------------------------------------------------------------------
# Float
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFloat:
    def test_float_happy(self) -> None:
        prop_def = _pd(PropertyType.FLOAT)
        assert _validate(3.5, prop_def) == 3.5

    def test_float_from_int(self) -> None:
        prop_def = _pd(PropertyType.FLOAT)
        result = _validate(4, prop_def)
        assert result == 4.0
        assert isinstance(result, float)

    def test_float_rejects_bool(self) -> None:
        prop_def = _pd(PropertyType.FLOAT)
        with pytest.raises(PropertyValidationError):
            _validate(False, prop_def)

    def test_float_coerces_numeric_string(self) -> None:
        prop_def = _pd(PropertyType.FLOAT)
        assert _validate("2.25", prop_def) == 2.25

    def test_float_non_numeric_raises(self) -> None:
        prop_def = _pd(PropertyType.FLOAT)
        with pytest.raises(PropertyValidationError):
            _validate("xyz", prop_def)


# ---------------------------------------------------------------------------
# Boolean
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBoolean:
    def test_boolean_passthrough_true(self) -> None:
        prop_def = _pd(PropertyType.BOOLEAN)
        assert _validate(True, prop_def) is True

    def test_boolean_string_truthy(self) -> None:
        prop_def = _pd(PropertyType.BOOLEAN)
        assert _validate("Yes", prop_def) is True

    def test_boolean_string_falsy(self) -> None:
        prop_def = _pd(PropertyType.BOOLEAN)
        assert _validate("no", prop_def) is False

    def test_boolean_invalid_string_raises(self) -> None:
        prop_def = _pd(PropertyType.BOOLEAN)
        with pytest.raises(PropertyValidationError):
            _validate("maybe", prop_def)

    def test_boolean_non_str_non_bool_raises(self) -> None:
        prop_def = _pd(PropertyType.BOOLEAN)
        with pytest.raises(PropertyValidationError):
            _validate(5, prop_def)


# ---------------------------------------------------------------------------
# Date
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDate:
    def test_date_object_passthrough(self) -> None:
        prop_def = _pd(PropertyType.DATE)
        assert _validate(date(2026, 1, 2), prop_def) == "2026-01-02"

    def test_date_iso_string(self) -> None:
        prop_def = _pd(PropertyType.DATE)
        assert _validate("2026-03-04", prop_def) == "2026-03-04"

    def test_date_iso_with_z_suffix(self) -> None:
        prop_def = _pd(PropertyType.DATE)
        assert _validate("2026-03-04T10:00:00Z", prop_def) == "2026-03-04"

    def test_date_bad_format_raises(self) -> None:
        prop_def = _pd(PropertyType.DATE)
        with pytest.raises(PropertyValidationError):
            _validate("not-a-date", prop_def)

    def test_date_wrong_type_raises(self) -> None:
        prop_def = _pd(PropertyType.DATE)
        with pytest.raises(PropertyValidationError):
            _validate(12345, prop_def)


# ---------------------------------------------------------------------------
# Datetime
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDatetime:
    def test_datetime_object_passthrough(self) -> None:
        prop_def = _pd(PropertyType.DATETIME)
        dt = datetime(2026, 1, 2, 3, 4, 5)  # noqa: DTZ001 - naive datetime is the test input
        assert _validate(dt, prop_def) == dt.isoformat()

    def test_datetime_iso_string(self) -> None:
        prop_def = _pd(PropertyType.DATETIME)
        result = _validate("2026-03-04T05:06:07", prop_def)
        assert result == "2026-03-04T05:06:07"

    def test_datetime_z_suffix(self) -> None:
        prop_def = _pd(PropertyType.DATETIME)
        result = _validate("2026-03-04T05:06:07Z", prop_def)
        assert "2026-03-04T05:06:07" in result

    def test_datetime_bad_format_raises(self) -> None:
        prop_def = _pd(PropertyType.DATETIME)
        with pytest.raises(PropertyValidationError):
            _validate("garbage", prop_def)

    def test_datetime_wrong_type_raises(self) -> None:
        prop_def = _pd(PropertyType.DATETIME)
        with pytest.raises(PropertyValidationError):
            _validate(99, prop_def)


# ---------------------------------------------------------------------------
# URL
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUrl:
    def test_url_happy(self) -> None:
        prop_def = _pd(PropertyType.URL)
        assert _validate("https://example.com/x", prop_def) == "https://example.com/x"

    def test_url_non_string_raises(self) -> None:
        prop_def = _pd(PropertyType.URL)
        with pytest.raises(PropertyValidationError):
            _validate(123, prop_def)

    def test_url_invalid_format_raises(self) -> None:
        prop_def = _pd(PropertyType.URL)
        with pytest.raises(PropertyValidationError):
            _validate("ftp://example.com", prop_def)

    def test_url_custom_pattern_match(self) -> None:
        prop_def = _pd(PropertyType.URL, validation_pattern=r"^https://example\.com/.+")
        assert _validate("https://example.com/page", prop_def) == "https://example.com/page"

    def test_url_custom_pattern_mismatch_raises(self) -> None:
        prop_def = _pd(PropertyType.URL, validation_pattern=r"^https://example\.com/.+")
        with pytest.raises(PropertyValidationError):
            _validate("https://other.com/page", prop_def)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmail:
    def test_email_happy(self) -> None:
        prop_def = _pd(PropertyType.EMAIL)
        assert _validate("user@example.com", prop_def) == "user@example.com"

    def test_email_non_string_raises(self) -> None:
        prop_def = _pd(PropertyType.EMAIL)
        with pytest.raises(PropertyValidationError):
            _validate(["a@b.com"], prop_def)

    def test_email_invalid_format_raises(self) -> None:
        prop_def = _pd(PropertyType.EMAIL)
        with pytest.raises(PropertyValidationError):
            _validate("not-an-email", prop_def)

    def test_email_custom_pattern_match(self) -> None:
        prop_def = _pd(PropertyType.EMAIL, validation_pattern=r"^[a-z]+@corp\.com$")
        assert _validate("dev@corp.com", prop_def) == "dev@corp.com"

    def test_email_custom_pattern_mismatch_raises(self) -> None:
        prop_def = _pd(PropertyType.EMAIL, validation_pattern=r"^[a-z]+@corp\.com$")
        with pytest.raises(PropertyValidationError):
            _validate("dev@other.com", prop_def)


# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnum:
    def test_enum_happy(self) -> None:
        prop_def = _pd(PropertyType.ENUM, enum_values=["red", "green", "blue"])
        assert _validate("green", prop_def) == "green"

    def test_enum_non_string_raises(self) -> None:
        prop_def = _pd(PropertyType.ENUM, enum_values=["red"])
        with pytest.raises(PropertyValidationError):
            _validate(1, prop_def)

    def test_enum_no_values_raises(self) -> None:
        prop_def = _pd(PropertyType.ENUM, enum_values=None)
        with pytest.raises(PropertyValidationError):
            _validate("anything", prop_def)

    def test_enum_not_in_list_raises(self) -> None:
        prop_def = _pd(PropertyType.ENUM, enum_values=["red", "green"])
        with pytest.raises(PropertyValidationError):
            _validate("purple", prop_def)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestJson:
    def test_json_dict_passthrough(self) -> None:
        prop_def = _pd(PropertyType.JSON)
        payload = {"a": 1, "b": [2, 3]}
        assert _validate(payload, prop_def) == payload

    def test_json_list_passthrough(self) -> None:
        prop_def = _pd(PropertyType.JSON)
        payload = [1, 2, 3]
        assert _validate(payload, prop_def) == payload


# ---------------------------------------------------------------------------
# Node reference
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNodeReference:
    def test_node_reference_accepts_valid_prefix(self) -> None:
        prop_def = _pd(PropertyType.NODE_REFERENCE)
        assert _validate("node_abc", prop_def) == "node_abc"

    def test_node_reference_non_string_raises(self) -> None:
        prop_def = _pd(PropertyType.NODE_REFERENCE)
        with pytest.raises(PropertyValidationError):
            _validate(42, prop_def)

    def test_node_reference_bad_prefix_raises(self) -> None:
        prop_def = _pd(PropertyType.NODE_REFERENCE)
        with pytest.raises(PropertyValidationError):
            _validate("random_id", prop_def)


# ---------------------------------------------------------------------------
# Node reference list
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNodeReferenceList:
    def test_node_reference_list_happy(self) -> None:
        prop_def = _pd(PropertyType.NODE_REFERENCE_LIST)
        value = ["node_a", "edge_b", "template_c"]
        assert _validate(value, prop_def) == value

    def test_node_reference_list_non_list_raises(self) -> None:
        prop_def = _pd(PropertyType.NODE_REFERENCE_LIST)
        with pytest.raises(PropertyValidationError):
            _validate("node_a", prop_def)

    def test_node_reference_list_non_string_item_raises(self) -> None:
        prop_def = _pd(PropertyType.NODE_REFERENCE_LIST)
        with pytest.raises(PropertyValidationError):
            _validate(["node_a", 99], prop_def)

    def test_node_reference_list_bad_prefix_item_raises(self) -> None:
        prop_def = _pd(PropertyType.NODE_REFERENCE_LIST)
        with pytest.raises(PropertyValidationError):
            _validate(["node_a", "oops"], prop_def)


# ---------------------------------------------------------------------------
# validate_properties (orchestration)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateProperties:
    def test_required_missing_no_default_raises(self) -> None:
        defs = [_pd(PropertyType.STRING, name="title", required=True)]
        with pytest.raises(PropertyValidationError):
            PropertyValidator.validate_properties({}, defs)

    def test_required_missing_applies_default(self) -> None:
        defs = [
            _pd(PropertyType.STRING, name="title", required=True, default_value="untitled"),
        ]
        result = PropertyValidator.validate_properties({}, defs)
        assert result["title"] == "untitled"

    def test_optional_missing_applies_default(self) -> None:
        defs = [
            _pd(PropertyType.INTEGER, name="count", required=False, default_value=7),
        ]
        result = PropertyValidator.validate_properties({}, defs)
        assert result["count"] == 7

    def test_present_value_is_validated_and_coerced(self) -> None:
        defs = [_pd(PropertyType.INTEGER, name="count")]
        result = PropertyValidator.validate_properties({"count": "33"}, defs)
        assert result["count"] == 33

    def test_present_value_invalid_raises(self) -> None:
        defs = [_pd(PropertyType.INTEGER, name="count")]
        with pytest.raises(PropertyValidationError):
            PropertyValidator.validate_properties({"count": "nope"}, defs)

    def test_apply_defaults_false_skips_defaults(self) -> None:
        # required-missing with apply_defaults=False should raise even with a default present
        defs = [
            _pd(PropertyType.STRING, name="title", required=True, default_value="x"),
        ]
        with pytest.raises(PropertyValidationError):
            PropertyValidator.validate_properties({}, defs, apply_defaults=False)

    def test_extra_unknown_property_passes_through(self) -> None:
        # Properties not in defs are preserved untouched.
        defs = [_pd(PropertyType.STRING, name="known")]
        result = PropertyValidator.validate_properties({"known": "ok", "extra": "kept"}, defs)
        assert result["extra"] == "kept"
        assert result["known"] == "ok"
