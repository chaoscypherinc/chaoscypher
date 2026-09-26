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
    sanitize_justification,
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


class TestSanitizeJustification:
    """Justification is evidence prose, never the model's deliberation (media audit 2026-09-15)."""

    def test_clean_sentence_passes_unchanged(self):
        text = "Pierre is the illegitimate son of Count Cyril Vladimirovich Bezukhov."
        assert sanitize_justification(text) == text

    def test_reasoning_markers_blank_the_field(self):
        text = (
            "The lady asks about her son. Wait, the prompt says to use numbered sentences. "
            "I will link 9 to 3 via interacts_with. Actually, S25 implies parentage."
        )
        assert sanitize_justification(text) == ""

    def test_keeps_only_the_first_two_sentences(self):
        text = "First sentence here. Second sentence here. Third sentence must go. Fourth too."
        assert sanitize_justification(text) == "First sentence here. Second sentence here."

    def test_long_single_sentence_is_cut_on_a_word_boundary(self):
        text = "word " * 100
        out = sanitize_justification(text.strip())
        assert len(out) <= 300
        assert not out.endswith(" ")
        assert out.split(" ") == ["word"] * len(out.split(" "))

    def test_empty_and_whitespace_stay_empty(self):
        assert sanitize_justification("") == ""
        assert sanitize_justification("   ") == ""

    def test_parse_relationship_line_applies_the_sanitizer(self):
        cot = (
            "No explicit parent_of link in this text. Wait, the prompt says extract "
            "relationships based on the numbered sentences. I will link 9 to 3."
        )
        result = parse_relationship_line(f"R|9|3|parent_of|0.9|S25|{cot}")
        assert result is not None
        assert result["type"] == "parent_of"
        assert result["justification"] == ""

    def test_parse_relationship_line_keeps_a_clean_justification(self):
        result = parse_relationship_line(
            "R|1|2|serves|1.0|S7|Petrushka is identified as Prince Andrew's valet."
        )
        assert result is not None
        assert result["justification"] == "Petrushka is identified as Prince Andrew's valet."


def test_escaped_pipe_in_a_name_round_trips_as_the_prompt_now_documents() -> None:
    r"""The prompt tells the model to write a literal | as \|; the parser must agree.

    Probe A2-hard scored this before the prompt said an escape existed
    (13/16 models failed); the rule and this test keep the two in step.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
        parse_entity_line,
    )

    parsed = parse_entity_line(
        r"E|Rostov \| Bolkonsky & Sons|organization||0.9|S1|A shop on the Arbat with a sign"
    )
    assert parsed is not None
    assert parsed["name"] == "Rostov | Bolkonsky & Sons"


def test_split_fields_honours_escapes_and_maxsplit() -> None:
    r"""``\|`` stays in its field, ``\\|`` is a backslash then a separator."""
    from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
        split_fields,
    )

    assert split_fields(r"a \| b|c|d", 5) == [r"a \| b", "c", "d"]
    assert split_fields(r"C:\\|org|x", 5) == [r"C:\\", "org", "x"]
    assert split_fields("k|v|prose | with | pipes", 2) == ["k", "v", "prose | with | pipes"]
    assert split_fields("", 5) == [""]


class TestEntityLineAnchoring:
    """Entity fields are located from the confidence + sent_ref pair, not by position."""

    def test_omitted_aliases_field_parses_with_no_aliases(self) -> None:
        """``E|name|type|conf|ref|desc`` (aliases slot dropped) keeps the entity."""
        entity = parse_entity_line(
            "E|Rousseau|Author|1.0|S7|The author of the Contrat Social, argued over"
        )
        assert entity is not None
        assert entity["name"] == "Rousseau"
        assert entity["type"] == "Author"
        assert entity["aliases"] == []
        assert entity["confidence"] == 1.0
        assert entity["sent_ref"] == "S7"
        assert entity["description"] == "The author of the Contrat Social, argued over"

    def test_extra_empty_field_after_aliases_parses(self) -> None:
        """A doubled ``||`` after the alias no longer shifts the sent_ref slot."""
        entity = parse_entity_line(
            "E|Anna Pávlovna|Character|Anna Pávlovna||1.0|S1, S9|The hostess of the soirée"
        )
        assert entity is not None
        assert entity["name"] == "Anna Pávlovna"
        assert entity["aliases"] == ["Anna Pávlovna"]
        assert entity["confidence"] == 1.0
        assert entity["sent_ref"] == "S1, S9"
        assert entity["description"] == "The hostess of the soirée"

    def test_canonical_seven_field_line_unchanged(self) -> None:
        """The documented layout parses exactly as before."""
        entity = parse_entity_line(
            "E|Prince Andrei|Character|Andrei; Prince Andrew|0.9|S1-S3|The eldest son"
        )
        assert entity == {
            "name": "Prince Andrei",
            "type": "Character",
            "description": "The eldest son",
            "aliases": ["Andrei", "Prince Andrew"],
            "confidence": 0.9,
            "sent_ref": "S1-S3",
        }

    def test_unescaped_pipes_in_description_survive(self) -> None:
        """Prose after the sent_ref keeps its pipes, even ones that look like fields."""
        entity = parse_entity_line(
            "E|Pierre|Character|Pierre Bezukhov|0.9|S2|Arrives late | awkward | 0.5|S4 aside"
        )
        assert entity is not None
        assert entity["aliases"] == ["Pierre Bezukhov"]
        assert entity["sent_ref"] == "S2"
        assert entity["description"] == "Arrives late | awkward | 0.5|S4 aside"

    def test_numeric_alias_is_not_taken_for_the_confidence(self) -> None:
        """``1812 Campaign`` / ``Chapter 1`` stay aliases: the anchor needs a sent_ref next."""
        entity = parse_entity_line(
            "E|Russian campaign|Event|1812 Campaign; Louis XVI era|0.8|S3|Napoleon's invasion"
        )
        assert entity is not None
        assert entity["aliases"] == ["1812 Campaign", "Louis XVI era"]
        assert entity["confidence"] == 0.8
        assert entity["sent_ref"] == "S3"

        entity = parse_entity_line("E|Opening|Event|Chapter 1|0.7|S1|The soirée opens the book")
        assert entity is not None
        assert entity["aliases"] == ["Chapter 1"]
        assert entity["confidence"] == 0.7
        assert entity["sent_ref"] == "S1"

    def test_escaped_pipe_with_omitted_aliases(self) -> None:
        r"""``\|`` in a name still round-trips when the aliases slot is also dropped."""
        entity = parse_entity_line(r"E|Rostov \| Bolkonsky & Sons|organization|0.9|S1|A shop")
        assert entity is not None
        assert entity["name"] == "Rostov | Bolkonsky & Sons"
        assert entity["aliases"] == []
        assert entity["description"] == "A shop"

    def test_word_confidence_in_canonical_position_still_parses(self) -> None:
        """No numeric confidence: the canonical slot is used and safe_float defaults."""
        entity = parse_entity_line("E|Anna|Character|Annette|High|S1|A character")
        assert entity is not None
        assert entity["aliases"] == ["Annette"]
        assert entity["confidence"] == 0.8
        assert entity["sent_ref"] == "S1"

    def test_line_without_any_anchor_is_rejected(self) -> None:
        """Neither a sent_ref nor a confidence + sent_ref pair: dropped."""
        assert parse_entity_line("E|Anna|Character|0.9|A character|more") is None
        assert parse_entity_line("E|Anna|Character|0.9") is None

    def test_unescaped_pipe_in_a_name_is_still_rejected(self) -> None:
        """Realignment must not turn a leaked ``|`` into a corrupt entity.

        Shapes seen from local models on probe A2-hard and Book One: a spaced
        pipe in the name (with populated, empty or omitted aliases) and an
        unspaced one (``Natasha|Rostov``) that pushes a non-blank field in
        front of the confidence.
        """
        for line in (
            "E|Rostov | Bolkonsky & Sons|Location|The shop; The store|1.0|S1|A shop",
            "E|Rostov | Bolkonsky & Sons|Location||1.0|S1|A shop on the Arbat",
            "E|Rostov | Bolkonsky & Sons|Location|1.0|S1|A shop on the Arbat",
            "E|Natasha|Rostov|Natasha Rostova; Natasha|Rostov|1.0|S1|Young noblewoman",
            "E|Montmorencys|Rohans|Location|Montmorencys; Rohans|1.0|S7|A noble family",
        ):
            assert parse_entity_line(line) is None, line

    def test_several_blank_fields_before_the_confidence_are_tolerated(self) -> None:
        """``|||`` after the alias is the doubled-field drift, not a pipe leak."""
        entity = parse_entity_line("E|Kutuzov|Character|Kutuzov| ||1.0|S1, S2|The commander")
        assert entity is not None
        assert entity["aliases"] == ["Kutuzov"]
        assert entity["sent_ref"] == "S1, S2"
