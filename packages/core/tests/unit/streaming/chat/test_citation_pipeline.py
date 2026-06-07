# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the chat citation pipeline (``citations.py``).

Covers the largely-pure citation/entity-reference processing helpers:
entity reference extraction + enrichment, chunk-reference normalization,
mismatched-citation correction, blockquote / paragraph citation injection,
duplicate-prose stripping, and chunk-citation extraction + enrichment.

These functions take plain ``dict`` / ``str`` inputs and rely only on
``get_settings()`` defaults, so no patching is required. Helper builders
mirror ``test_citation_by_reference.py`` (copied locally per test rules).
"""

import json

import pytest

from chaoscypher_core.streaming.chat import (
    _strip_inline_quotes_before_citations,
    correct_mismatched_citations,
    enrich_chunk_citations_from_tool_results,
    enrich_entity_references_from_tool_results,
    extract_chunk_citations,
    extract_entity_references,
    inject_citations_for_uncited_paragraphs,
    inject_citations_into_blockquotes,
    normalize_chunk_references,
    strip_duplicated_citation_text,
)
from chaoscypher_core.streaming.chat.citations import (
    _collect_entities_from_data,
    _normalize_for_dedup,
    _resolve_sentence_text,
)


# ---------------------------------------------------------------------------
# Helpers (copied locally — no cross-test imports allowed)
# ---------------------------------------------------------------------------


def _wrap(payload: dict) -> list[dict]:
    """Wrap a payload dict into a single-message tool_results list."""
    return [{"content": json.dumps(payload)}]


def _chunk(
    chunk_id: str,
    original_content: str,
    *,
    chunk_alias: str | None = None,
    filename: str = "source.txt",
    chunk_index: int | None = None,
    sentence_offsets: list[dict] | None = None,
    **extra,
) -> dict:
    """Build a chunk dict matching the tool-result schema citations.py reads."""
    chunk: dict = {
        "chunk_id": chunk_id,
        "original_content": original_content,
        "filename": filename,
    }
    if chunk_alias is not None:
        chunk["chunk_alias"] = chunk_alias
    if chunk_index is not None:
        chunk["chunk_index"] = chunk_index
    if sentence_offsets is not None:
        chunk["chunk_metadata"] = {"sentence_offsets": sentence_offsets}
    chunk.update(extra)
    return chunk


# A 60+ char distinctive quote string we can embed verbatim in chunk content
# so the 60-char search-key lands inside ``original_content``.
LONG_QUOTE = "Annette Scherer had been the maid of honour to the Empress Maria Feodorovna"


# ---------------------------------------------------------------------------
# extract_entity_references
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cortex
class TestExtractEntityReferences:
    def test_node_and_edge_mixed_separators(self):
        """node/edge refs with different separators and prefixed/plain ids."""
        content = (
            "See [[node:abc-123|Pierre]] and "
            "[[edge_def-456|knows]] plus "
            "[[node node_ff00aa|Natasha]]."
        )
        refs = extract_entity_references(content)
        assert refs["abc-123"]["type"] == "node"
        assert refs["abc-123"]["label"] == "Pierre"
        assert refs["def-456"]["type"] == "edge"
        assert refs["node_ff00aa"]["label"] == "Natasha"

    def test_no_references_returns_empty(self):
        assert extract_entity_references("no references here at all") == {}


# ---------------------------------------------------------------------------
# _collect_entities_from_data
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cortex
class TestCollectEntitiesFromData:
    def test_direct_fields_and_type_rename(self):
        data = {
            "id": "n1",
            "title": "Pierre",
            "description": "a count",
            "type": "Person",
            "template_id": "t1",
            "properties": {"age": 30},
        }
        out: dict = {}
        _collect_entities_from_data(data, out)
        info = out["n1"]
        assert info["title"] == "Pierre"
        assert info["description"] == "a count"
        # "type" is renamed to avoid collision
        assert info["entity_type"] == "Person"
        assert info["template_id"] == "t1"
        assert info["properties"] == {"age": 30}

    def test_relationship_counts_derived(self):
        data = {
            "id": "n2",
            "relationships": {
                "incoming": [{"x": 1}, {"x": 2}],
                "outgoing": [{"y": 1}],
            },
        }
        out: dict = {}
        _collect_entities_from_data(data, out)
        assert out["n2"]["incoming_count"] == 2
        assert out["n2"]["outgoing_count"] == 1

    def test_context_container_counts(self):
        data = {"id": "n3", "context": {"incoming": [1, 2, 3]}}
        out: dict = {}
        _collect_entities_from_data(data, out)
        assert out["n3"]["incoming_count"] == 3

    def test_recurses_into_nested_results_and_lists(self):
        data = {
            "results": [
                {"id": "a", "title": "A"},
                {"nodes": [{"id": "b", "title": "B"}]},
            ]
        }
        out: dict = {}
        _collect_entities_from_data(data, out)
        assert set(out) == {"a", "b"}

    def test_non_recurse_key_is_ignored(self):
        """Nested entities under a non-whitelisted key are not collected."""
        data = {"unrelated_key": {"id": "hidden", "title": "X"}}
        out: dict = {}
        _collect_entities_from_data(data, out)
        assert out == {}

    def test_primitive_input_noop(self):
        out: dict = {}
        _collect_entities_from_data("just a string", out)
        _collect_entities_from_data(42, out)
        assert out == {}


# ---------------------------------------------------------------------------
# enrich_entity_references_from_tool_results
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cortex
class TestEnrichEntityReferences:
    def test_direct_match_enrichment(self):
        refs = {"n1": {"id": "n1", "type": "node", "label": "Pierre"}}
        tool_results = _wrap(
            {
                "nodes": [
                    {
                        "id": "n1",
                        "template_id": "tpl",
                        "template_name": "Person",
                        "description": "a count",
                        "type": "Character",
                        "properties": {"k": "v"},
                    }
                ]
            }
        )
        out = enrich_entity_references_from_tool_results(refs, tool_results)
        assert out["n1"]["template_name"] == "Person"
        assert out["n1"]["description"] == "a count"
        assert out["n1"]["entity_type"] == "Character"
        assert out["n1"]["properties"] == {"k": "v"}

    def test_prefixed_id_fallback(self):
        """LLM gives plain UUID; tool result has node_ prefixed id."""
        refs = {"abc": {"id": "abc", "type": "node", "label": "X"}}
        tool_results = _wrap({"nodes": [{"id": "node_abc", "template_name": "Thing"}]})
        out = enrich_entity_references_from_tool_results(refs, tool_results)
        assert out["abc"]["template_name"] == "Thing"

    def test_edge_prefix_fallback(self):
        refs = {"e1": {"id": "e1", "type": "edge", "label": "rel"}}
        tool_results = _wrap({"edges": [{"id": "edge_e1", "description": "linked"}]})
        out = enrich_entity_references_from_tool_results(refs, tool_results)
        assert out["e1"]["description"] == "linked"

    def test_malformed_json_skipped(self):
        refs = {"n1": {"id": "n1", "type": "node", "label": "P"}}
        tool_results = [
            {"content": "{not valid json"},
            {"content": None},  # falsy content skipped
        ]
        out = enrich_entity_references_from_tool_results(refs, tool_results)
        # unchanged — nothing to enrich
        assert "template_name" not in out["n1"]

    def test_empty_inputs_passthrough(self):
        assert enrich_entity_references_from_tool_results({}, [{"content": "{}"}]) == {}
        refs = {"n1": {"id": "n1", "type": "node", "label": "P"}}
        assert enrich_entity_references_from_tool_results(refs, []) == refs


# ---------------------------------------------------------------------------
# normalize_chunk_references
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cortex
class TestNormalizeChunkReferences:
    def test_raw_chunk_form(self):
        out = normalize_chunk_references("Quote [CHUNK C0 | war.txt] here")
        assert "[[cite:C0:S1|war.txt]]" in out

    def test_raw_chunk_default_label(self):
        out = normalize_chunk_references("Quote [CHUNK C0] here")
        assert "[[cite:C0:S1|source]]" in out

    def test_paren_chunk_sentence_form(self):
        out = normalize_chunk_references("text (Chunk C0, Sentences S1, S2) end")
        assert "[[cite:C0:S1,S2|source]]" in out

    def test_alias_cite_resolution_to_uuid(self):
        tool_results = _wrap(
            {"chunks": [_chunk("uuid-1", "body", chunk_alias="C0", filename="book.txt")]}
        )
        out = normalize_chunk_references("[[cite:C0:S1|book.txt]]", tool_results)
        assert "[[cite:uuid-1:S1|book.txt]]" in out

    def test_bare_alias_paren_all_known(self):
        tool_results = _wrap(
            {
                "chunks": [
                    _chunk("u-a", "a", chunk_alias="C0", filename="f1.txt"),
                    _chunk("u-b", "b", chunk_alias="C1", filename="f2.txt"),
                ]
            }
        )
        out = normalize_chunk_references("see (C0, C1)", tool_results)
        assert "[[cite:u-a:S1|f1.txt]]" in out
        assert "[[cite:u-b:S1|f2.txt]]" in out

    def test_bare_alias_paren_partial_unknown_left_intact(self):
        tool_results = _wrap({"chunks": [_chunk("u-a", "a", chunk_alias="C0", filename="f1.txt")]})
        out = normalize_chunk_references("see (C0, C9)", tool_results)
        # C9 unknown -> whole bare-paren left untouched
        assert "(C0, C9)" in out

    def test_no_tool_results_skips_alias_passes(self):
        # Without alias_map, bare-paren and alias-cite passes are skipped.
        content = "see (C0) and [[cite:C0:S1|x]]"
        out = normalize_chunk_references(content)
        assert out == content


# ---------------------------------------------------------------------------
# correct_mismatched_citations
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cortex
class TestCorrectMismatchedCitations:
    def test_wrong_chunk_rewritten(self):
        right = _chunk("right-id", LONG_QUOTE + " in the document.", chunk_index=0)
        wrong = _chunk("wrong-id", "Totally unrelated content sitting elsewhere.", chunk_index=5)
        tool_results = _wrap({"chunks": [wrong, right]})
        content = f'> "{LONG_QUOTE}" [[cite:wrong-id:S1|source.txt]]'
        out = correct_mismatched_citations(content, tool_results)
        assert "[[cite:right-id:S1|source.txt]]" in out
        assert "wrong-id" not in out

    def test_correct_chunk_passthrough(self):
        right = _chunk("right-id", LONG_QUOTE + " in the document.", chunk_index=0)
        tool_results = _wrap({"chunks": [right]})
        content = f'> "{LONG_QUOTE}" [[cite:right-id:S1|source.txt]]'
        out = correct_mismatched_citations(content, tool_results)
        assert "[[cite:right-id:S1|source.txt]]" in out

    def test_too_short_quote_skipped(self):
        right = _chunk("right-id", "short body text", chunk_index=0)
        tool_results = _wrap({"chunks": [right]})
        content = '> "tiny" [[cite:other-id:S1|source.txt]]'
        out = correct_mismatched_citations(content, tool_results)
        # below citation_min_quote_chars (40) -> untouched
        assert "[[cite:other-id:S1|source.txt]]" in out

    def test_no_tool_results_passthrough(self):
        content = "> something [[cite:x:S1|y]]"
        assert correct_mismatched_citations(content, []) == content

    def test_blockquote_without_citation_passthrough(self):
        right = _chunk("right-id", LONG_QUOTE, chunk_index=0)
        tool_results = _wrap({"chunks": [right]})
        content = f'> "{LONG_QUOTE}"'
        out = correct_mismatched_citations(content, tool_results)
        assert out == content

    def test_no_chunk_data_passthrough(self):
        # tool_results present but contain no chunk lists
        tool_results = _wrap({"nodes": [{"id": "n"}]})
        content = "> q [[cite:x:S1|y]]"
        assert correct_mismatched_citations(content, tool_results) == content


# ---------------------------------------------------------------------------
# inject_citations_into_blockquotes
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cortex
class TestInjectCitationsIntoBlockquotes:
    def test_injects_when_missing(self):
        chunk = _chunk("c-1", LONG_QUOTE + " trailing.", chunk_index=0)
        tool_results = _wrap({"chunks": [chunk]})
        content = f'> "{LONG_QUOTE}"'
        out = inject_citations_into_blockquotes(content, tool_results)
        assert "[[cite:c-1:S1|source.txt]]" in out

    def test_skips_when_citation_present_in_run(self):
        chunk = _chunk("c-1", LONG_QUOTE, chunk_index=0)
        tool_results = _wrap({"chunks": [chunk]})
        content = f'> "{LONG_QUOTE}" [[cite:c-1:S1|source.txt]]'
        out = inject_citations_into_blockquotes(content, tool_results)
        # exactly one citation, not doubled
        assert out.count("[[cite:") == 1

    def test_skips_when_next_line_has_cite(self):
        chunk = _chunk("c-1", LONG_QUOTE, chunk_index=0)
        tool_results = _wrap({"chunks": [chunk]})
        content = f'> "{LONG_QUOTE}"\n[[cite:c-1:S1|source.txt]]'
        out = inject_citations_into_blockquotes(content, tool_results)
        assert out.count("[[cite:") == 1

    def test_no_match_left_unchanged(self):
        chunk = _chunk("c-1", "completely different chunk content here", chunk_index=0)
        tool_results = _wrap({"chunks": [chunk]})
        content = '> "A quote with no matching chunk text whatsoever yeah"'
        out = inject_citations_into_blockquotes(content, tool_results)
        assert "[[cite:" not in out

    def test_no_tool_results_passthrough(self):
        content = '> "x"'
        assert inject_citations_into_blockquotes(content, []) == content


# ---------------------------------------------------------------------------
# inject_citations_for_uncited_paragraphs
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cortex
class TestInjectCitationsForUncitedParagraphs:
    def test_appends_marker_before_trailing_punct(self):
        chunk = _chunk("p-1", LONG_QUOTE + " etc.", chunk_index=0)
        tool_results = _wrap({"chunks": [chunk]})
        content = f'The narrator notes "{LONG_QUOTE}".'
        out = inject_citations_for_uncited_paragraphs(content, tool_results)
        assert "[[cite:p-1:S1|source.txt]]" in out
        # marker placed before the final period
        assert out.rstrip().endswith("]].") or "]] ." in out or out.rstrip()[-1] == "."

    def test_skips_already_cited_paragraph(self):
        chunk = _chunk("p-1", LONG_QUOTE, chunk_index=0)
        tool_results = _wrap({"chunks": [chunk]})
        content = f'"{LONG_QUOTE}" [[cite:p-1:S1|source.txt]]'
        out = inject_citations_for_uncited_paragraphs(content, tool_results)
        assert out.count("[[cite:") == 1

    def test_skips_blockquote_paragraph(self):
        chunk = _chunk("p-1", LONG_QUOTE, chunk_index=0)
        tool_results = _wrap({"chunks": [chunk]})
        content = f'> "{LONG_QUOTE}"'
        out = inject_citations_for_uncited_paragraphs(content, tool_results)
        # blockquote handled elsewhere -> not injected here
        assert "[[cite:" not in out

    def test_no_quoted_span_no_change(self):
        chunk = _chunk("p-1", LONG_QUOTE, chunk_index=0)
        tool_results = _wrap({"chunks": [chunk]})
        content = "A plain paragraph with no quoted spans at all."
        out = inject_citations_for_uncited_paragraphs(content, tool_results)
        assert out == content

    def test_no_tool_results_passthrough(self):
        content = 'text "quoted span here longer"'
        assert inject_citations_for_uncited_paragraphs(content, []) == content


# ---------------------------------------------------------------------------
# _strip_inline_quotes_before_citations
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cortex
class TestStripInlineQuotesBeforeCitations:
    def test_strips_straight_quote_before_cite(self):
        long_quoted = '"' + ("This is a long quoted passage exceeding thirty chars") + '"'
        content = f"{long_quoted} [[cite:c1:S1|f.txt]]"
        out = _strip_inline_quotes_before_citations(content)
        assert long_quoted not in out
        assert "[[cite:c1:S1|f.txt]]" in out

    def test_strips_curly_quote_before_cite(self):
        long_quoted = "“" + "Another long curly-quoted passage over thirty chars" + "”"
        content = f"{long_quoted} [[cite:c1:S1|f.txt]]"
        out = _strip_inline_quotes_before_citations(content)
        assert "“" not in out
        assert "[[cite:c1:S1|f.txt]]" in out

    def test_strips_setup_phrase(self):
        long_quoted = '"' + "Some long passage of quoted prose exceeding thirty" + '"'
        content = f"He wrote, with the line {long_quoted} [[cite:c1:S1|f.txt]]"
        out = _strip_inline_quotes_before_citations(content)
        # the "with the line" setup phrase is collapsed away
        assert "with the line" not in out
        assert "[[cite:c1:S1|f.txt]]" in out

    def test_short_quote_left_intact(self):
        content = '"short" [[cite:c1:S1|f.txt]]'
        out = _strip_inline_quotes_before_citations(content)
        assert '"short"' in out


# ---------------------------------------------------------------------------
# _normalize_for_dedup
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cortex
class TestNormalizeForDedup:
    def test_strips_quotes_collapses_ws_lowercases(self):
        assert _normalize_for_dedup('  "Hello   World"  ') == "hello world"

    def test_curly_quotes_and_apostrophes_removed(self):
        assert _normalize_for_dedup("“It’s”") == "its"  # noqa: RUF001 - intentional curly quotes


# ---------------------------------------------------------------------------
# strip_duplicated_citation_text
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cortex
class TestStripDuplicatedCitationText:
    def test_removes_duplicated_prose(self):
        sentence = "The Emperor Alexander has solemnly declared his intentions to all"
        citations = {
            "c1:S1": {
                "chunk_id": "c1",
                "sentence_refs": "S1",
                "label": "f.txt",
                "sentence_text": sentence,
            }
        }
        content = f"{sentence} [[cite:c1:S1|f.txt]]"
        out = strip_duplicated_citation_text(content, citations)
        # The duplicated prose preceding the marker is removed
        assert out.count(sentence) == 0
        assert "[[cite:c1:S1|f.txt]]" in out

    def test_strips_with_setup_phrase(self):
        sentence = "She had the maid of honour title bestowed by the Empress herself"
        citations = {
            "c1:S1": {
                "chunk_id": "c1",
                "sentence_refs": "S1",
                "label": "f.txt",
                "sentence_text": sentence,
            }
        }
        content = f'It reads: "{sentence}" [[cite:c1:S1|f.txt]]'
        out = strip_duplicated_citation_text(content, citations)
        assert sentence not in out
        assert "[[cite:c1:S1|f.txt]]" in out

    def test_short_sentence_text_skipped(self):
        citations = {
            "c1:S1": {
                "chunk_id": "c1",
                "sentence_refs": "S1",
                "label": "f.txt",
                "sentence_text": "too short",  # < 30 chars
            }
        }
        content = "too short [[cite:c1:S1|f.txt]]"
        out = strip_duplicated_citation_text(content, citations)
        assert "too short" in out

    def test_no_overlap_left_intact(self):
        sentence = "A long sentence text that is definitely thirty plus characters yes"
        citations = {
            "c1:S1": {
                "chunk_id": "c1",
                "sentence_refs": "S1",
                "label": "f.txt",
                "sentence_text": sentence,
            }
        }
        content = "Entirely different prose nowhere near it [[cite:c1:S1|f.txt]]"
        out = strip_duplicated_citation_text(content, citations)
        assert "Entirely different prose" in out

    def test_empty_citations_passthrough(self):
        content = "anything [[cite:c1:S1|f.txt]]"
        assert strip_duplicated_citation_text(content, {}) == content


# ---------------------------------------------------------------------------
# extract_chunk_citations
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cortex
class TestExtractChunkCitations:
    def test_keyed_by_chunk_and_sentence(self):
        content = "[[cite:abc:S1|a.txt]] and [[cite:abc:S3|a.txt]] and [[cite:def#S2]]"
        out = extract_chunk_citations(content)
        assert "abc:S1" in out
        assert "abc:S3" in out  # same chunk, different sentence -> separate entry
        assert "def:S2" in out
        assert out["abc:S1"]["label"] == "a.txt"
        assert out["def:S2"]["label"] == ""  # optional label omitted

    def test_no_citations_empty(self):
        assert extract_chunk_citations("plain text") == {}

    def test_multi_sentence_refs(self):
        out = extract_chunk_citations("[[cite:c0:S1,S2|f]]")
        assert "c0:S1,S2" in out
        assert out["c0:S1,S2"]["sentence_refs"] == "S1,S2"


# ---------------------------------------------------------------------------
# _resolve_sentence_text
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cortex
class TestResolveSentenceText:
    def test_single_sentence(self):
        content = "First sentence. Second sentence."
        offsets = [{"start": 0, "end": 15}, {"start": 16, "end": 32}]
        assert _resolve_sentence_text(content, offsets, "S1") == "First sentence."

    def test_joins_multiple(self):
        content = "First sentence. Second sentence."
        offsets = [{"start": 0, "end": 15}, {"start": 16, "end": 32}]
        out = _resolve_sentence_text(content, offsets, "S1,S2")
        assert out == "First sentence. Second sentence."

    def test_out_of_bounds_tolerated(self):
        content = "Only one."
        offsets = [{"start": 0, "end": 9}]
        # S1 in-bounds, S5 out-of-bounds -> only S1 resolved
        assert _resolve_sentence_text(content, offsets, "S1,S5") == "Only one."

    def test_all_out_of_bounds_returns_none(self):
        content = "Only one."
        offsets = [{"start": 0, "end": 9}]
        assert _resolve_sentence_text(content, offsets, "S5") is None


# ---------------------------------------------------------------------------
# enrich_chunk_citations_from_tool_results
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.cortex
class TestEnrichChunkCitations:
    def test_resolves_sentence_text_and_metadata(self):
        citations = {"c1:S1": {"chunk_id": "c1", "sentence_refs": "S1", "label": "x.txt"}}
        chunk = _chunk(
            "c1",
            "First sentence. Second sentence.",
            sentence_offsets=[{"start": 0, "end": 15}, {"start": 16, "end": 32}],
            source_id="src-9",
            page_number=4,
        )
        out = enrich_chunk_citations_from_tool_results(citations, _wrap({"chunks": [chunk]}))
        assert out["c1:S1"]["sentence_text"] == "First sentence."
        assert out["c1:S1"]["source_id"] == "src-9"
        assert out["c1:S1"]["page_number"] == 4

    def test_vision_flag_set(self):
        citations = {"c1:S1": {"chunk_id": "c1", "sentence_refs": "S1", "label": "x"}}
        chunk = _chunk(
            "c1",
            "irrelevant",
            content="[Visual Content] a chart",
            source_id="src-1",
        )
        out = enrich_chunk_citations_from_tool_results(citations, _wrap({"chunks": [chunk]}))
        assert out["c1:S1"]["has_vision_image"] is True

    def test_label_fallback_from_filename(self):
        citations = {"c1:S1": {"chunk_id": "c1", "sentence_refs": "S1", "label": ""}}
        chunk = _chunk("c1", "body", filename="report.pdf")
        out = enrich_chunk_citations_from_tool_results(citations, _wrap({"chunks": [chunk]}))
        assert out["c1:S1"]["label"] == "report.pdf"

    def test_missing_chunk_skipped(self):
        citations = {"ghost:S1": {"chunk_id": "ghost", "sentence_refs": "S1", "label": "g"}}
        chunk = _chunk("c1", "body")
        out = enrich_chunk_citations_from_tool_results(citations, _wrap({"chunks": [chunk]}))
        assert "sentence_text" not in out["ghost:S1"]

    def test_no_offsets_no_sentence_text(self):
        citations = {"c1:S1": {"chunk_id": "c1", "sentence_refs": "S1", "label": "x"}}
        chunk = _chunk("c1", "body with no offsets")  # no sentence_offsets
        out = enrich_chunk_citations_from_tool_results(citations, _wrap({"chunks": [chunk]}))
        assert "sentence_text" not in out["c1:S1"]

    def test_empty_inputs_passthrough(self):
        assert enrich_chunk_citations_from_tool_results({}, _wrap({"chunks": []})) == {}
        cites = {"c1:S1": {"chunk_id": "c1", "sentence_refs": "S1", "label": "x"}}
        assert enrich_chunk_citations_from_tool_results(cites, []) == cites
