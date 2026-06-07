# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""All Core domain exceptions descend from ChaosCypherException.

Why: the HTTP mapper in cortex/shared/api/errors.py catches
ChaosCypherException to produce structured error envelopes. Anything
outside that hierarchy escapes as a 500 with no ``code`` field, which
breaks the API contract. This test pins the invariant for the three
known offenders identified in the 2026-04-24 architecture review.
"""

from chaoscypher_core.exceptions import (
    ChaosCypherException,
    ValidationError,
)
from chaoscypher_core.plugins.registry import DuplicatePluginError
from chaoscypher_core.services.sources.engine.extraction.content_categories import (
    UnknownContentCategoryError,
)
from chaoscypher_core.services.sources.engine.extraction.safe_user_regex import (
    PatternTooLongError,
)


def test_duplicate_plugin_error_is_chaoscypher_exception():
    err = DuplicatePluginError("foo", "/a.py", "/b.py")
    assert isinstance(err, ChaosCypherException)
    assert isinstance(err, ValidationError)
    assert err.code == "VALIDATION_ERROR"


def test_duplicate_plugin_error_keeps_value_error_compatibility():
    err = DuplicatePluginError("foo", "/a.py", "/b.py")
    assert isinstance(err, ValueError)


def test_pattern_too_long_error_is_chaoscypher_exception():
    err = PatternTooLongError("Pattern length 600 exceeds 512")
    assert isinstance(err, ChaosCypherException)
    assert isinstance(err, ValidationError)


def test_pattern_too_long_error_keeps_value_error_compatibility():
    err = PatternTooLongError("Pattern length 600 exceeds 512")
    assert isinstance(err, ValueError)


def test_unknown_content_category_error_is_chaoscypher_exception():
    err = UnknownContentCategoryError(unknown=["bogus"], available={"text"})
    assert isinstance(err, ChaosCypherException)
    assert isinstance(err, ValidationError)


def test_unknown_content_category_error_keeps_key_error_compatibility():
    err = UnknownContentCategoryError(unknown=["bogus"], available={"text"})
    assert isinstance(err, KeyError)


def test_duplicate_plugin_error_carries_field_details():
    err = DuplicatePluginError("foo", "/a.py", "/b.py")
    assert err.details["plugin_id"] == "foo"
    assert err.details["existing_path"] == "/a.py"
    assert err.details["new_path"] == "/b.py"


def test_unknown_content_category_error_carries_unknown_in_details():
    err = UnknownContentCategoryError(unknown=["bogus"], available={"text"})
    assert err.details["unknown"] == ["bogus"]
    assert set(err.details["available"]) == {"text"}
