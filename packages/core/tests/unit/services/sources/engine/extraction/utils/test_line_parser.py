# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for line_parser alias parsing, descriptor separation, and entity parsing."""

from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
    _is_proper_name_alias,
    _is_valid_alias,
    _parse_aliases,
    _separate_descriptors,
    _strip_markdown_decoration,
    parse_entity_line,
    parse_relationship_line,
)


class TestIsValidAlias:
    """Tests for _is_valid_alias() garbage filtering."""

    def test_rejects_parentheses(self):
        """Aliases with parentheses are rejected."""
        assert _is_valid_alias("should be Rostov)") is False

    def test_rejects_brackets(self):
        """Aliases with brackets are rejected."""
        assert _is_valid_alias("[Annette]") is False
        assert _is_valid_alias("[Annette Schérer, Annette]") is False

    def test_rejects_curly_braces(self):
        """Aliases with curly braces are rejected."""
        assert _is_valid_alias("{entity}") is False

    def test_rejects_equals_sign(self):
        """Aliases containing '=' are rejected."""
        assert _is_valid_alias("Location=Paris, France") is False
        assert _is_valid_alias("Type=Person") is False

    def test_rejects_null_values(self):
        """Known null/placeholder values are rejected."""
        assert _is_valid_alias("N/A") is False
        assert _is_valid_alias("none") is False
        assert _is_valid_alias("unknown") is False
        assert _is_valid_alias("null") is False
        assert _is_valid_alias("None") is False
        assert _is_valid_alias("UNKNOWN") is False
        assert _is_valid_alias("na") is False
        assert _is_valid_alias("nil") is False

    def test_rejects_meta_prefixes(self):
        """LLM meta-commentary prefixes are rejected."""
        assert _is_valid_alias("should be Rostov") is False
        assert _is_valid_alias("also known as Pierre") is False
        assert _is_valid_alias("formerly Prince Andrew") is False
        assert _is_valid_alias("previously called Anna") is False
        assert _is_valid_alias("Should Be Rostov") is False

    def test_accepts_normal_names(self):
        """Normal proper names are accepted."""
        assert _is_valid_alias("Prince Andrew") is True
        assert _is_valid_alias("Andrei") is True
        assert _is_valid_alias("Anna Pávlovna Schérer") is True
        assert _is_valid_alias("Pierre Bezukhov") is True

    def test_accepts_single_word_names(self):
        """Single-word proper names are accepted."""
        assert _is_valid_alias("Napoleon") is True
        assert _is_valid_alias("Andrei") is True


class TestParseAliases:
    """Tests for _parse_aliases() splitting behavior."""

    def test_semicolon_separated(self):
        """Standard semicolon-separated aliases split correctly."""
        valid, rejected = _parse_aliases("Andrei; Prince Andrew; Andrew Bolkonsky")
        assert valid == ["Andrei", "Prince Andrew", "Andrew Bolkonsky"]
        assert rejected == []

    def test_comma_separated(self):
        """Comma-space separated aliases split correctly."""
        valid, rejected = _parse_aliases("Annette Schérer, Annette, Anna Pávlovna")
        assert valid == ["Annette Schérer", "Annette", "Anna Pávlovna"]
        assert rejected == []

    def test_mixed_semicolons_and_commas(self):
        """Mixed delimiters split correctly."""
        valid, rejected = _parse_aliases("Andrei; Annette, Anna Pávlovna")
        assert valid == ["Andrei", "Annette", "Anna Pávlovna"]
        assert rejected == []

    def test_empty_string(self):
        """Empty string returns empty lists."""
        assert _parse_aliases("") == ([], [])

    def test_whitespace_only(self):
        """Whitespace-only string returns empty lists."""
        assert _parse_aliases("   ") == ([], [])

    def test_filters_short_parts(self):
        """Parts shorter than 2 characters are filtered out."""
        valid, rejected = _parse_aliases("Anna; ; A; Bo")
        assert valid == ["Anna", "Bo"]
        assert rejected == []

    def test_single_alias(self):
        """Single alias without delimiters is returned as-is."""
        valid, rejected = _parse_aliases("Prince Andrew")
        assert valid == ["Prince Andrew"]
        assert rejected == []

    def test_garbage_aliases_rejected(self):
        """Garbage aliases are captured in rejected list."""
        valid, rejected = _parse_aliases("Andrei; N/A; should be Rostov)")
        assert valid == ["Andrei"]
        assert "N/A" in rejected
        assert "should be Rostov)" in rejected

    def test_mixed_valid_and_garbage(self):
        """Mix of valid and garbage aliases are correctly separated."""
        valid, rejected = _parse_aliases("Pierre; Location=Paris; Anna; [brackets]")
        assert valid == ["Pierre", "Anna"]
        assert "Location=Paris" in rejected
        assert "[brackets]" in rejected


class TestIsProperNameAlias:
    """Tests for _is_proper_name_alias() classification."""

    def test_capitalized_name_is_proper(self):
        """Name with uppercase word is a proper name."""
        assert _is_proper_name_alias("Princess Anna") is True

    def test_all_lowercase_is_descriptor(self):
        """All-lowercase phrase is a descriptor."""
        assert _is_proper_name_alias("the friend of childhood") is False

    def test_possessive_phrase_is_descriptor(self):
        """Possessive phrase with 's is a descriptor."""
        assert _is_proper_name_alias("Boris's mother") is False

    def test_the_with_uppercase_is_proper(self):
        """'The X' with uppercase is a proper name (e.g., 'The Hound')."""
        assert _is_proper_name_alias("The Hound") is True

    def test_single_capitalized_word(self):
        """Single capitalized word is a proper name."""
        assert _is_proper_name_alias("Napoleon") is True

    def test_the_lowercase_is_descriptor(self):
        """All-lowercase 'the careworn woman' is a descriptor."""
        assert _is_proper_name_alias("the careworn woman") is False


class TestSeparateDescriptors:
    """Tests for _separate_descriptors() separation logic."""

    def test_mixed_list(self):
        """Mixed list separates proper names from descriptors."""
        aliases = ["Princess Anna", "the friend of childhood", "Boris's mother", "The Hound"]
        proper, descriptors = _separate_descriptors(aliases)

        assert proper == ["Princess Anna", "The Hound"]
        assert descriptors == ["the friend of childhood", "Boris's mother"]

    def test_all_proper_names(self):
        """All proper names produce empty descriptors list."""
        aliases = ["Andrei", "Prince Andrew"]
        proper, descriptors = _separate_descriptors(aliases)

        assert proper == ["Andrei", "Prince Andrew"]
        assert descriptors == []

    def test_all_descriptors(self):
        """All descriptors produce empty proper list."""
        aliases = ["the hostess", "Boris's mother"]
        proper, descriptors = _separate_descriptors(aliases)

        assert proper == []
        assert descriptors == ["the hostess", "Boris's mother"]

    def test_empty_list(self):
        """Empty list produces empty results."""
        proper, descriptors = _separate_descriptors([])
        assert proper == []
        assert descriptors == []


class TestParseEntityLineDescriptors:
    """Tests that parse_entity_line() separates aliases from descriptors."""

    def test_mixed_aliases_and_descriptors(self):
        """Entity line with mixed aliases/descriptors separates correctly."""
        line = "E|Anna|Character|Princess Anna; the hostess; Boris's mother|0.9|S1|A character"
        entity = parse_entity_line(line)

        assert entity is not None
        assert entity["aliases"] == ["Princess Anna"]
        assert entity["descriptors"] == ["the hostess", "Boris's mother"]
        assert entity["sent_ref"] == "S1"

    def test_only_proper_aliases(self):
        """Entity with only proper-name aliases has no descriptors key."""
        line = "E|Anna|Character|Princess Anna; Annette|0.9|S1|A character"
        entity = parse_entity_line(line)

        assert entity is not None
        assert entity["aliases"] == ["Princess Anna", "Annette"]
        assert "descriptors" not in entity

    def test_comma_separated_aliases_in_entity(self):
        """Entity line with comma-separated aliases splits them correctly."""
        line = "E|Anna|Character|Annette, Princess Anna, Anna Pávlovna|0.9|S1|A character"
        entity = parse_entity_line(line)

        assert entity is not None
        assert entity["aliases"] == ["Annette", "Princess Anna", "Anna Pávlovna"]

    def test_garbage_aliases_stored_as_rejected(self):
        """Entity with garbage aliases stores them under rejected_aliases."""
        line = "E|Rostov|Character|N/A; should be Nikolai|0.9|S1|A character"
        entity = parse_entity_line(line)

        assert entity is not None
        assert entity["aliases"] == []
        assert "rejected_aliases" in entity
        assert "N/A" in entity["rejected_aliases"]
        assert "should be Nikolai" in entity["rejected_aliases"]

    def test_no_rejected_key_when_all_valid(self):
        """Entity with all valid aliases has no rejected_aliases key."""
        line = "E|Anna|Character|Princess Anna; Annette|0.9|S1|A character"
        entity = parse_entity_line(line)

        assert entity is not None
        assert "rejected_aliases" not in entity

    def test_rejects_line_without_sent_ref(self):
        """Entity line missing sent_ref is rejected (V1 format unsupported)."""
        # 5-field line — no sent_ref
        line = "E|Anna|Character|Princess Anna|0.9|A character"
        assert parse_entity_line(line) is None

    def test_accepts_semicolon_separated_sent_ref(self):
        """Entity lines with ``S1;S15`` (semicolon-separated refs) parse.

        Regression: the prompt teaches ``;`` as the alias delimiter and
        the LLM generalizes the convention to sent_ref lists. Before this
        change every such entity was silently dropped with a
        ``bad_sent_ref`` warning.
        """
        line = (
            "E|Buonaparte|Historical Figure|Buonaparte; Antichrist|0.9|S1;S15|"
            "Refers to Napoleon Bonaparte across both sentences."
        )
        entity = parse_entity_line(line)

        assert entity is not None, "S1;S15 must be accepted as a valid sent_ref"
        assert entity["name"] == "Buonaparte"
        assert entity["sent_ref"] == "S1;S15"


class TestStripMarkdownDecoration:
    """Tests for _strip_markdown_decoration().

    Some LLMs (e.g. ministral-3:14b) decorate pipe-delimited field values
    with Markdown emphasis, producing entity names like ``**Anna**`` and
    relationship types like ``**interacts_with**``. The stripper removes
    only fully-balanced outer wrappers so inner emphasis on substrings is
    preserved.
    """

    def test_strips_double_asterisk(self):
        assert _strip_markdown_decoration("**Anna**") == "Anna"

    def test_strips_double_underscore(self):
        assert _strip_markdown_decoration("__Anna__") == "Anna"

    def test_strips_single_asterisk(self):
        assert _strip_markdown_decoration("*Anna*") == "Anna"

    def test_strips_single_underscore(self):
        assert _strip_markdown_decoration("_Anna_") == "Anna"

    def test_strips_backticks(self):
        assert _strip_markdown_decoration("`Anna`") == "Anna"

    def test_preserves_plain_text(self):
        assert _strip_markdown_decoration("Anna") == "Anna"

    def test_preserves_unbalanced_wrapper(self):
        """Unbalanced openers (no matching closer) are kept verbatim."""
        assert _strip_markdown_decoration("**Anna") == "**Anna"
        assert _strip_markdown_decoration("Anna**") == "Anna**"

    def test_preserves_inner_emphasis(self):
        """Inner emphasis on substrings is not touched — only outer wrappers."""
        assert _strip_markdown_decoration("A class **named Foo**") == "A class **named Foo**"

    def test_strips_nested_wrappers(self):
        """Bold-italic ``***Anna***`` is stripped iteratively to plain text."""
        assert _strip_markdown_decoration("***Anna***") == "Anna"

    def test_preserves_internal_underscores(self):
        """A wrapped snake_case identifier yields the inner identifier intact.

        ``_interacts_with_`` is a balanced single-underscore wrapper around
        ``interacts_with``; only the outer pair is stripped.
        """
        assert _strip_markdown_decoration("_interacts_with_") == "interacts_with"
        assert _strip_markdown_decoration("__interacts_with__") == "interacts_with"

    def test_strips_whitespace_inside_wrapper(self):
        """Whitespace inside outer wrappers is removed — bold is insertion noise."""
        assert _strip_markdown_decoration("** Anna **") == "Anna"

    def test_empty_string(self):
        assert _strip_markdown_decoration("") == ""


class TestParseEntityLineMarkdownStripping:
    """Tests confirming Markdown decoration is stripped from entity fields.

    Regression context: a 2026-05-18 audit found that ministral-3:14b
    extractions produced node labels like ``**Anna Pávlovna Schérer**`` and
    edge types like ``**interacts_with**``, fragmenting the graph (e.g.
    splitting ``interacts_with`` and ``**interacts_with**`` into two
    distinct edge types). The parser must strip these wrappers so the
    graph stays canonical regardless of model output style.
    """

    def test_strips_bold_from_name(self):
        line = "E|**Anna Pávlovna Schérer**|Character|Annette|0.9|S1|host of soirees"
        entity = parse_entity_line(line)

        assert entity is not None
        assert entity["name"] == "Anna Pávlovna Schérer"

    def test_strips_bold_from_type(self):
        line = "E|Anna|**Character**|Annette|0.9|S1|host of soirees"
        entity = parse_entity_line(line)

        assert entity is not None
        assert entity["type"] == "Character"

    def test_strips_bold_from_aliases(self):
        line = "E|Anna|Character|**Annette**|0.9|S1|host of soirees"
        entity = parse_entity_line(line)

        assert entity is not None
        assert entity["aliases"] == ["Annette"]


class TestParseRelationshipLineMarkdownStripping:
    """Tests confirming Markdown decoration is stripped from relationship type.

    See TestParseEntityLineMarkdownStripping for regression context.
    """

    def test_strips_bold_from_type(self):
        line = "R|0|1|**interacts_with**|0.9|S1|conversation at soiree"
        rel = parse_relationship_line(line)

        assert rel is not None
        assert rel["type"] == "interacts_with"

    def test_preserves_clean_type(self):
        line = "R|0|1|interacts_with|0.9|S1|conversation at soiree"
        rel = parse_relationship_line(line)

        assert rel is not None
        assert rel["type"] == "interacts_with"
