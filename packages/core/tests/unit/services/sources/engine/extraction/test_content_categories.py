# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for content category matching."""

import re

import pytest

from chaoscypher_core.services.sources.engine.extraction.content_categories import (
    CONTENT_CATEGORIES,
    CategoryMatcher,
    compile_custom_patterns,
    resolve_categories,
)


class TestCategoryMatcher:
    """Tests for CategoryMatcher.matches()."""

    def test_count_mode_matches_when_threshold_met(self) -> None:
        """Count mode returns True when pattern matches >= threshold times."""
        matcher = CategoryMatcher(
            name="test",
            description="test",
            mode="count",
            pattern=re.compile(r"changed in version \d", re.IGNORECASE),
            threshold=3,
        )
        text = "Changed in version 3.9.\nChanged in version 3.10.\nChanged in version 3.11.\n"
        assert matcher.matches(text) is True

    def test_count_mode_no_match_below_threshold(self) -> None:
        """Count mode returns False when matches < threshold."""
        matcher = CategoryMatcher(
            name="test",
            description="test",
            mode="count",
            pattern=re.compile(r"changed in version \d"),
            threshold=3,
        )
        text = "Changed in version 3.9.\nSome other content.\n"
        assert matcher.matches(text) is False

    def test_line_ratio_mode_matches_when_ratio_met(self) -> None:
        """Line ratio mode returns True when matching lines >= threshold ratio."""
        matcher = CategoryMatcher(
            name="test",
            description="test",
            mode="line_ratio",
            pattern=re.compile(r"^\s*[-*]\s+\S"),
            threshold=0.70,
        )
        # 8 bullet lines out of 10 = 0.80 ratio
        text = "\n".join(
            [
                "- item 1",
                "- item 2",
                "- item 3",
                "- item 4",
                "- item 5",
                "- item 6",
                "- item 7",
                "- item 8",
                "Some real content here.",
                "Another real sentence.",
            ]
        )
        assert matcher.matches(text) is True

    def test_line_ratio_mode_no_match_below_ratio(self) -> None:
        """Line ratio mode returns False when matching lines < threshold ratio."""
        matcher = CategoryMatcher(
            name="test",
            description="test",
            mode="line_ratio",
            pattern=re.compile(r"^\s*[-*]\s+\S"),
            threshold=0.70,
        )
        # 3 bullet lines out of 10 = 0.30 ratio
        text = "\n".join(
            [
                "- item 1",
                "- item 2",
                "- item 3",
                "Content A.",
                "Content B.",
                "Content C.",
                "Content D.",
                "Content E.",
                "Content F.",
                "Content G.",
            ]
        )
        assert matcher.matches(text) is False

    def test_line_ratio_ignores_empty_lines(self) -> None:
        """Line ratio mode only considers non-empty lines."""
        matcher = CategoryMatcher(
            name="test",
            description="test",
            mode="line_ratio",
            pattern=re.compile(r"^\s*[-*]\s+\S"),
            threshold=0.70,
        )
        # 3 bullet lines, 1 content line, lots of blanks = 0.75 ratio of non-empty
        text = "- item 1\n\n\n- item 2\n\n- item 3\n\nSome content.\n\n\n"
        assert matcher.matches(text) is True

    def test_strip_lines_removes_matching_lines(self) -> None:
        """strip_lines returns content with matching lines removed."""
        matcher = CategoryMatcher(
            name="test",
            description="test",
            mode="line_ratio",
            pattern=re.compile(r"^\s*[-*]\s+\S"),
            threshold=0.70,
        )
        text = "- item 1\nReal content here.\n- item 2\nMore real content."
        result = matcher.strip_lines(text)
        assert "Real content here." in result
        assert "More real content." in result
        assert "- item 1" not in result
        assert "- item 2" not in result

    def test_strip_lines_only_applies_to_line_ratio_mode(self) -> None:
        """strip_lines returns original text for count mode matchers."""
        matcher = CategoryMatcher(
            name="test",
            description="test",
            mode="count",
            pattern=re.compile(r"copyright"),
            threshold=2,
        )
        text = "Copyright 2024. All rights reserved. Copyright holder."
        result = matcher.strip_lines(text)
        assert result == text


class TestResolveCategories:
    """Tests for resolve_categories lookup."""

    def test_resolves_known_categories(self) -> None:
        """Known category names resolve to matchers."""
        matchers = resolve_categories(["toc", "changelog"])
        assert len(matchers) == 2
        assert matchers[0].name == "toc"
        assert matchers[1].name == "changelog"

    def test_raises_for_unknown_category(self) -> None:
        """Unknown category name raises KeyError."""
        with pytest.raises(KeyError, match="nonexistent"):
            resolve_categories(["toc", "nonexistent"])

    def test_empty_list_returns_empty(self) -> None:
        """Empty category list returns empty matchers."""
        assert resolve_categories([]) == []


class TestCompileCustomPatterns:
    """Tests for compile_custom_patterns."""

    def test_compiles_count_pattern(self) -> None:
        """Custom count pattern compiles to a working matcher."""
        matchers = compile_custom_patterns(
            [
                {
                    "regex": "make install",
                    "mode": "count",
                    "threshold": 2,
                    "description": "Build instructions",
                }
            ]
        )
        assert len(matchers) == 1
        assert matchers[0].name == "custom_0"
        assert matchers[0].matches("Run make install. Then make install again.") is True

    def test_compiles_line_ratio_pattern(self) -> None:
        """Custom line_ratio pattern compiles to a working matcher."""
        matchers = compile_custom_patterns(
            [
                {
                    "regex": r"^\s*#",
                    "mode": "line_ratio",
                    "threshold": 0.50,
                    "description": "Comment-heavy",
                }
            ]
        )
        assert len(matchers) == 1
        text = "# comment\n# comment\ncode\ncode"
        assert matchers[0].matches(text) is True

    def test_skips_invalid_regex(self) -> None:
        """Invalid regex patterns are skipped with no error."""
        matchers = compile_custom_patterns(
            [
                {"regex": "[invalid", "mode": "count", "threshold": 1, "description": "bad"},
            ]
        )
        assert matchers == []

    def test_rejects_overlong_pattern(self) -> None:
        """Custom patterns over 512 chars are skipped with a warning."""
        long_regex = "a" * 1000
        matchers = compile_custom_patterns(
            [
                {
                    "regex": long_regex,
                    "mode": "count",
                    "threshold": 1,
                    "description": "too long",
                }
            ]
        )
        assert matchers == []

    def test_redos_pattern_does_not_hang_extraction(self) -> None:
        """Custom patterns with catastrophic backtracking return False within 1s."""
        import time

        matchers = compile_custom_patterns(
            [
                {
                    "regex": r"(a+)+b",
                    "mode": "count",
                    "threshold": 1,
                    "description": "redos payload",
                }
            ]
        )
        assert len(matchers) == 1
        payload = "a" * 30
        start = time.monotonic()
        matched = matchers[0].matches(payload)
        elapsed = time.monotonic() - start
        assert matched is False
        assert elapsed < 1.0, f"ReDoS protection failed — took {elapsed:.3f}s"


class TestUnknownContentCategoryError:
    """resolve_categories exposes a typed error with available set."""

    def test_error_carries_offending_and_available_names(self) -> None:
        """UnknownContentCategoryError exposes both the bad names and the valid set."""
        from chaoscypher_core.services.sources.engine.extraction.content_categories import (
            UnknownContentCategoryError,
        )

        with pytest.raises(UnknownContentCategoryError) as excinfo:
            resolve_categories(["toc", "made_up_name", "also_fake"])
        err = excinfo.value
        assert "made_up_name" in err.unknown
        assert "also_fake" in err.unknown
        assert "toc" not in err.unknown  # valid one not listed
        assert "toc" in err.available
        assert "changelog" in err.available

    def test_error_subclasses_key_error_for_back_compat(self) -> None:
        """Existing callers that catch KeyError continue to work."""
        from chaoscypher_core.services.sources.engine.extraction.content_categories import (
            UnknownContentCategoryError,
        )

        with pytest.raises(KeyError):
            resolve_categories(["definitely_not_a_category"])
        # And the typed error IS-A KeyError:
        assert issubclass(UnknownContentCategoryError, KeyError)


class TestBuiltInCategories:
    """Tests that built-in categories detect their target content."""

    def test_toc_detects_bullet_heavy_content(self) -> None:
        """TOC category matches content with >70% bullet lines."""
        matchers = resolve_categories(["toc"])
        toc_text = "\n".join([f"- Section {i}" for i in range(10)] + ["Introduction"])
        assert matchers[0].matches(toc_text) is True

    def test_changelog_detects_version_notes(self) -> None:
        """Changelog category matches content with dense version change annotations."""
        matchers = resolve_categories(["changelog"])
        # Threshold is 5 — needs at least 5 tight "verb in/since version N" matches.
        text = (
            "Changed in version 3.9: Added support for X.\n"
            "New in version 3.10: Feature Y.\n"
            "Deprecated since version 3.11: Old API.\n"
            "Added in version 3.12: New serialisation layer.\n"
            "Removed in version 3.11: Legacy adapter.\n"
        )
        assert matchers[0].matches(text) is True

    def test_legal_detects_copyright(self) -> None:
        """Legal category matches copyright/license text."""
        matchers = resolve_categories(["legal"])
        text = "Copyright 2024 Example Corp. All rights reserved. Licensed under MIT."
        assert matchers[0].matches(text) is True

    def test_boilerplate_detects_short_content(self) -> None:
        """Boilerplate category matches very short content."""
        matchers = resolve_categories(["boilerplate"])
        text = "See also: next page."
        assert matchers[0].matches(text) is True

    def test_all_categories_are_defined(self) -> None:
        """All 15 expected categories exist."""
        expected = {
            "toc",
            "changelog",
            "legal",
            "bibliography",
            "acknowledgments",
            "boilerplate",
            "metadata",
            "code_blocks",
            "data_tables",
            "math",
            "api_tables",
            "procedural",
            "advertising",
            "web_artifacts",
            "bulk_lists",
        }
        assert set(CONTENT_CATEGORIES.keys()) == expected


class TestValidateCustomPatterns:
    """validate_custom_patterns returns structured errors per bad pattern."""

    def test_returns_empty_list_for_all_valid_patterns(self) -> None:
        """A list of good patterns yields no errors."""
        from chaoscypher_core.services.sources.engine.extraction.content_categories import (
            validate_custom_patterns,
        )

        errors = validate_custom_patterns(
            [
                {"regex": r"\bword\b", "mode": "count", "threshold": 1, "description": "ok"},
                {"regex": r"^#", "mode": "line_ratio", "threshold": 0.5, "description": "ok"},
            ]
        )
        assert errors == []

    def test_reports_invalid_regex_with_index_and_reason(self) -> None:
        """A malformed regex is reported with index, regex text, and error."""
        from chaoscypher_core.services.sources.engine.extraction.content_categories import (
            validate_custom_patterns,
        )

        errors = validate_custom_patterns(
            [
                {"regex": r"\bword\b", "mode": "count", "threshold": 1, "description": "ok"},
                {"regex": "[invalid", "mode": "count", "threshold": 1, "description": "bad"},
            ]
        )
        assert len(errors) == 1
        err = errors[0]
        assert err["index"] == 1
        assert err["regex"] == "[invalid"
        assert err.get("error")  # non-empty reason

    def test_reports_overlong_pattern(self) -> None:
        """A pattern over 512 chars is reported as a length violation."""
        from chaoscypher_core.services.sources.engine.extraction.content_categories import (
            validate_custom_patterns,
        )

        errors = validate_custom_patterns(
            [
                {"regex": "a" * 1000, "mode": "count", "threshold": 1, "description": "long"},
            ]
        )
        assert len(errors) == 1
        assert errors[0]["index"] == 0
        assert "512" in errors[0]["error"]

    def test_reports_missing_regex_field(self) -> None:
        """A pattern with no regex field is reported (not silently dropped)."""
        from chaoscypher_core.services.sources.engine.extraction.content_categories import (
            validate_custom_patterns,
        )

        errors = validate_custom_patterns(
            [
                {"mode": "count", "threshold": 1, "description": "oops no regex"},
            ]
        )
        assert len(errors) == 1
        assert errors[0]["index"] == 0
        assert "regex" in errors[0]["error"].lower()


class TestConfigurableDomainCategoryValidation:
    """ConfigurableDomain validates category names at construction time."""

    def test_unknown_category_logs_warning_and_does_not_raise(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,  # pytest fixture, side-effect only
    ) -> None:
        """Domain with bad category name loads (with WARNING) — does not halt."""
        import logging as _logging

        from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
            ConfigurableDomain,
        )

        config = {
            "name": "bad_domain",
            "content_exclusions": {
                "categories": ["toc", "not_a_category"],
                "custom_patterns": [],
            },
        }
        with caplog.at_level(_logging.WARNING):
            domain = ConfigurableDomain(config)
        assert domain.name == "bad_domain"
        combined = " ".join(r.getMessage() for r in caplog.records)
        assert "not_a_category" in combined or "unknown_content_category" in combined

    def test_valid_categories_construct_without_warning(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,  # pytest fixture, side-effect only
    ) -> None:
        """A well-formed domain does not emit unknown-category warnings."""
        import logging as _logging

        from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
            ConfigurableDomain,
        )

        config = {
            "name": "ok_domain",
            "content_exclusions": {
                "categories": ["toc", "legal"],
                "custom_patterns": [],
            },
        }
        with caplog.at_level(_logging.WARNING):
            ConfigurableDomain(config)
        combined = " ".join(r.getMessage() for r in caplog.records)
        assert "unknown_content_category" not in combined


class TestConfigurableDomainCustomPatternValidation:
    """ConfigurableDomain validates custom_patterns at construction time."""

    def test_invalid_custom_pattern_logs_warning_and_does_not_raise(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,  # pytest fixture, side-effect only
    ) -> None:
        """Invalid regex in custom_patterns logs WARNING at load — domain still builds."""
        import logging as _logging

        from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
            ConfigurableDomain,
        )

        config = {
            "name": "bad_custom",
            "content_exclusions": {
                "categories": [],
                "custom_patterns": [
                    {"regex": "[bad", "mode": "count", "threshold": 1, "description": "bad"},
                ],
            },
        }
        with caplog.at_level(_logging.WARNING):
            domain = ConfigurableDomain(config)
        assert domain.name == "bad_custom"
        combined = " ".join(r.getMessage() for r in caplog.records)
        assert "invalid_custom_pattern" in combined or "[bad" in combined

    def test_valid_custom_patterns_no_warning(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,  # pytest fixture, side-effect only
    ) -> None:
        """A domain with all-valid custom_patterns emits no validation warnings."""
        import logging as _logging

        from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
            ConfigurableDomain,
        )

        config = {
            "name": "ok_custom",
            "content_exclusions": {
                "categories": [],
                "custom_patterns": [
                    {
                        "regex": r"\bmake install\b",
                        "mode": "count",
                        "threshold": 1,
                        "description": "ok",
                    },
                ],
            },
        }
        with caplog.at_level(_logging.WARNING):
            ConfigurableDomain(config)
        combined = " ".join(r.getMessage() for r in caplog.records)
        assert "invalid_custom_pattern" not in combined
