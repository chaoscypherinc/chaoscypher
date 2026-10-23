# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphRAG Tool Handlers.

Implements the full GraphRAG retrieval pipeline: embed query, match seed
entities via vector search, run Personalized PageRank, assemble graph
context, retrieve provenance, vector and evidence chunks (the chunks the
graph block cites, ``graphrag.evidence_chunks``), then merge via RRF with the
evidence chunks in the budget by right and a reserved share of it for the
hybrid arm's best hits (``graphrag.vector_reserve``). The hybrid arm alone is public as
:meth:`GraphRAGToolHandlers.hybrid_chunk_search`, for the benchmark's
chunks-only baseline.

Extracted as a standalone handler class for SRP compliance and
strategy-pattern delegation from ToolExecutorService.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.cache.call_site import call_site
from chaoscypher_core.graphrag_profiles import apply_shape_levers
from chaoscypher_core.ports.search import item_in_source_scope
from chaoscypher_core.question_shapes import named_mention_count, question_parts, question_tokens
from chaoscypher_core.services.graph.engine.algorithms import calculate_pagerank
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DEFAULT_NAME_CONVENTIONS,
    NameConventions,
    NodeStanding,
    TripleStanding,
    fold,
    identity_word,
    is_descriptor,
    name_form,
    node_standing,
    reference_key,
    triple_standing,
)
from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
    UNIVERSAL_VOCABULARY,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import SUBTYPE_PROPERTY
from chaoscypher_core.services.workflows.tools.engine.chunk_hydration import (
    assign_chunk_aliases,
    chunk_source_filenames,
    clean_chunk_metadata,
    format_chunk_content,
)
from chaoscypher_core.services.workflows.tools.engine.context_render import render_retrieved_context
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_blocks import (
    _edge_in_context,
    aggregate_blocks,
    annotate_markers,
    chain_node_ids,
    cited_chunk_ids,
    labelled_sentences,
    walk_blocks,
    walk_chains,
    walk_edge_ids,
    walk_pairs,
    walk_pairs_left_out,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.question_shape import (
    ChatCallback,
    QuestionShape,
    ShapeDecision,
    classify_by_rules,
    route,
)
from chaoscypher_core.utils.query_text import normalize_query
from chaoscypher_core.utils.retrieval_faults import (
    NEEDS_FULL_REINDEX_UNREADABLE,
    SCOPE_SHORT,
    collect_retrieval_faults,
    record_retrieval_fault,
)
from chaoscypher_core.utils.rrf import reciprocal_rank_fusion


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.index import IndexingProtocol
    from chaoscypher_core.ports.search import SearchRepositoryProtocol
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        RelationVocabulary,
    )
    from chaoscypher_core.settings import EngineSettings, GraphRAGSettings

logger = structlog.get_logger(__name__)


_ACTIVE_LEVERS: ContextVar[GraphRAGSettings | None] = ContextVar(
    "graphrag_active_levers", default=None
)
"""The settings of the ``graphrag_search`` call in progress (its shape's levers), else None.

A context variable, so concurrent calls (one asyncio task each) keep their own
shape; read through :attr:`GraphRAGToolHandlers.levers`.
"""


_SUMMARY_MAX_CHARS = 4000
"""The triple summary's budget in characters (~1K tokens): whole lines, by rank (``_build_summary``)."""

_UNHYDRATED_ROW = "_unhydrated_row"
"""The key under which an unhydrated provenance chunk carries its stored row until the merge keeps it (``_hydrate_survivors``)."""


def _default_graphrag_settings() -> GraphRAGSettings:
    """Create default GraphRAG settings when none are provided.

    Returns:
        A ``GraphRAGSettings`` instance with all defaults.

    """
    from chaoscypher_core.settings import GraphRAGSettings

    return GraphRAGSettings()


@dataclass
class _DescriptionDrops:
    """The sentences the description cleaning dropped as repeats, summed over one call (``retrieval_stats.descriptions_dropped``).

    A mutable counter handed down from the graph-context assembly through
    :meth:`GraphRAGToolHandlers._entity_entries` and :func:`_node_description`
    to :func:`_clean_description`, which adds each sentence it drops: keep,
    flag, never delete, so a repeat dropped from a description is counted.
    """

    sentences: int = 0


def _node_description(
    node: Any,
    limit: int,
    *,
    clean: bool = False,
    query_terms: set[str] | None = None,
    conventions: NameConventions | None = None,
    dropped: _DescriptionDrops | None = None,
) -> str:
    """An entity's extracted description, as the commit stored it (definition, content, descriptors).

    ``clean`` (``GraphRAGSettings.clean_descriptions``) keeps whole sentences:
    one that repeats a kept sentence is dropped (a merged entity carries one
    description per mention; :func:`_clean_description`, counted on
    ``dropped`` when given), and the text ends at the last sentence that
    fits ``limit``; only a first sentence longer than ``limit`` is cut, at a
    word boundary.

    ``query_terms`` (the question's :func:`_significant_terms` under
    ``conventions``, ``GraphRAGSettings.question_aware_descriptions``) put
    the kept sentences sharing a term first, before the cut
    (:func:`_question_first`). Only with ``clean``: without it the text is a
    raw cut of the stored text, where sentence order is not defined, and the
    terms are ignored. Without terms (or ``conventions``) the description is
    the one before the lever, byte for byte.
    """
    text = _stored_description(node)
    if not text:
        return ""
    if clean:
        return _clean_description(
            text, limit, query_terms=query_terms, conventions=conventions, dropped=dropped
        )
    return text[: limit - 1].rstrip() + "…" if len(text) > limit else text


def _stored_description(node: Any) -> str:
    """The entity's whole stored description (definition, content or descriptors), whitespace-folded.

    What :func:`_node_description` renders from, before any cleaning or cut;
    empty for the commit's ``Entity: <name>`` placeholder.
    """
    props = getattr(node, "properties", None) or {}
    text = props.get("definition") or props.get("content") or ""
    if not text:
        descriptors = props.get("descriptors") or []
        text = "; ".join(str(d) for d in descriptors[:3]) if isinstance(descriptors, list) else ""
    text = " ".join(str(text).split())
    if text.startswith("Entity: "):  # the commit's placeholder, not a description
        return ""
    return text


def _stored_text(node: Any) -> str:
    """The label and whole stored description of a node: what the question gate matches on.

    Never the rendered line: ``clean_descriptions``, ``entity_description_chars``
    and ``entity_descriptions`` change how an entity is shown, not whether it
    is retrieved. Until 2026-10-06 the gate read the rendered description, so
    ``clean_descriptions`` changed the related entities on 22 of 120 questions
    (audit R6).
    """
    if node is None:
        return ""
    return f"{getattr(node, 'label', '') or ''} {_stored_description(node)}"


def _folded_titles(conventions: NameConventions) -> frozenset[str]:
    """The conventions' title words in folded spelling (``abbé`` -> ``abbe``)."""
    return _FOLDED_TITLES.setdefault(conventions, frozenset(fold(t) for t in conventions.titles))


_FOLDED_TITLES: dict[NameConventions, frozenset[str]] = {}
""":func:`_folded_titles`' memo: the conventions are hashable and few (one per domain)."""


def _singular(word: str, conventions: NameConventions) -> str:
    """A folded word without its plural ending: the matcher's plural fold.

    ``rostovs`` -> ``rostov``, ``kuragins`` -> ``kuragin``, ``brothers`` ->
    ``brother``, ``families`` -> ``family``, ``countesses`` -> ``countess``.
    A word ending in ``ss`` keeps it, and one ending in ``us`` or ``is``
    keeps its ``s`` unless what is left is one of the conventions' name forms
    (``bolkonskis`` -> ``bolkonski``; ``boris`` stays). Words under four
    letters are left alone. Both sides of every comparison go through it,
    so a name and its plural are one word.
    """
    if len(word) < 4 or not word.endswith("s") or word.endswith("ss"):
        return word
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    stem = word[:-1]
    if word.endswith(("us", "is")) and name_form(stem, conventions) is None:
        return word
    return stem


def _word_key(word: str, conventions: NameConventions) -> str:
    """A folded word as the matcher compares it: singular, then its family stem.

    The one fold the keyword seeds, ``question_entities_first``, the question
    gate, the seed-mention ranking and the evidence markers share: the plural
    fold (:func:`_singular`) and the name rule's own name-form fold
    (:func:`~chaoscypher_core.services.sources.engine.deduplication.name_identity.identity_word`
    under the domain's conventions: ``rostova`` -> ``rostov``,
    ``dolokhova`` -> ``dolokhov``). A possessive was already dropped by
    :func:`~chaoscypher_core.services.sources.engine.deduplication.name_identity.fold`.
    """
    return identity_word(_singular(word, conventions), conventions)


def _name_key_list(name: str, conventions: NameConventions) -> list[str]:
    """The naming words of ``name`` as match keys, in order: titles and articles left out.

    The name rule's naming words (as
    :func:`~chaoscypher_core.services.sources.engine.deduplication.name_identity.name_words`
    reads them: the domain's title words left out), each through
    :func:`_word_key`, first occurrence first; a word whose singular is a
    title (``princes``) is a title too. Empty for a name
    of titles alone (``the prince``, ``the Emperor``, ``Dowager Empress``):
    such a name names no one. Empty too for a descriptor (the name rule's
    :func:`~chaoscypher_core.services.sources.engine.deduplication.name_identity.is_descriptor`:
    ``Bezúkhov's son``, ``the mother of Natásha``): it names the bearer by a
    relation to someone else, so its words are that other person's name.
    Until 2026-10-06 the alias ``Bezúkhov's son`` made Pierre a keyword seed
    of every question naming the old count (audit wave 4).
    """
    if is_descriptor(name, conventions):
        return []
    titles = _folded_titles(conventions)
    out: list[str] = []
    for word in fold(name).split():
        if word in titles or _singular(word, conventions) in titles:
            continue
        key = _word_key(word, conventions)
        if key not in out:
            out.append(key)
    return out


def _text_keys(text: str, conventions: NameConventions) -> set[str]:
    """Every word of ``text`` as a match key (:func:`_word_key`), titles kept.

    What the question gate, the seed-mention ranking and the evidence
    markers look names and terms up in: whole words, never substrings.
    """
    return {_word_key(w, conventions) for w in fold(text).split()}


def _names_a_term(node: Any, terms: set[str], conventions: NameConventions) -> bool:
    """Whether a naming word of the node's label or of any stored alias is one of ``terms``.

    ``terms`` come from :func:`_naming_terms` under the same conventions.
    Whole words, both sides through :func:`_name_key_list` /
    :func:`_word_key`: titles left out (the domain's title words), accents,
    possessives and plurals folded, surname forms folded to their family
    stem, so "the Rostóv family" names ``Rostóvs`` and ``Countess Rostóva``,
    and "Dólokhov" names ``Márya Ivánovna Dólokhova``.

    A name of titles alone (``the prince``, ``the little princess``) has no
    naming word and never matches. Until 2026-10-06 it matched as a whole
    phrase of the question, so the v3 graph's node ``the prince`` seeded 30
    of 120 benchmark questions at weight 1.0, ahead of every vector seed,
    on the word "prince" alone (audit wave 3); and before that the test was a
    substring over the label ("prince" in "princess", audit wave 2).

    A flagged reference (:func:`_seedable`) names no one, whatever its label
    or aliases hold: ``the count`` stores the alias ``Papa``, which is his
    children's word for him, not his name.
    """
    if node is None or not terms or not _seedable(node):
        return False
    return any(
        terms.intersection(_name_key_list(name, conventions))
        for name in (getattr(node, "label", "") or "", *_node_aliases(node, limit=None))
    )


def _seedable(node: Any) -> bool:
    """Whether ``node`` is a named node, which any seed match may match; False for a flagged reference.

    The one decision the vector seeds (:func:`_similarity_seeds`), the
    keyword seeds and ``question_entities_first`` (:func:`_names_a_term`)
    read; the entity and provenance ranking read the node's whole standing
    (:func:`_by_standing`), the reference flag and the orphan flag. A
    flagged orphan (``properties.orphan``: no edge when it was committed) is
    seedable: it seeds by name, written label or vector like any named node. A reference is a node the
    extraction flagged ``descriptor_reference``
    (:func:`~chaoscypher_core.services.sources.engine.deduplication.name_identity.is_reference_record`):
    a bare title, a role or a descriptor no name of its own identifies
    (``the count``, ``the little princess``, ``Prince Vasíli's son``). Its
    words name someone else or no one, so its label never name-matches: on
    the B0 literary pack the vector seeds took ``Prince Vasíli's son`` for
    questions naming Prince Vasíli (25 of 120) and ``the count`` for 31, and
    the anonymous references, one node across many chunks, then outranked in
    PageRank the named person asked about (2026-10-07). It seeds when the
    question writes its whole label (:func:`_writes_reference`) or when it
    is the question's closest match (:func:`_similarity_seeds`), and the
    graph walk still reaches it from a named seed. A node the graph does not
    return (None) is seedable: PageRank drops an id that is no node of its
    graph.
    """
    return node is None or not node_standing(getattr(node, "properties", None)).reference


def _similarity_seeds(
    candidates: list[tuple[str, float]], nodes: dict[str, Any]
) -> list[tuple[str, float]]:
    """The vector candidates that may seed: every named one, a flagged reference only when no named one is closer.

    ``candidates`` are the vector match's ``(node_id, similarity)`` above
    the threshold, ``nodes`` their graph nodes by id; the order is kept. A
    reference (:func:`_seedable`) seeds by similarity when its similarity is
    at least the best named candidate's: the question then asks about it by
    meaning ("Princess Mary's father" is ``Prince Andrew's father``, whose
    description alone holds what his son said of women: B0 q088). A
    reference a named node outscores is a loose likeness, and dropping it
    before the cut gives the slot to a name (``the little princess`` second
    to Kutúzov for "the little princess and General Kutúzov": B0 q099, where
    the named Lise then seeds). Until 2026-10-07 every reference above the
    threshold seeded (84 of 120 B0 questions); then none did, which lost the
    references whose facts no named node carries (q088 on four models). The
    offline measure (answer-term recall in the context, both B0 v4b packs):
    this rule recovers q088 and loses nothing; letting a reference within
    0.02 to 0.1 of the best name, or among the top three, seed as well
    recovers nothing more.
    """
    best = max((score for nid, score in candidates if _seedable(nodes.get(nid))), default=None)
    return [
        (nid, score)
        for nid, score in candidates
        if best is None or score >= best or _seedable(nodes.get(nid))
    ]


def _phrase_words(text: str, conventions: NameConventions) -> list[tuple[str, bool]]:
    """``(word, joins a name)`` per folded word of ``text``: how a written label is compared.

    Each word as :func:`fold` reads it (accents, possessives), its title in
    the one spelling (``title_class``); the words of a token as the
    question's one tokenisation reads it
    (:func:`~chaoscypher_core.question_shapes.question_tokens`), on both
    sides, so a label and a question split alike. ``joins a name`` is True
    for a token written capitalised right after the one before it (only
    space between): ``Rostóva`` in "the Countess Rostóva", never ``Who``
    after "the count. ".
    """
    out: list[tuple[str, bool]] = []
    after_word = False  # the token before this one ends with only space between them
    for token in question_tokens(text):
        if token is None:
            after_word = False
            continue
        joins = after_word and token[:1].isupper()
        for i, word in enumerate(fold(token).split()):
            out.append((conventions.title_class.get(word, word), joins and i == 0))
        after_word = True
    return out


def _writes_reference(
    query_words: list[tuple[str, bool]], node: Any, conventions: NameConventions
) -> bool:
    """Whether the question writes a flagged reference's whole label (``the little princess``).

    The label must be a reference by the name rule's reading
    (:func:`~chaoscypher_core.services.sources.engine.deduplication.name_identity.reference_key`)
    and its words (:func:`_phrase_words`, articles kept) consecutive words of
    the question's (``query_words``, the same reading), not followed by a
    name written on to them: "the little princess" writes ``the little
    princess`` and not ``the princess``, "the countess" does not write ``the
    count``, "the count's daughter" writes ``the count`` (the fold drops the
    possessive), and "the Countess Rostóva" names Countess Rostóva, not ``the
    countess`` (on the B0 pack it seeded that reference on five questions
    about her). The label only, never an alias: a reference's alias is no
    name either (:func:`_names_a_term`).
    """
    if node is None or _seedable(node):
        return False
    label = getattr(node, "label", "") or ""
    if reference_key(label, conventions, getattr(node, "entity_type", None)) is None:
        return False
    words = [w for w, _ in _phrase_words(label, conventions)]
    n = len(words)
    asked = [w for w, _ in query_words]
    return any(
        asked[i : i + n] == words and not (i + n < len(query_words) and query_words[i + n][1])
        for i in range(len(asked) - n + 1)
    )


_CONNECTED_NAME = NodeStanding(reference=False, orphan=False)
"""The standing of a node no pipeline flag marks (``name_identity.node_standing``): it ranks first."""


def _entry_standing(node: Any) -> dict[str, Any]:
    """The ``"standing"`` an entity entry carries for ``node``: nothing for a connected named node.

    ``name_identity.node_standing`` of its stored properties, the one
    reading of the pipeline flags (reference, orphan) the ranking sorts by
    (:func:`_by_standing`).
    """
    standing = node_standing(getattr(node, "properties", None))
    return {} if standing == _CONNECTED_NAME else {"standing": standing}


def _by_standing(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Entity entries by standing, order kept within each: named before reference, connected before orphan.

    The one ordering the entity lines and the provenance lookup
    (:func:`_provenance_lookup_ids`) apply: a flagged reference after every
    named entity (2026-10-07: ``the princess`` holds the mentions of three
    princesses), and a flagged orphan (``properties.orphan``) after every
    connected entity of its standing (keep, flag, never delete, 2026-10-07:
    the commit now keeps an edgeless entity that carries evidence, where it
    used to delete it; its description is what the text says, but no edge
    places it, so a connected node the walk reached says more about the
    question).
    """
    return sorted(entries, key=lambda e: e.get("standing", _CONNECTED_NAME))


def _line_standing(edge: Any, nodes_map: Mapping[str, Any]) -> TripleStanding:
    """One relationship line's standing (``name_identity.triple_standing``): the one reading of it.

    The edge's own flags and its endpoints'
    :func:`~chaoscypher_core.services.sources.engine.deduplication.name_identity.node_standing`
    (an endpoint not in ``nodes_map`` reads as unflagged). The one ranking
    of the relationship lines: the ``max_triples`` selection sorts by it
    before the cap (:meth:`GraphRAGToolHandlers._select_triples`), and the
    triple summary's budget cuts the tail of that order
    (:meth:`GraphRAGToolHandlers._build_summary`), so both agree.
    """
    src, tgt = nodes_map.get(edge.source_node_id), nodes_map.get(edge.target_node_id)
    return triple_standing(
        getattr(edge, "properties", None),
        node_standing(getattr(src, "properties", None)),
        node_standing(getattr(tgt, "properties", None)),
    )


def _is_graph_node_id(item_id: str) -> bool:
    """Whether a search hit's id names a graph node (not a ``chunk:`` or ``template:`` hit)."""
    return not str(item_id).startswith(("chunk:", "template:"))


_SENTENCE_END = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z\"\u201c\u2018(])"
)  # a capital or an opening quote follows


def _content_words(sentence: str) -> set[str]:
    """A sentence's words of three letters or more, lowercased, function words (:data:`_STOP`) left out."""
    return {w for w in re.findall(r"\w{3,}", sentence.lower()) if w not in _STOP}


def _clean_description(
    text: str,
    limit: int,
    *,
    query_terms: set[str] | None = None,
    conventions: NameConventions | None = None,
    dropped: _DescriptionDrops | None = None,
) -> str:
    """Whole, distinct sentences of ``text`` up to ``limit`` characters (see :func:`_node_description`).

    Three passes: the repeated sentences dropped, the question's sentences
    first when ``query_terms`` and ``conventions`` are given
    (:func:`_question_first`), then the length cut.

    A sentence is a repeat when its content words (:func:`_content_words`)
    are all among one kept sentence's: "He is the father of Nicholas."
    states a fact "He is the father of Pierre." does not, and both stay.
    Until 2026-10-09 a 60% overlap of all its words, function words
    included, dropped it (125 distinct sentences on 46 of the v5d literary
    graph's 209 nodes). Each drop is counted on ``dropped`` when given.
    """
    kept: list[str] = []
    kept_words: list[set[str]] = []
    for sentence in _SENTENCE_END.split(text):
        words = _content_words(sentence)
        if any(words <= other for other in kept_words):
            if dropped is not None:
                dropped.sentences += 1
            continue
        kept.append(sentence)
        kept_words.append(words)
    if query_terms and conventions is not None:
        kept = _question_first(kept, query_terms, conventions)
    out = ""
    for sentence in kept:
        candidate = f"{out} {sentence}".strip()
        if len(candidate) > limit:
            break
        out = candidate
    if not out and kept:  # the first sentence alone is longer than the limit
        first = kept[0][: limit - 1]
        out = (first.rsplit(" ", 1)[0] if " " in first else first).rstrip(",;:") + "…"
    return out


def _question_first(
    sentences: list[str], query_terms: set[str], conventions: NameConventions
) -> list[str]:
    """The sentences sharing a word with the question (:func:`_shares_a_term`) first, stably.

    ``GraphRAGSettings.question_aware_descriptions``: the sharing sentences
    keep their stored order, and so do the rest after them, so a cast
    question sees the sentence naming the children before the one about
    temperament once the cut keeps only the first.
    """
    return sorted(sentences, key=lambda s: not _shares_a_term(s, query_terms, conventions))


def _node_aliases(node: Any, limit: int | None = 4) -> list[str]:
    """The node's stored aliases that differ from its label, at most ``limit`` (None: all)."""
    props = getattr(node, "properties", None) or {}
    aliases = props.get("aliases")
    if not isinstance(aliases, list):
        return []
    label = (getattr(node, "label", "") or "").casefold()
    out: list[str] = []
    for alias in aliases:
        a = str(alias).strip()
        if a and a.casefold() != label and a.casefold() not in {o.casefold() for o in out}:
            out.append(a)
    return out[:limit]


def _name_keys(label: str, conventions: NameConventions) -> set[str]:
    """The distinctive words of an entity label, as match keys (``General Kutúzov`` -> ``kutuzov``).

    The matcher's naming words (:func:`_name_key_list`, the domain's titles
    left out); a label of titles alone keeps its long words, so a relation
    of ``the prince`` can still point at a sentence about a prince (an
    evidence marker is a pointer for the reader, never a seed).
    """
    keys = set(_name_key_list(label, conventions))
    return keys or {_word_key(w, conventions) for w in fold(label).split() if len(w) >= 4}


def _chunk_sentences(
    chunks: list[Any], conventions: NameConventions
) -> list[tuple[str, int, set[str]]]:
    """``(chunk label, sentence number, word keys)`` for every labelled sentence of the chunks."""
    out: list[tuple[str, int, set[str]]] = []
    for chunk in chunks:
        parsed = labelled_sentences(chunk)
        if parsed is None:
            continue
        label, sentences = parsed
        out.extend((label, num, _text_keys(text, conventions)) for num, text in sentences)
    return out


def _evidence(
    source: str,
    target: str,
    sentences: list[tuple[str, int, set[str]]],
    conventions: NameConventions,
) -> str | None:
    """``C3:S2`` for the first retrieved sentence naming both entities (whole words), or None."""
    a, b = _name_keys(source, conventions), _name_keys(target, conventions)
    if not a or not b:
        return None
    for label, num, words in sentences:
        if a & words and b & words:
            return f"{label}:S{num}"
    return None


def _annotate_evidence(result: dict[str, Any], conventions: NameConventions) -> None:
    """Set ``evidence`` on each relationship and path step a retrieved sentence names both ends of."""
    sentences = _chunk_sentences(result.get("chunks") or [], conventions)
    if not sentences:
        return
    gc = result.get("graph_context") or {}
    for rel in gc.get("relationships") or []:
        ref = _evidence(rel.get("source", ""), rel.get("target", ""), sentences, conventions)
        if ref:
            rel["evidence"] = ref
    for path in gc.get("paths") or []:
        for step in path.get("steps") or []:
            ref = _evidence(step.get("source", ""), step.get("target", ""), sentences, conventions)
            if ref:
                step["evidence"] = ref


HYBRID_MODE = "hybrid"
"""The ``retrieval_stats["mode"]`` of :meth:`GraphRAGToolHandlers.hybrid_retrieval`."""

_SOURCE_PAGE = 1000
"""Source rows read for the database's domain (:attr:`GraphRAGToolHandlers.name_conventions`)."""

HYBRID_OVERFETCH = 2
"""The hybrid arm asks the search for ``limit * HYBRID_OVERFETCH`` chunks per arm (``k``)."""

HYBRID_MIN_SIMILARITY = 0.3
"""The hybrid arm's vector half keeps chunks of at least this cosine similarity."""


def _interleave_hits(hit_lists: Sequence[Sequence[tuple[str, float]]]) -> list[tuple[str, float]]:
    """Round-robin over the lists, each id once where it first appears: every list's first hit, then every list's second, ...

    The hybrid arm's order is its rank (rank fusion and the hybrid reserve
    read the ranks alone), so a two-part question's lists are merged by rank
    rather than by score: the whole question's top hit, then each part's top
    hit, then the seconds. One list is returned as it came.
    """
    seen: set[str] = set()
    merged: list[tuple[str, float]] = []
    for rank in range(max((len(hits) for hits in hit_lists), default=0)):
        for hits in hit_lists:
            if rank < len(hits) and hits[rank][0] not in seen:
                seen.add(hits[rank][0])
                merged.append((hits[rank][0], hits[rank][1]))
    return merged


GENERIC_RELATIONS = frozenset(
    {"interacts_with", "related_to", "references", "associated_with", "mentions"}
)
"""Relations that say little: the extraction prompt's catch-alls (it asks for spouse_of over
related_to). ``GraphRAGSettings.generic_relations_last`` ranks them after the specific ones."""

SYMMETRIC_GENERIC_RELATIONS = frozenset({"interacts_with", "related_to", "associated_with"})
"""Generic relations whose meaning has no direction: A interacts_with B is B interacts_with A.
``GraphRAGSettings.dedupe_graph_lines`` keeps one line of such a pair; a directed relation
stated both ways (A courts B, B courts A) is two facts and both lines stay."""


def _in_scope(by_node: Mapping[str, list[Any]], in_scope: set[str]) -> dict[str, list[Any]]:
    """Each node's edges with both ends in ``in_scope`` (a chat's source scope, audit wave 4)."""
    return {
        nid: [e for e in edges if e.source_node_id in in_scope and e.target_node_id in in_scope]
        for nid, edges in by_node.items()
    }


def _connected_edge(src: str, tgt: str, top_ids: set[str], seed_ids: set[str]) -> bool:
    """The ``connected`` triple filter: an edge between two top entities, or touching a seed."""
    return (src in top_ids and tgt in top_ids) or src in seed_ids or tgt in seed_ids


def _seed_paths(
    seed_ids: list[str], edges: list[Any], label_map: dict[str, str], max_paths: int
) -> list[dict[str, Any]]:
    """Relation chains of one or two hops between distinct seed entities, over the fetched edges.

    Each path is ``{"steps": [{"source", "label", "target"}, ...]}`` in the
    edges' own direction; one-hop chains first, then seed order.
    """
    adjacency: dict[str, list[Any]] = {}
    for e in edges:
        adjacency.setdefault(e.source_node_id, []).append(e)
        adjacency.setdefault(e.target_node_id, []).append(e)
    pairs = [(a, b) for i, a in enumerate(seed_ids) for b in seed_ids[i + 1 :]]
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for hops in (1, 2):
        for a, b in pairs:
            for chain in _chains(a, b, adjacency, hops):
                key = tuple(x for e in chain for x in (e.source_node_id, e.label, e.target_node_id))
                if key in seen:
                    continue
                seen.add(key)
                found.append({"steps": [_step(e, label_map) for e in chain]})
                if len(found) >= max_paths:
                    return found
    return found


def _step(e: Any, label_map: dict[str, str]) -> dict[str, str]:
    """One path step as ``{source, label, target}`` with node ids replaced by labels."""
    return {
        "source": label_map.get(e.source_node_id, e.source_node_id),
        "label": e.label,
        "target": label_map.get(e.target_node_id, e.target_node_id),
    }


def _other(e: Any, nid: str) -> str:
    """The edge's endpoint that is not ``nid``."""
    return str(e.target_node_id if e.source_node_id == nid else e.source_node_id)


def _chains(a: str, b: str, adjacency: dict[str, list[Any]], hops: int) -> list[list[Any]]:
    """The edge chains of exactly ``hops`` (1 or 2) from ``a`` to ``b``."""
    chains: list[list[Any]] = []
    for e1 in adjacency.get(a, []):
        mid = _other(e1, a)
        if hops == 1:
            if mid == b:
                chains.append([e1])
            continue
        if mid in (a, b):
            continue
        chains.extend([e1, e2] for e2 in adjacency.get(mid, []) if _other(e2, mid) == b)
    return chains


_STOP = frozenset(
    {
        "what",
        "who",
        "whom",
        "whose",
        "which",
        "where",
        "when",
        "does",
        "did",
        "the",
        "and",
        "that",
        "this",
        "with",
        "from",
        "into",
        "about",
        "between",
        "among",
        "have",
        "has",
        "had",
        "was",
        "were",
        "are",
        "been",
        "being",
        "their",
        "there",
        "they",
        "them",
        "than",
        "then",
        "both",
        "each",
        "only",
        "also",
        "some",
        "such",
        "very",
        "more",
        "most",
        "over",
        "under",
        "after",
        "before",
        "while",
        "during",
        "against",
        "through",
        "name",
        "named",
        "called",
        "text",
        "story",
        # short words a question may capitalise (a sentence's first word): a
        # short query word counts only when written as a name (Ann, Bob)
        "a",
        "an",
        "i",
        "is",
        "it",
        "in",
        "on",
        "at",
        "to",
        "of",
        "or",
        "as",
        "by",
        "be",
        "do",
        "he",
        "we",
        "me",
        "my",
        "no",
        "so",
        "up",
        "us",
        "if",
        "how",
        "why",
        "his",
        "her",
        "its",
        "him",
        "she",
        "for",
        "but",
        "not",
        "nor",
        "yet",
        "you",
        "our",
        "any",
        "all",
        "can",
        "may",
        "one",
        "two",
    }
)


def _is_query_word(word: str, token: str) -> bool:
    """Whether a folded ``word`` of the question's ``token`` can carry a name or a topic (see :func:`_query_words`)."""
    if word in _STOP or not word.isalpha():
        return False
    return len(word) >= 4 or token[:1].isupper()


def _query_words(query: str) -> list[str]:
    """The query's folded words that can carry a name or a topic, in order.

    Function words (:data:`_STOP`) and non-alphabetic words are left out; a
    word of four letters or more is kept, and a shorter one only when the
    question writes it as a name (a capital first letter: ``Ann``, ``Bob``),
    the name rule's own test for a one-word name (:func:`_is_query_word`).
    Until 2026-10-06 every word under four letters was dropped, so a
    question about Ann or Bob seeded neither (audit wave 3). The tokens are
    the question's one tokenisation
    (:func:`~chaoscypher_core.question_shapes.question_tokens`, composed
    first: the token pattern reads a decomposed accent as a break, so NFD
    "Bezúkhov" was the words ``bezu`` and ``khov``, audit wave 5).
    """
    out: list[str] = []
    for token in question_tokens(query):
        if token is None:  # punctuation: no word
            continue
        for word in fold(token).split():
            if _is_query_word(word, token) and word not in out:
                out.append(word)
    return out


def _mention_words(query: str, conventions: NameConventions) -> list[str | None]:
    """The question's words in order as match keys, for the named-mention count; None where a mention breaks.

    The words :func:`_naming_terms` reads (:func:`_is_query_word`, as match
    keys), titles left out (a title neither names nor breaks: "Prince
    Andrew" is Andrew), and None for punctuation and for a word that can
    name no one (a function word), so
    :func:`~chaoscypher_core.question_shapes.named_mention_count` counts
    "Anna Pávlovna" once and "Pierre and Andrew" twice.
    """
    titles = _folded_titles(conventions)
    out: list[str | None] = []
    for token in question_tokens(query):
        if token is None:
            out.append(None)
            continue
        for word in fold(token).split():
            if word in titles or _singular(word, conventions) in titles:
                continue
            out.append(_word_key(word, conventions) if _is_query_word(word, token) else None)
    return out


def question_relations(query: str, vocabulary: RelationVocabulary) -> list[str]:
    """The relations the question's words name in ``vocabulary`` (``wife`` names ``spouse``), in query order, each once.

    Each word of :func:`_query_words` looked up in the vocabulary's
    ``word_relation``. The one reading of a question's relation words: the
    handler's (:attr:`GraphRAGToolHandlers.relation_vocabulary`: the
    aggregate block's families and the shape rules' relational lookup), and
    the fixture rules test's and ``router_check.py``'s (the universal
    vocabulary, no stack).
    """
    words = vocabulary.word_relation
    relations: list[str] = []
    for word in _query_words(query):
        relation = words.get(word)
        if relation is not None and relation not in relations:
            relations.append(relation)
    return relations


def _significant_terms(query: str, conventions: NameConventions) -> set[str]:
    """The query's words (:func:`_query_words`) as match keys (:func:`_word_key`), titles kept.

    What the question gate matches, word for word, on stored text: a topic
    word, titles included ("prince" keeps an entity described as "the
    daughter of a prince"; "brothers" keeps one described as "her
    brother"). Folded by the name rule's :func:`fold`, so a possessive is its
    word (``Andrew's`` -> ``andrew``).
    """
    return {_word_key(w, conventions) for w in _query_words(query)}


def _naming_terms(query: str, conventions: NameConventions) -> set[str]:
    """The query's words that can name someone: :func:`_significant_terms` without the titles.

    What a keyword seed and ``question_entities_first`` match against an
    entity's naming words (:func:`_names_a_term`): "Who did Prince Vasíli's
    daughter marry?" names ``vasili``, never ``prince``. The titles are the
    domain's (the ones its deduplication read names by).
    """
    titles = _folded_titles(conventions)
    return {
        _word_key(w, conventions)
        for w in _query_words(query)
        if w not in titles and _singular(w, conventions) not in titles
    }


def _shares_a_term(text: str, terms: set[str], conventions: NameConventions) -> bool:
    """Whether a word of ``text`` is one of ``terms``, both as match keys (whole words).

    The question gate's test. Until 2026-10-06 it was a substring test
    ("father" in "grandfather", "count" in "country"); the plural and
    possessive fold (:func:`_word_key`, the matcher's own) keeps what the
    substring got right ("brother" and "brothers").
    """
    return bool(terms) and not terms.isdisjoint(_text_keys(text, conventions))


def _rank_by_seed_mentions(
    chunks: list[dict[str, Any]], seed_labels: list[str], conventions: NameConventions
) -> list[dict[str, Any]]:
    """Stable order by how many seed names (whole) and surnames a chunk's text mentions.

    A seed counts once when every naming word of its label is a word of the
    chunk, and once more for its surname (the last naming word of a label of
    two or more) alone; both through the matcher's fold (:func:`_name_key_list`,
    :func:`_text_keys`). A label of titles alone (``the prince``) names no
    one and counts nothing. Until 2026-10-06 the label was a substring of the
    chunk text, so ``the prince`` ranked every chunk mentioning a prince
    first (audit wave 3).
    """
    names = [k for k in (_name_key_list(label, conventions) for label in seed_labels if label) if k]
    surnames = [k[-1] for k in names if len(k) > 1]

    def mentions(chunk: dict[str, Any]) -> int:
        """How many seed names or surnames the chunk's words hold."""
        words = _text_keys(str(chunk.get("text") or chunk.get("content") or ""), conventions)
        return sum(1 for k in names if words.issuperset(k)) + sum(1 for s in surnames if s in words)

    return sorted(chunks, key=lambda c: -mentions(c))


def _note_shape_reason(stats: dict[str, Any] | None, note: str) -> None:
    """Append ``note`` to ``stats["shape_reason"]`` (``"; "``-joined): how the call's assembly departed from its shape.

    The one wording of a shape the call could not assemble as decided (a
    path question naming fewer than two entities, an aggregate naming none,
    a global question before the community block): the reason keeps the
    router's own words first. No stats, nothing to note.
    """
    if stats is None:
        return
    reason = stats.get("shape_reason") or ""
    stats["shape_reason"] = f"{reason}; {note}" if reason else note


def _ppr_ranked(ppr_scores: dict[str, float]) -> list[str]:
    """Node ids by PageRank score, highest first; equal scores by node id.

    The tie order is the handler's own, never the order the graph listing
    returned the nodes in: the repository's minimal listings carry no
    ``ORDER BY``, so the dict's insertion order is SQLite's row order
    (audit wave 4).
    """
    return sorted(ppr_scores, key=lambda nid: (-ppr_scores[nid], nid))


def _reserved_hybrid_ids(hybrid_order: Sequence[str], reserve: int, budget: int) -> list[str]:
    """The hybrid arm's chunk ids the reserve keeps in a budget: its top ``min(reserve, budget)``.

    The one cut of the reserve: :meth:`GraphRAGToolHandlers._reserve_hybrid_hits`
    protects these ids, the evidence arm spends no slot on them and the
    pipeline counts those that did not enter (``reserve_shortfall``).
    """
    return list(hybrid_order[: min(reserve, budget)])


def _budget_cut(
    merged: list[dict[str, Any]], limit: int, evidence_ids: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[str, ...]]:
    """The fused list cut to the budget with the evidence chunks inside it: ``(kept, rest, entered)``.

    The evidence chunks enter by right: up to ``limit`` of them (in fused
    order) are kept wherever fusion ranked them, and the other arms fill
    the remaining slots in fused order. Fusion interleaves the arms rank by
    rank, so a plain cut kept only the first few of six cited chunks under a
    budget of ten. Both lists keep the fused order; ``entered`` names the
    evidence chunks kept. With no evidence it is ``merged[:limit]`` and
    ``merged[limit:]``.
    """
    entered = tuple(
        [c["chunk_id"] for c in merged if c["chunk_id"] in evidence_ids][: max(limit, 0)]
    )
    room = max(limit, 0) - len(entered)
    others = [c["chunk_id"] for c in merged if c["chunk_id"] not in evidence_ids][: max(room, 0)]
    keep = set(entered) | set(others)
    kept = [c for c in merged if c["chunk_id"] in keep]
    rest = [c for c in merged if c["chunk_id"] not in keep]
    return kept, rest, entered


def _in_lookup_order[R](
    rows: list[R],
    ids: list[str],
    key: Callable[[R], Any],
    then: Callable[[R], Any] | None = None,
) -> list[R]:
    """``rows`` ordered by the position of ``key(row)`` in ``ids``; ties by ``then(row)``.

    Rows whose key is not in ``ids`` are dropped. A batch lookup (``IN
    (...)``) returns rows in whatever order SQLite's index walk gives, which
    is not the order asked for: the citation lookup returned its rows by
    ``entity_uri``, so the provenance arm the handler had ordered by
    PageRank reached rank fusion ordered by node id, and moved the chunk
    sets of 83 of 120 benchmark questions at ``vector_reserve`` 8 (118 at
    0; audit wave 4). Every batch lookup whose order feeds a rank goes
    through here; ``then`` breaks ties the repository's order would
    otherwise decide (several rows for one id).
    """
    position = {item_id: i for i, item_id in enumerate(ids)}
    kept = [row for row in rows if key(row) in position]
    if then is None:
        return sorted(kept, key=lambda row: position[key(row)])
    tiebreak = then
    return sorted(kept, key=lambda row: (position[key(row)], tiebreak(row)))


def _citation_tiebreak(citation: dict[str, Any]) -> tuple[float, str]:
    """Among one entity's citations: the most confident first, then by chunk id."""
    confidence = citation.get("confidence")
    return (-float(confidence) if confidence is not None else 0.0, str(citation.get("chunk_id")))


def _by_edge_id(edges: list[Any]) -> list[Any]:
    """Edges by id: the order the triples and paths break ties in, whatever the listing returned."""
    return sorted(edges, key=lambda e: str(getattr(e, "id", "")))


def _provenance_lookup_ids(
    graph_context: dict[str, Any], ppr_scores: dict[str, float]
) -> list[str]:
    """The kept related entities, then the kept seeds, each in PageRank order; flagged ones last.

    The order the provenance chunks are fetched in feeds rank fusion, so it
    follows PageRank, never the rendered order: ``question_entities_first``
    reorders the entity lines for the reader and must not change which
    chunks are retrieved (audit R6). Then by standing (:func:`_by_standing`,
    ``"standing"`` on the entry): a flagged reference's chunks come after
    every named entity's (``the princess`` holds the mentions of three
    princesses, so its citations are no evidence about the one asked for,
    2026-10-07), and a flagged orphan's after every connected entity's of its
    standing.
    """
    ranked = _ppr_ranked(ppr_scores)
    entries = {
        e["id"]: e
        for key in ("seed_entities", "related_entities")
        for e in graph_context.get(key, [])
    }
    related = {e["id"] for e in graph_context.get("related_entities", [])}
    seeds = {e["id"] for e in graph_context.get("seed_entities", [])}
    ordered = [nid for nid in ranked if nid in related] + [nid for nid in ranked if nid in seeds]
    return [e["id"] for e in _by_standing([entries[nid] for nid in ordered])]


def _paths_to_top(
    seed_ids: list[str],
    top_ids: list[str],
    edges: list[Any],
    label_map: dict[str, str],
    budget: int,
) -> list[dict[str, Any]]:
    """One-hop chains from each seed to the other top-ranked entities, in rank order."""
    if budget <= 0:
        return []
    top = set(top_ids)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for sid in seed_ids:
        for e in edges:
            if sid not in (e.source_node_id, e.target_node_id):
                continue
            if _other(e, sid) not in top:
                continue
            key = (e.source_node_id, e.label, e.target_node_id)
            if key in seen:
                continue
            seen.add(key)
            out.append({"steps": [_step(e, label_map)]})
            if len(out) >= budget:
                return out
    return out


def _as_query_embedding(callback: Callable[..., Any] | None) -> Callable[..., Any] | None:
    """``callback`` with its model calls counted under the cache's ``embeddings.query`` site."""
    if callback is None:
        return None
    embed = callback

    async def embed_query(*args: Any, **kwargs: Any) -> Any:
        """Embed the query through ``callback`` inside ``call_site("embeddings.query")``."""
        with call_site("embeddings.query"):
            return await embed(*args, **kwargs)

    return embed_query


def _rendered_subtype(node: Any) -> str | None:
    """A node's pack subtype as its context line shows it (``military_unit`` -> ``military unit``)."""
    properties = getattr(node, "properties", None)
    subtype = properties.get(SUBTYPE_PROPERTY) if isinstance(properties, dict) else None
    if not isinstance(subtype, str) or not subtype.strip():
        return None
    return " ".join(subtype.replace("_", " ").split())


class GraphRAGToolHandlers:
    """Handles the graphrag_search tool: graph-enhanced retrieval pipeline.

    Fuses Personalized PageRank over the knowledge graph with hybrid
    vector/keyword search and RRF merging to produce context that is both
    structurally and semantically relevant to the user query.
    """

    def __init__(
        self,
        graph_repository: GraphRepositoryProtocol,
        search_repository: SearchRepositoryProtocol,
        indexing_repository: IndexingProtocol | None = None,
        source_storage: Any | None = None,
        embedding_callback: Callable[..., Any] | None = None,
        settings: EngineSettings | None = None,
        database_name: str = "default",
        name_conventions: NameConventions | None = None,
        chat_callback: ChatCallback | None = None,
    ) -> None:
        """Initialize the instance.

        Args:
            graph_repository: Repository for graph node/edge operations.
            search_repository: Repository for keyword and vector search.
            indexing_repository: Optional repository for chunk hydration.
            source_storage: Optional source storage for citation lookups.
            embedding_callback: Async callback that embeds text and returns
                an object with an ``embedding`` attribute (or dict key).
            settings: Full engine settings (``settings.graphrag`` is used).
            database_name: Active database name for citation queries.
            name_conventions: The naming conventions the matcher reads names
                by; None reads them from the database's source rows on first
                use (:attr:`name_conventions`).
            chat_callback: The chat model the shape router asks when the rules
                abstain (``GraphRAGSettings.router``), counted under the
                cache's ``router`` call site; None routes by the rules alone.

        """
        self.graph = graph_repository
        self.search = search_repository
        self.indexing = indexing_repository
        self.source_storage = source_storage
        # every arm's query embedding (graphrag's and the hybrid arm's) counted as a query
        self.embedding_callback = _as_query_embedding(embedding_callback)
        self._graphrag: GraphRAGSettings = (
            settings.graphrag if settings else _default_graphrag_settings()
        )
        self.database_name = database_name
        self._settings = settings
        self._name_conventions = name_conventions
        self.chat_callback = chat_callback
        self._relation_vocabulary: RelationVocabulary | None = None

    @property
    def levers(self) -> GraphRAGSettings:
        """The GraphRAG settings the retrieval reads now: the shape's levers inside a call, else the base settings.

        ``graphrag_search`` decides the question's shape after the seeds
        (:meth:`_pipeline_steps`, step 2b) and from then on runs
        ``apply_shape_levers(base, shape)``: the graph-context assembly, the
        chunk arms, the merge, the chunk budget, the evidence markers and the
        rendering read it here. The seeds (:meth:`_match_seed_entities`,
        :meth:`_add_keyword_seeds`, ``keyword_seeds``), PageRank
        (:meth:`_run_ppr`) and the router run before the shape is known and
        read the base settings (``self._graphrag``) directly: the fields they
        read are :data:`~chaoscypher_core.question_shapes.PRE_SHAPE_FIELDS`,
        which no shape bundle may set (the settings refuse it, and a guard
        test pins every base read to the set). Outside a call, and after one
        (the scope is reset in a ``finally``, so a failed call leaks nothing),
        this is the base settings.
        """
        active = _ACTIVE_LEVERS.get()
        return self._graphrag if active is None else active

    @property
    def name_conventions(self) -> NameConventions:
        """The naming conventions the name matcher reads entity names and questions by.

        The ones the database's names were resolved by: the floor's tables and
        the union of its sources' packs
        (:func:`~chaoscypher_core.services.sources.engine.extraction.domain_resolver.source_packs`),
        through the rule the post-commit identity resolver applies
        (:func:`~chaoscypher_core.services.sources.engine.extraction.domain_resolver.conventions_of_sources`).
        Sources that chose different packs read by every pack's tables
        together; sources naming no pack read by the floor's. When no source
        listing can be read, the universal fallback
        (:data:`~chaoscypher_core.services.sources.engine.deduplication.name_identity.DEFAULT_NAME_CONVENTIONS`).
        Read once per handler. Until 2026-10-06 the matcher used a title list of
        its own (``name_identity.UNIVERSAL_TITLE_WORDS``), not the domain's (audit wave 3);
        until 2026-10-07 sources naming several domains fell back to the
        universal conventions.
        """
        if self._name_conventions is None:
            self._name_conventions = self._read_name_conventions()
        return self._name_conventions

    def _read_name_conventions(self) -> NameConventions:
        """The conventions of the packs the database's source rows name (see :attr:`name_conventions`)."""
        from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
            conventions_of_sources,
        )

        rows = self._source_rows("name_conventions")
        if rows is None:
            return DEFAULT_NAME_CONVENTIONS
        return conventions_of_sources(rows, self._settings)

    @property
    def relation_vocabulary(self) -> RelationVocabulary:
        """The relation vocabulary the database's sources read: the words prose uses for each relation.

        The floor's and the sources' packs' together, from the stack
        :attr:`name_conventions` reads names by
        (:func:`~chaoscypher_core.services.sources.engine.extraction.domain_resolver.vocabulary_of_sources`);
        the universal vocabulary when no source listing can be read, or when
        reading it fails or the sources' packs cannot be built (the retrieval
        fault ``relation_vocabulary``). The
        shape router reads the relation words a question uses from it
        (:meth:`_question_relations`). Read once per handler.
        """
        if self._relation_vocabulary is None:
            self._relation_vocabulary = self._read_relation_vocabulary()
        return self._relation_vocabulary

    def _read_relation_vocabulary(self) -> RelationVocabulary:
        """The vocabulary of the packs the database's source rows name (see :attr:`relation_vocabulary`)."""
        from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
            vocabulary_of_sources,
        )

        rows = self._source_rows("relation_vocabulary")
        if rows is None:
            return UNIVERSAL_VOCABULARY
        try:
            vocabulary = vocabulary_of_sources(rows, self._settings)
        except Exception:
            logger.warning("graphrag_relation_vocabulary_unreadable", exc_info=True)
            vocabulary = None
        else:
            if vocabulary is None:
                logger.warning("graphrag_relation_vocabulary_packs_unbuilt")
        if vocabulary is None:
            record_retrieval_fault("relation_vocabulary")
            return UNIVERSAL_VOCABULARY
        return vocabulary

    def _source_rows(self, fault: str) -> list[Any] | None:
        """The database's source rows, from the first store that lists them; None when none does.

        A listing that raises records ``fault`` (the reading that needed the
        rows: ``name_conventions``, ``relation_vocabulary``) and is None too,
        so the reader falls back to its universal tables, loudly.
        """
        for store in (self.source_storage, self.indexing):
            list_sources = getattr(store, "list_sources", None)
            if not callable(list_sources):
                continue
            try:
                listed = list_sources(page=1, page_size=_SOURCE_PAGE)
            except Exception:
                logger.warning("graphrag_source_domains_unreadable", reading=fault, exc_info=True)
                record_retrieval_fault(fault)
                return None
            if not (isinstance(listed, tuple) and listed and isinstance(listed[0], list)):
                continue  # not a source store (a test double)
            return listed[0]
        return None

    def _named_seed_count(self, query: str, nodes: Iterable[Any]) -> int:
        """How many things the question names among the seeds: its mentions that name a seed, not the seeds they match.

        A mention is a run of the question's naming words
        (:func:`_mention_words`) each naming a seed by the test keyword seeds
        and ``question_entities_first`` use (:func:`_names_a_term`: whole
        words, titles left out), counted by
        :func:`~chaoscypher_core.question_shapes.named_mention_count`: the
        rules count "Lise" and "Prince Andrew" in "How is Lise connected to
        Prince Andrew?" and never "the prince", "the Rostóvs" once however
        many Rostóv seeds it names, and a three-word name ("Anna Pávlovna
        Schérer") once: the rule is mentions, never the distinct naming words
        (ruling of 2026-10-09: counting the words made a one-person lookup
        abstain). The aggregate's entities are still the seeds those words
        name (:meth:`_question_named`).

        Args:
            query: The question.
            nodes: The seeds' node objects.
        """
        conventions = self.name_conventions
        seeds = list({node.id: node for node in nodes}.values())

        def names(key: str) -> bool:
            """Whether ``key`` is a naming word of one of the seeds."""
            return any(_names_a_term(node, {key}, conventions) for node in seeds)

        return named_mention_count(_mention_words(query, conventions), names)

    def _question_relations(self, query: str) -> list[str]:
        """The relations the question's words name in the database's :attr:`relation_vocabulary` (:func:`question_relations`)."""
        return question_relations(query, self.relation_vocabulary)

    def _router_chat(self) -> ChatCallback | None:
        """The chat model the shape router asks, counted under the ``router`` call site; None without one."""
        chat = self.chat_callback
        if chat is None:
            return None

        async def routed(*args: Any, **kwargs: Any) -> dict[str, Any]:
            """The chat call under the ``router`` call site."""
            with call_site("router"):
                return await chat(*args, **kwargs)

        return routed

    def _check_index_ready(self) -> None:
        """Record the fault ``needs_full_reindex`` when the search index is flagged for a rebuild.

        The flag is set when the index's vectors were written by another
        embedding model than the one configured: the searches still run and
        fill the budget, over vectors that do not compare with the query's.
        Until 2026-10-06 such a retrieval was complete as far as its record
        said, so the benchmark pinned and served it (audit wave 4). Recorded
        inside the caller's fault collector, which the benchmark refuses
        (:func:`~chaoscypher_core.benchmark.retrieval.require_complete_retrieval`).
        """
        try:
            flagged = getattr(self.search, "needs_full_reindex", False) is True
        except Exception:
            # unread is not in step: recorded, so the benchmark refuses it (audit wave 5)
            logger.warning("graphrag_reindex_flag_unreadable", exc_info=True)
            record_retrieval_fault(NEEDS_FULL_REINDEX_UNREADABLE)
            flagged = False
        if flagged:
            logger.warning("graphrag_index_needs_full_reindex")
            record_retrieval_fault("needs_full_reindex")

    # ------------------------------------------------------------------
    # Pipeline step 1: Embed query
    # ------------------------------------------------------------------

    async def _embed_query(self, query: str) -> list[float] | None:
        """Generate an embedding vector for the user query.

        Args:
            query: Raw query text.

        Returns:
            Embedding vector, or ``None`` if the callback is missing or
            fails (triggers keyword-only fallback in the main pipeline).

        """
        if not self.embedding_callback:
            logger.debug("graphrag_no_embedding_callback")
            return None
        try:
            result = await self.embedding_callback(query)
            # Support both attribute access and dict access
            if hasattr(result, "embedding"):
                return result.embedding  # type: ignore[no-any-return]
            if isinstance(result, dict):
                return result.get("embedding")
            return None
        except Exception:
            logger.warning("graphrag_embed_query_failed", query=query, exc_info=True)
            record_retrieval_fault("embed_query")
            return None

    # ------------------------------------------------------------------
    # Pipeline step 2: Match seed entities
    # ------------------------------------------------------------------

    def _match_seed_entities(
        self,
        query_embedding: list[float],
        seed_limit: int,
        source_ids: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Find seed entities via vector similarity.

        Queries the node vector index, keeps graph node ids only (no chunk or
        template hit can take a seed slot), and applies the similarity
        threshold from settings. A source scope is applied in the search,
        before its cut: an out-of-scope node that took a seed slot was then
        dropped by PageRank's scope, and the scoped chat seeded fewer
        entities than in-scope ones could have filled (audit wave 5). A
        flagged reference seeds by similarity only when no named candidate
        is closer (:func:`_similarity_seeds`), filtered before the cut too,
        so a named node takes the slot of a loose likeness: the embedding of
        ``Prince Vasíli's son`` is close to every question about Prince
        Vasíli (2026-10-07).

        Args:
            query_embedding: Embedding vector for the query.
            seed_limit: Maximum number of seed entities to return.
            source_ids: The chat's source scope, or None for the whole graph.

        Returns:
            List of ``(node_id, similarity_score)`` tuples.

        """
        try:
            overfetch = seed_limit * self._graphrag.vector_overfetch_multiplier
            raw_results = self.search.vector_search(
                query_embedding, k=overfetch, item_type="node", source_ids=source_ids
            )

            # Seeds are graph nodes only, filtered before the cut: a chunk or
            # template hit that took a slot (templates scored >= 0.3 on 118 of
            # 120 benchmark questions until 2026-10-06, audit R4) pushed a
            # real entity out of the ten.
            threshold = self._graphrag.seed_similarity_threshold
            seeds = [
                (rid, score)
                for rid, score in raw_results
                if _is_graph_node_id(rid) and score >= threshold
            ]
            fetched = (
                self.graph.get_nodes_batch([rid for rid, _ in seeds], include_embedding=False)
                if seeds
                else []
            )
            nodes = {n.id: n for n in fetched}
            return _similarity_seeds(seeds, nodes)[:seed_limit]
        except Exception:
            logger.warning("graphrag_seed_match_failed", exc_info=True)
            record_retrieval_fault("seed_match")
            return []

    # ------------------------------------------------------------------
    # Pipeline step 3: Personalized PageRank
    # ------------------------------------------------------------------

    def _add_keyword_seeds(
        self,
        query: str,
        seeds: list[tuple[str, float]],
        seed_limit: int,
        source_ids: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Seeds from keyword search over the node index, ahead of the vector seeds.

        A name or alias the query spells out should always seed PageRank; the
        vector match can miss it below the similarity threshold. The keyword
        search matches any significant word anywhere in a node (its
        description too), so a hit seeds only when a naming word of its
        label or of a stored alias is a naming word of the query
        (:func:`_names_a_term`, the test ``question_entities_first`` uses:
        whole words, titles left out, so a title alone never makes a keyword
        seed that outranks a vector seed), and only graph nodes that exist.
        A source scope is applied in the search, before its cut (as
        :meth:`_match_seed_entities`).

        A flagged reference (:func:`_seedable`) is never a name match; it
        seeds when the question writes its whole label
        (:func:`_writes_reference`: "the little princess"), after every
        keyword seed and before the vector seeds (a written label is the
        question's own text, a vector seed a likeness), at no more teleport
        weight than the lightest named seed kept, so PageRank never favours
        it over a name. The search runs for a question with no naming word
        too ("Who is the little princess?" has none).
        """
        conventions = self.name_conventions
        terms = _naming_terms(query, conventions)
        query_words = _phrase_words(query, conventions)
        try:
            overfetch = seed_limit * self._graphrag.vector_overfetch_multiplier
            hits = self.search.keyword_search(
                query, limit=overfetch, item_type="node", source_ids=source_ids
            )
            ids = [str(nid) for nid, _ in hits if _is_graph_node_id(nid)]
            fetched = self.graph.get_nodes_batch(ids, include_embedding=False) if ids else []
        except Exception:
            logger.warning("graphrag_keyword_seed_failed", exc_info=True)
            record_retrieval_fault("keyword_seeds")
            return seeds
        nodes = {n.id: n for n in fetched}
        keyword = [(nid, 1.0) for nid in ids if _names_a_term(nodes.get(nid), terms, conventions)]
        written = [
            (nid, 1.0) for nid in ids if _writes_reference(query_words, nodes.get(nid), conventions)
        ]
        seen: set[str] = set()
        merged: list[tuple[str, float]] = []
        for nid, score in [*keyword, *written, *seeds]:
            if nid not in seen:
                seen.add(nid)
                merged.append((nid, score))
        merged = merged[:seed_limit]
        references = {nid for nid, _ in written}
        weight = min((score for nid, score in merged if nid not in references), default=1.0)
        return [(nid, weight if nid in references else score) for nid, score in merged]

    def _load_graph(
        self, source_ids: list[str] | None, stats: dict[str, Any] | None = None
    ) -> tuple[list[Any], list[Any]]:
        """The whole graph PageRank and the walk read: ``(nodes, edges)`` under the chat's source scope.

        The minimal listings when the repository has them (else the plain
        ones), at most ``max_graph_nodes`` nodes and four times as many
        edges; under a scope, the nodes of its sources and the edges with
        both ends among them. An edge listing that returned its limit is
        recorded as ``stats["graph_listing_capped"]`` 1 (the edges past it
        were never read: the walk's gaps then say so, ``walk_skipped``'s
        ``listing_capped``). Every edge is returned, unevidenced ones
        included: PageRank (:meth:`_run_ppr`) and the walk
        (``graphrag_blocks.walk_chains``) each apply their own
        :func:`_edge_in_context` filter. A listing that fails records the
        retrieval fault ``ppr`` (PageRank cannot run without the graph) and
        returns none.

        Args:
            source_ids: The chat's source scope, or None for the whole graph.
            stats: The call's retrieval stats, for the listing cap (when given).
        """
        try:
            max_nodes = self._graphrag.max_graph_nodes
            # Load graph nodes (minimal variant when available), pushing the
            # chat's source scope into SQL: a Python filter AFTER the LIMIT
            # hands a scoped chat an arbitrary id-ordered slice once the
            # graph exceeds max_graph_nodes.
            if hasattr(self.graph, "list_nodes_minimal"):
                nodes = list(self.graph.list_nodes_minimal(limit=max_nodes, source_ids=source_ids))
            else:
                nodes = list(self.graph.list_nodes(limit=max_nodes, source_ids=source_ids))

            if not nodes:
                return [], []

            # Scope filter (belt-and-braces for repos ignoring the kwarg)
            if source_ids:
                nodes = [n for n in nodes if item_in_source_scope(n, source_ids)]

            # Load edges (scope in SQL too; the endpoint filter below still
            # enforces both-ends-in-scope)
            edge_limit = max_nodes * 4
            if hasattr(self.graph, "list_edges_minimal"):
                edges = list(self.graph.list_edges_minimal(limit=edge_limit, source_ids=source_ids))
            else:
                edges = list(self.graph.list_edges(limit=edge_limit))
            if stats is not None:  # the edges past a full listing were never read
                stats["graph_listing_capped"] = int(len(edges) >= edge_limit)

            if source_ids:
                node_id_set = {n.id for n in nodes}
                edges = [
                    e
                    for e in edges
                    if e.source_node_id in node_id_set and e.target_node_id in node_id_set
                ]
        except Exception:
            logger.warning("graphrag_graph_load_failed", exc_info=True)
            record_retrieval_fault("ppr")
            return [], []
        return nodes, edges

    def _run_ppr(
        self,
        seeds: list[tuple[str, float]],
        nodes: list[Any],
        edges: list[Any],
        source_ids: list[str] | None,
    ) -> dict[str, float]:
        """Run Personalized PageRank seeded on matched entities, over the loaded graph.

        Args:
            seeds: Seed entities as ``(node_id, score)`` pairs.
            nodes: The graph's nodes (:meth:`_load_graph`).
            edges: The graph's edges (:meth:`_load_graph`), unevidenced ones
                included: they spread no mass unless the base settings'
                ``include_unevidenced_edges`` lets them in.
            source_ids: The chat's source scope the graph was loaded under
                (logged when no seed is among its nodes).

        Returns:
            Mapping of node ID to PPR score. Empty dict when the graph
            has no nodes or no seed is one of its nodes (the pipeline then
            runs without graph context, ``vector_only``; never an
            unpersonalised PageRank).

        """
        try:
            # an unevidenced edge spreads no PageRank mass unless the lever lets it in
            include_unevidenced = self._graphrag.include_unevidenced_edges
            edges = [
                e for e in edges if _edge_in_context(e, include_unevidenced=include_unevidenced)
            ]

            if not nodes:
                return {}

            # Personalised on the seeds that are nodes of the loaded graph. With
            # none, rustworkx would teleport uniformly and hand back the graph's
            # global PageRank as if it were about the query (audit R4): fall
            # back to no graph context instead, loudly.
            node_ids = {n.id for n in nodes}
            personalization = {nid: score for nid, score in seeds if nid in node_ids and score > 0}
            if not personalization:
                logger.warning(
                    "graphrag_seeds_not_in_graph",
                    seeds=[nid for nid, _ in seeds],
                    scoped=bool(source_ids),
                    fallback="vector_only",
                )
                return {}

            # by id: PageRank's sums, and the order its scores tie in, are then the
            # graph's, never the listing's row order (the minimal listings have no ORDER BY)
            pr_result = calculate_pagerank(
                sorted(nodes, key=lambda n: str(n.id)),
                _by_edge_id(edges),
                damping=self._graphrag.ppr_damping,
                personalization=personalization,
            )
            if not pr_result.get("personalized", False):
                logger.warning("graphrag_ppr_not_personalized", fallback="vector_only")
                return {}
            scores: dict[str, float] = pr_result.get("pagerank_scores", {})
            return scores
        except Exception:
            logger.warning("graphrag_ppr_failed", exc_info=True)
            record_retrieval_fault("ppr")
            return {}

    # ------------------------------------------------------------------
    # Pipeline step 4: Assemble graph context
    # ------------------------------------------------------------------

    def _entity_entries(
        self,
        sorted_ids: list[str],
        nodes_map: dict[str, Any],
        seed_ids: set[str],
        ppr_scores: dict[str, float],
        query_terms: set[str] | None = None,
        dropped: _DescriptionDrops | None = None,
        template_map: Mapping[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The seed and related entity entries, with a description when the settings ask for one.

        ``template_map`` (:meth:`_resolve_template_names`) gives each entry its
        ``type``, the name the context lines show (the memory renderer's card head).

        ``query_terms`` (the question's :func:`_significant_terms`) order each
        description's sentences when ``question_aware_descriptions`` is on
        (:func:`_node_description`); off, they are not read. The sentences
        ``clean_descriptions`` drops as repeats are added to ``dropped`` (when
        given), for every entity described here, the question gate's later
        cut included.
        """
        terms = query_terms if self.levers.question_aware_descriptions else None
        seed_entities: list[dict[str, Any]] = []
        related_entities: list[dict[str, Any]] = []
        for nid in sorted_ids:
            node = nodes_map.get(nid)
            if not node:
                continue
            entry: dict[str, Any] = {
                "id": nid,
                "label": node.label,
                "template_id": node.template_id,
                "type": (template_map or {}).get(nid, ""),
                "ppr_score": ppr_scores.get(nid, 0.0),
            }
            entry.update(_entry_standing(node))  # flagged ones ranked after (_by_standing)
            if self.levers.entity_descriptions:
                desc = _node_description(
                    node,
                    self.levers.entity_description_chars,
                    clean=self.levers.clean_descriptions,
                    query_terms=terms,
                    conventions=self.name_conventions,
                    dropped=dropped,
                )
                if desc:
                    entry["description"] = desc
            if self.levers.entity_aliases:
                aliases = _node_aliases(node)
                if aliases:
                    entry["aliases"] = aliases
            (seed_entities if nid in seed_ids else related_entities).append(entry)
        return seed_entities, related_entities

    def _select_triples(
        self,
        sorted_ids: list[str],
        edges_by_node: dict[str, list[Any]],
        seed_ids: set[str],
        ppr_scores: dict[str, float],
        label_map: dict[str, str],
        max_triples: int,
        stated: set[tuple[str, str, str]] | None = None,
        named: set[str] | None = None,
        nodes_map: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """The relationship lines, capped at ``max_triples``.

        ``all_edges``: every edge of the top nodes in node order. ``connected``:
        the edges between top nodes or touching a seed, ranked by the higher
        endpoint's PageRank. With ``dedupe_graph_lines`` a line in ``stated``
        (``(source label, relation, target label)`` of the path lines) and the
        reverse of a symmetric generic line already taken are skipped; with
        ``generic_relations_last`` generic relations rank after the specific
        ones, and with ``question_entities_first`` the lines touching an entity
        the question names (``named``) rank before all others. Last, and so
        first in effect, the lines are ranked by standing (:func:`_line_standing`
        over ``nodes_map``: the order the triple summary cuts by), so a line
        the pipeline flagged gives its slot to an unflagged one. All act before
        the cap, so it fills with distinct, specific facts about what was asked.
        """
        connected = self.levers.triple_selection == "connected"
        dedupe = self.levers.dedupe_graph_lines
        top_ids = set(sorted_ids)
        seen: set[tuple[str, str, str]] = set()
        taken: set[tuple[str, str, str]] = set()
        candidates: list[tuple[float, Any]] = []
        for nid in sorted_ids:
            for edge in edges_by_node.get(nid, []):
                key = (edge.source_node_id, edge.label, edge.target_node_id)
                if key in seen:
                    continue
                seen.add(key)
                src, tgt = edge.source_node_id, edge.target_node_id
                if connected and not _connected_edge(src, tgt, top_ids, seed_ids):
                    continue
                if dedupe and (
                    (label_map.get(src, src), edge.label, label_map.get(tgt, tgt))
                    in (stated or set())
                    or (
                        edge.label in SYMMETRIC_GENERIC_RELATIONS
                        and (tgt, edge.label, src) in taken
                    )
                ):
                    continue
                rank = max(ppr_scores.get(src, 0.0), ppr_scores.get(tgt, 0.0)) if connected else 0.0
                candidates.append((rank, edge))
                taken.add(key)
        if connected:
            candidates.sort(key=lambda c: -c[0])  # stable: ties keep node order
        if self.levers.generic_relations_last:
            candidates.sort(key=lambda c: c[1].label in GENERIC_RELATIONS)  # stable
        if named:
            candidates.sort(  # stable: lines touching a named entity first, the order above kept
                key=lambda c: not (c[1].source_node_id in named or c[1].target_node_id in named)
            )
        # stable: by standing before the cap, the order the summary cuts by (keep, flag, never
        # delete): a line touching a reference, an orphan or a doubted node after the others, an
        # unevidenced one (only present under include_unevidenced_edges) after every evidenced
        # one, a capped, unmatched-label or type-mismatched one last of its tier
        candidates.sort(key=lambda c: _line_standing(c[1], nodes_map or {}))
        return [
            {
                "source": label_map.get(e.source_node_id, e.source_node_id),
                "source_id": e.source_node_id,
                "label": e.label,
                "target": label_map.get(e.target_node_id, e.target_node_id),
                "target_id": e.target_node_id,
                # the fact behind the line (the memory renderer's provenance); a bare test edge has none
                "edge_id": getattr(e, "id", None),
            }
            for _, e in candidates[:max_triples]
        ]

    def _context_edges(self, edges: Iterable[Any]) -> list[Any]:
        """The listed edges the context reads, in their order: unevidenced ones only with the lever."""
        include = self.levers.include_unevidenced_edges
        return [e for e in edges if _edge_in_context(e, include_unevidenced=include)]

    def _node_edges(
        self, node_ids: Sequence[str]
    ) -> tuple[dict[str, list[Any]], dict[str, list[Any]], set[str]]:
        """Each node's edges, outgoing then incoming, each by id: ``(read, listed, capped)``.

        ``read`` is what the context reads (:meth:`_context_edges`:
        unevidenced ones only with the lever); ``listed`` is every listed
        edge, so the aggregate block counts the ones it leaves out (keep,
        flag, never delete); ``capped`` holds the nodes whose listing returned
        ``batching.graphrag_edge_query_limit`` rows in a direction, whose
        edges past the limit were never read. Two reads for all the nodes
        (``list_edges_by_node``), not two per node: listed node by node, the
        edges took 40% of a retrieval sample run (9,248 queries, 2026-10-16).
        """
        limit = get_settings().batching.graphrag_edge_query_limit
        outgoing = self.graph.list_edges_by_node(node_ids, outgoing=True, limit_per_node=limit)
        incoming = self.graph.list_edges_by_node(node_ids, outgoing=False, limit_per_node=limit)
        read: dict[str, list[Any]] = {}
        listed: dict[str, list[Any]] = {}
        capped: set[str] = set()
        for nid in node_ids:
            out = _by_edge_id(list(outgoing.get(nid, [])))
            into = _by_edge_id(list(incoming.get(nid, [])))
            read[nid] = self._context_edges(out) + self._context_edges(into)
            listed[nid] = out + into
            if len(out) >= limit or len(into) >= limit:
                capped.add(nid)
        return read, listed, capped

    def _assemble_graph_context(
        self,
        ppr_scores: dict[str, float],
        seeds: list[tuple[str, float]],
        query: str = "",
        source_ids: list[str] | None = None,
        *,
        shape: QuestionShape = QuestionShape.LOOKUP,
        relations: Sequence[str] = (),
        graph: tuple[Sequence[Any], Sequence[Any]] | None = None,
        stats: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a structured graph context from PPR results.

        Fetches node metadata and 1-hop edges for the top-scoring
        entities and formats them as triples. Under a source scope only the
        edges with both ends in it are kept, as PageRank kept them
        (:meth:`_run_ppr`): until 2026-10-06 the per-node edge listing was
        unscoped, so a scoped chat read relationships to entities of sources
        outside its scope (audit wave 4).

        Args:
            ppr_scores: Mapping of node ID to PPR score.
            seeds: Original seed ``(node_id, score)`` pairs.
            query: The user query, for the question gate (``GraphRAGSettings.question_gate``).
            source_ids: The chat's source scope, or None for the whole graph.
            shape: The question's shape: ``aggregate`` adds the aggregate
                block (``graphrag_blocks.aggregate_blocks``) of the named seeds.
            relations: The relations the question's words name
                (:meth:`_question_relations`), the aggregate block's families.
            graph: The whole loaded graph's ``(nodes, edges)``
                (:meth:`_load_graph`), the walk's search space under the
                ``path`` shape; None (no graph loaded) runs no walk.
            stats: The call's retrieval stats: the walk records what it left
                out (``walk_skipped``), the descriptions the sentences they
                dropped as repeats (``descriptions_dropped``), and a ``path``
                question naming fewer than two entities, or an ``aggregate``
                one naming none, says so on ``shape_reason`` (the assembly is
                then the lookup's).

        Returns:
            Dict with ``seed_entities``, ``related_entities``,
            ``relationships`` (triples), and ``summary`` keys, plus
            ``paths``, ``aggregates``, ``walks`` and ``walk_gaps`` when
            there are any.

        """
        try:
            seed_ids = {sid for sid, _ in seeds}
            ppr_top_k = self.levers.ppr_top_k
            max_triples = self.levers.max_triples

            # Sort by PPR score descending, take top K
            sorted_ids = _ppr_ranked(ppr_scores)[:ppr_top_k]

            if not sorted_ids:
                return {}

            # 1-hop edges of the top entities, and their neighbours to batch-fetch labels for
            edges_by_node, listed_by_node, capped_ids = self._node_edges(sorted_ids)
            # the aggregate block reads every listed edge: fetch its endpoints too, so the scope
            # below judges them (an edge to an unfetched node would read as out of scope)
            aggregate = shape == QuestionShape.AGGREGATE
            neighbor_ids = {
                end
                for edges in (listed_by_node if aggregate else edges_by_node).values()
                for e in edges
                for end in (e.source_node_id, e.target_node_id)
            }

            # Single batch-fetch for all nodes (sorted + neighbors)
            all_needed = neighbor_ids | set(sorted_ids)
            all_fetched = self.graph.get_nodes_batch(list(all_needed), include_embedding=False)
            if source_ids:
                scope = set(source_ids)
                in_scope = {n.id for n in all_fetched if item_in_source_scope(n, scope)}
                edges_by_node, listed_by_node = (
                    _in_scope(edges_by_node, in_scope),
                    _in_scope(listed_by_node, in_scope),
                )
            nodes_map: dict[str, Any] = {n.id: n for n in all_fetched}
            label_map: dict[str, str] = {n.id: n.label for n in all_fetched}
            template_map = self._resolve_template_names(all_fetched)

            conventions = self.name_conventions
            # the entities the question names: the aggregate's entities whatever the lever,
            # and the entities question_entities_first lists first
            question_named = self._question_named(query, sorted_ids, nodes_map)
            named = question_named if self.levers.question_entities_first else set()

            # Classify into seed vs related; the question's words order each description's
            # sentences under question_aware_descriptions, gate or no gate
            dropped = _DescriptionDrops()
            seed_entities, related_entities = self._entity_entries(
                sorted_ids,
                nodes_map,
                seed_ids,
                ppr_scores,
                _significant_terms(query, conventions)
                if self.levers.question_aware_descriptions
                else None,
                dropped,
                template_map=template_map,
            )
            if stats is not None:
                stats["descriptions_dropped"] = dropped.sentences
            if named:  # stable: the entities the question names first
                seed_entities.sort(key=lambda e: e["id"] not in named)
                related_entities.sort(key=lambda e: e["id"] not in named)
            # a flagged reference after every named entity, an orphan after every connected
            # one of its standing, PageRank order kept within each
            seed_entities = _by_standing(seed_entities)
            related_entities = _by_standing(related_entities)
            query_terms = (
                _significant_terms(query, conventions) if self.levers.question_gate else set()
            )
            if query_terms:  # on the stored text, never the rendered line (audit R6)
                related_entities = [
                    e
                    for e in related_entities
                    if _shares_a_term(
                        _stored_text(nodes_map.get(e["id"])), query_terms, conventions
                    )
                ]

            all_edges = [e for edges in edges_by_node.values() for e in edges]
            ordered_seeds = [sid for sid in sorted_ids if sid in seed_ids]
            if len(named & set(ordered_seeds)) >= 2:  # paths between what the question names
                ordered_seeds = [sid for sid in ordered_seeds if sid in named]
            paths = (
                _seed_paths(ordered_seeds, all_edges, label_map, self.levers.max_paths)
                if self.levers.seed_paths
                else []
            )
            if self.levers.seed_paths and self.levers.paths_to_top:
                paths += _paths_to_top(
                    ordered_seeds,
                    [nid for nid in sorted_ids if nid not in seed_ids],
                    all_edges,
                    label_map,
                    self.levers.max_paths - len(paths),
                )
            stated = {
                (step["source"], step["label"], step["target"])
                for path in paths
                for step in path["steps"]
            }
            triples = self._select_triples(
                sorted_ids,
                edges_by_node,
                seed_ids,
                ppr_scores,
                label_map,
                max_triples,
                stated,
                named,
                nodes_map,
            )
            if query_terms:
                triples = [
                    t
                    for t in triples
                    if t["source_id"] in seed_ids
                    or t["target_id"] in seed_ids
                    or _shares_a_term(
                        f"{t['source']} {t['label']} {t['target']}", query_terms, conventions
                    )
                ]

            aggregates = (
                self._aggregate_block(
                    [nid for nid in ordered_seeds if nid in question_named],
                    listed_by_node,
                    nodes_map,
                    label_map,
                    relations,
                    source_ids,
                    capped_ids,
                    stats,
                )
                if aggregate
                else []
            )

            summary = self._build_summary(triples, template_map)
            entity_count = len(seed_entities) + len(related_entities)
            triple_count = len(triples)

            context: dict[str, Any] = {
                "seed_entities": seed_entities,
                "related_entities": related_entities,
                "relationships": triples,
                "entity_count": entity_count,
                "triple_count": triple_count,
                "summary": summary,
            }
            if paths:
                context["paths"] = paths
            if aggregates:
                context["aggregates"] = aggregates
            if shape == QuestionShape.PATH and graph is not None:
                context.update(
                    self._walk_block(
                        query,
                        [sid for sid in _ppr_ranked(ppr_scores) if sid in seed_ids],
                        graph,
                        nodes_map,
                        source_ids,
                        stats,
                    )
                )
            return context
        except Exception:
            logger.warning("graphrag_assemble_context_failed", exc_info=True)
            record_retrieval_fault("assemble_context")
            return {}

    def _aggregate_block(
        self,
        targets: list[str],
        listed_by_node: Mapping[str, Sequence[Any]],
        nodes_map: Mapping[str, Any],
        label_map: Mapping[str, str],
        relations: Sequence[str],
        source_ids: list[str] | None,
        capped_ids: Collection[str],
        stats: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """The aggregate blocks of an ``aggregate`` question: the named seeds' listed edges by the question's relations.

        ``targets`` are the seeds the question names, in PageRank order; each
        block lists their edges (unevidenced ones too, so the block counts
        what it leaves out), grouped by label and filtered to the question's
        relation families, each row with its first stored sentence
        (``graphrag_blocks.aggregate_blocks``). A question naming none has no
        block and assembles as a lookup, said on ``stats["shape_reason"]``
        (until 2026-10-09 it listed every seed's edges).
        """
        if not targets:
            _note_shape_reason(stats, "no named entity, lookup assembly")
            return []
        block_edges = [e for nid in targets for e in listed_by_node.get(nid, [])]
        return aggregate_blocks(
            entity_ids=targets,
            edges_by_node=listed_by_node,
            nodes_map=nodes_map,
            label_map=label_map,
            families=self._families(relations),
            citations=self._citations_by_edge(
                [str(getattr(e, "id", "") or "") for e in block_edges], source_ids
            ),
            per_label=self.levers.aggregate_per_label,
            include_unevidenced=self.levers.include_unevidenced_edges,
            capped_ids=capped_ids,
        )

    def _walk_block(
        self,
        query: str,
        ranked_seeds: Sequence[str],
        graph: tuple[Sequence[Any], Sequence[Any]],
        nodes_map: Mapping[str, Any],
        source_ids: list[str] | None,
        stats: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """The walk block of a ``path`` question, between the entities it names over the whole graph, as context keys.

        ``walks`` and ``walk_gaps`` (``graphrag_blocks.walk_blocks``), each
        only when non-empty (:meth:`_walk_context`). A walk that fails records
        the retrieval fault ``walk`` and adds nothing: the rest of the graph
        context stands.
        """
        try:
            return self._walk_context(query, ranked_seeds, graph, nodes_map, source_ids, stats)
        except Exception:
            logger.warning("graphrag_walk_failed", exc_info=True)
            record_retrieval_fault("walk")
            return {}

    def _walk_context(
        self,
        query: str,
        ranked_seeds: Sequence[str],
        graph: tuple[Sequence[Any], Sequence[Any]],
        nodes_map: Mapping[str, Any],
        source_ids: list[str] | None,
        stats: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """The walk block's work (:meth:`_walk_block`), unguarded.

        The ends are the seeds the question names (:meth:`_question_named`)
        in PageRank order, ranked anywhere (``ranked_seeds``), not only among
        the top entities: a seed the context did not fetch is fetched to be
        read. The pairs are those ends two at a time
        (``graphrag_blocks.walk_pairs``); the chains are searched over the
        loaded graph (``graph``, :meth:`_load_graph`: its edges with both ends
        among its nodes, ``graphrag_blocks.walk_chains``: inverses folded,
        unevidenced edges only with the lever, at most ``max_hops`` relations,
        the shortest length and one longer, and ``max_walks`` walks for the
        whole question). A node a chain passes through is
        labelled from the context's nodes, else from the loaded node when it
        carries a label, else fetched; each hop carries its edge's first
        stored sentence (:meth:`_citations_by_edge`). What the walk left out
        is recorded on ``stats["walk_skipped"]``: the walk's own counts, the
        pairs past ``WALK_MAX_PAIRS`` (``pairs``) and a capped graph listing
        (``listing_capped``, from :meth:`_load_graph`); a gap says whether
        any edge was left out.

        Fewer than two named seeds: no walk, and ``stats["shape_reason"]``
        says the call assembled as a lookup (spec § 4.2 step 3).
        """
        unread = [sid for sid in ranked_seeds if sid not in nodes_map]
        if unread:
            fetched = self.graph.get_nodes_batch(unread, include_embedding=False)
            nodes_map = {**nodes_map, **{n.id: n for n in fetched}}
        named = self._question_named(query, ranked_seeds, nodes_map)
        ends = [sid for sid in ranked_seeds if sid in named]
        pairs = walk_pairs(ends)
        if not pairs:
            _note_shape_reason(stats, "fewer than two named entities, lookup assembly")
            return {}
        levers = self.levers
        hops, max_walks = levers.max_hops, levers.max_walks
        include = levers.include_unevidenced_edges
        loaded = {n.id: n for n in graph[0]}
        chains, skipped = walk_chains(
            pairs=pairs,
            edges=graph[1],
            max_hops=hops,
            max_walks=max_walks,
            include_unevidenced=include,
            node_ids=loaded.keys(),
        )
        skipped["pairs"] = walk_pairs_left_out(ends)
        skipped["listing_capped"] = int(bool((stats or {}).get("graph_listing_capped")))
        labelled = {
            nid: loaded[nid]
            for nid in chain_node_ids(chains)
            if nid not in nodes_map and getattr(loaded.get(nid), "label", None)
        }
        nodes_map = {**labelled, **nodes_map}
        missing = [nid for nid in chain_node_ids(chains) if nid not in nodes_map]
        if missing:
            fetched = self.graph.get_nodes_batch(missing, include_embedding=False)
            nodes_map = {**nodes_map, **{n.id: n for n in fetched}}
        if stats is not None:
            stats["walk_skipped"] = skipped
        walks, gaps = walk_blocks(
            pairs=pairs,
            edges=graph[1],
            nodes_map=nodes_map,
            citations=self._citations_by_edge(walk_edge_ids(chains), source_ids),
            max_hops=hops,
            max_walks=max_walks,
            include_unevidenced=include,
            chains=chains,
            skipped=skipped,
        )
        return {key: rows for key, rows in (("walks", walks), ("walk_gaps", gaps)) if rows}

    def _question_named(
        self, query: str, node_ids: Sequence[str], nodes_map: Mapping[str, Any]
    ) -> set[str]:
        """The ids among ``node_ids`` whose node the question names, by :meth:`_named_seed_count`'s test.

        The one reading of "the question names it": the rules count these
        among the seeds, the walk joins them, and ``question_entities_first``
        lists them first.
        """
        conventions = self.name_conventions
        asked = _naming_terms(query, conventions)
        return {nid for nid in node_ids if _names_a_term(nodes_map.get(nid), asked, conventions)}

    def _families(self, relations: Sequence[str]) -> dict[str, frozenset[str]]:
        """Each of the question's relations and its edge labels (:attr:`relation_vocabulary`); none for none."""
        wanted = set(relations)
        families: dict[str, frozenset[str]] = {}
        for name, labels, _ in self.relation_vocabulary.relations:
            if name in wanted:
                families[name] = families.get(name, frozenset()) | labels
        return families

    def _citations_by_edge(
        self, edge_ids: Sequence[str], source_ids: list[str] | None
    ) -> dict[str, list[dict[str, Any]]]:
        """The stored citation rows of ``edge_ids``, by edge id, in the storage's order.

        Read through ``get_relationship_citations_batch`` when the source
        storage has it; a read that fails records the retrieval fault
        ``relationship_citations`` and returns none (the rows then render
        without sentences); a reply that is not a list (a test double) is none.
        """
        read = getattr(self.source_storage, "get_relationship_citations_batch", None)
        ids = list(dict.fromkeys(i for i in edge_ids if i))
        if not ids or not callable(read):
            return {}
        try:
            rows = read(self.database_name, ids, source_ids)
        except Exception:
            logger.warning("graphrag_relationship_citations_unreadable", exc_info=True)
            record_retrieval_fault("relationship_citations")
            return {}
        if not isinstance(rows, list):
            return {}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if isinstance(row, dict) and row.get("edge_id"):
                grouped.setdefault(str(row["edge_id"]), []).append(row)
        return grouped

    def _resolve_template_names(self, nodes: list[Any]) -> dict[str, str]:
        """Build a mapping from node ID to the type its context lines show.

        The pack subtype when the node carries one (``properties.entity_subtype``,
        ``character`` -> "character", ``military_unit`` -> "military unit"),
        else its core ``entity_type`` ("Person"); spec 2026-10-07 § 6. Nodes
        with neither fall back to their template's display name (one per core
        type since the restructure) instead of a raw UUID.

        Args:
            nodes: List of node objects with ``id``, ``template_id`` and
                optionally ``entity_type`` and ``properties``.

        Returns:
            Dict mapping node ID to type name (empty string if none).

        """
        template_ids = {n.template_id for n in nodes if n.template_id}
        name_cache: dict[str, str] = {}
        for tid in template_ids:
            tmpl = self.graph.get_template(tid)
            name_cache[tid] = tmpl.name if tmpl else tid
        return {
            n.id: (
                _rendered_subtype(n)
                or getattr(n, "entity_type", None)
                or (name_cache.get(n.template_id, "") if n.template_id else "")
            )
            for n in nodes
        }

    @staticmethod
    def _build_summary(
        triples: list[dict[str, str]],
        template_map: dict[str, str],
    ) -> str:
        """Build a budgeted human-readable summary from triples, cut by rank.

        The lines come ranked: the selection sorts them by standing before its
        cap (:meth:`_select_triples`, :func:`_line_standing`: named before
        reference, connected before orphan, undoubted before doubted,
        evidenced before unevidenced, then an undoubted edge before a capped,
        unmatched-label or type-mismatched one). The ~4K-character budget
        (~1K tokens) keeps whole lines from the top and cuts the tail, noting
        how many lines it left out. Until 2026-10-07 the cut was by bytes over
        the selection order: a reference's lines could take the budget from a
        named entity's; until 2026-10-08 the summary re-ranked the lines
        itself while the selection ranked only an unevidenced one last.

        Args:
            triples: List of triple dicts with source, target, label keys, in rank order.
            template_map: Mapping of node ID to template ID for type labels.

        Returns:
            Newline-separated summary string, cut when over the budget.

        """
        lines: list[str] = []
        for t in triples:
            src_type = template_map.get(t["source_id"], "")
            tgt_type = template_map.get(t["target_id"], "")
            src_suffix = f" ({src_type})" if src_type else ""
            tgt_suffix = f" ({tgt_type})" if tgt_type else ""
            lines.append(f"{t['source']}{src_suffix} {t['label']} {t['target']}{tgt_suffix}.")

        kept: list[str] = []
        used = 0
        for line in lines:
            cost = len(line) + (1 if kept else 0)
            if used + cost > _SUMMARY_MAX_CHARS:
                break
            kept.append(line)
            used += cost
        summary = "\n".join(kept)
        if len(kept) < len(lines):
            summary = f"{summary}\n... ({len(lines) - len(kept)} more triples omitted)"
        return summary

    # ------------------------------------------------------------------
    # The chunk arms' one hydration
    # ------------------------------------------------------------------

    def _hydrate_chunks(
        self,
        rows: Iterable[tuple[Mapping[str, Any], float]],
        source_ids: list[str] | None,
        *,
        origin: str,
    ) -> list[dict[str, Any]]:
        """The chunk dicts the merge reads, from stored chunk rows and their scores, in order.

        The one hydration of the three chunk arms (provenance, hybrid,
        evidence): a row outside ``source_ids`` is skipped, the filename is
        read once per source, the content is numbered under a provisional
        alias (``C0``, ``C1``, ... in this list; the merge assigns the final
        ones) and the origin is ``origin``.
        """
        chunks: list[dict[str, Any]] = []
        kept = [
            (chunk_data, score)
            for chunk_data, score in rows
            if not source_ids or chunk_data.get("source_id", "") in source_ids
        ]
        source_filenames = chunk_source_filenames(
            (chunk_data for chunk_data, _ in kept),
            getattr(self.indexing, "get_source_filenames_by_ids", None),
        )
        for chunk_data, score in kept:
            chunk_source_id = chunk_data.get("source_id", "")
            original_content = chunk_data.get("content", "")
            filename = source_filenames.get(chunk_source_id, "")
            alias = f"C{len(chunks)}"
            numbered_content, sentence_count = format_chunk_content(
                original_content, filename, alias
            )
            chunks.append(
                {
                    "chunk_id": chunk_data["id"],
                    "chunk_alias": alias,
                    "content": numbered_content,
                    "original_content": original_content,
                    "source_id": chunk_source_id,
                    "filename": filename,
                    "chunk_index": chunk_data.get("chunk_index"),
                    "page_number": chunk_data.get("page_number"),
                    "section": chunk_data.get("section"),
                    "start_time": chunk_data.get("start_time"),
                    "end_time": chunk_data.get("end_time"),
                    "sentence_count": sentence_count,
                    "chunk_metadata": clean_chunk_metadata(chunk_data.get("chunk_metadata")),
                    "score": score,
                    "retrieval_origin": origin,
                }
            )
        return chunks

    def _hydrate_survivors(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """``chunks`` with each unhydrated provenance stub built in place (:meth:`_hydrate_chunks`).

        A stub keeps the ``retrieval_origin`` and ``rrf_score`` the merge
        gave it; the built dict is the one the eager arm produced, key for
        key. Hydrated chunks pass through untouched.
        """
        stubs = [c for c in chunks if _UNHYDRATED_ROW in c]
        if not stubs:
            return chunks
        built = iter(
            self._hydrate_chunks(
                ((stub[_UNHYDRATED_ROW], stub["score"]) for stub in stubs),
                None,  # the arm already applied the source scope
                origin="provenance",
            )
        )
        out: list[dict[str, Any]] = []
        for chunk in chunks:
            if _UNHYDRATED_ROW not in chunk:
                out.append(chunk)
                continue
            full = next(built)
            full["retrieval_origin"] = chunk["retrieval_origin"]
            if "rrf_score" in chunk:
                full["rrf_score"] = chunk["rrf_score"]
            out.append(full)
        return out

    # ------------------------------------------------------------------
    # Pipeline step 5: Retrieve provenance chunks (citation-based)
    # ------------------------------------------------------------------

    async def _retrieve_provenance_chunks(
        self,
        ppr_top_node_ids: list[str],
        source_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        """Retrieve document chunks linked to top PPR entities via citations.

        Args:
            ppr_top_node_ids: Node IDs from the graph context.
            source_ids: Optional source scope filter.

        Returns:
            The cited chunks in PageRank order, unhydrated: ``chunk_id``,
            ``source_id``, ``score`` and ``retrieval_origin`` plus the stored
            row, which :meth:`_merge_and_rank` builds for the survivors only.

        """
        if not self.source_storage or not self.indexing:
            return []
        try:
            # in lookup (PageRank) order, never the repository's row order
            citations = _in_lookup_order(
                self.source_storage.get_citations_batch(
                    self.database_name,
                    entity_uris=ppr_top_node_ids,
                    source_ids=source_ids,
                ),
                ppr_top_node_ids,
                key=lambda c: c.get("entity_uri"),
                then=_citation_tiebreak,
            )

            # Extract unique chunk IDs
            chunk_ids: list[str] = []
            seen: set[str] = set()
            for cit in citations:
                cid = cit.get("chunk_id")
                if cid and cid not in seen:
                    seen.add(cid)
                    chunk_ids.append(cid)

            # Hydrate chunks — one batch SELECT instead of one per citation;
            # missing ids are silently absent, the order is the citations'.
            chunk_rows = _in_lookup_order(
                self.indexing.get_chunks_by_ids_batch(chunk_ids),
                chunk_ids,
                key=lambda row: row.get("id"),
            )
            # Unhydrated: the top entities cite most of the corpus, and rank fusion
            # reads ids and ranks alone, so only the chunks that survive the cut
            # are numbered and built (_hydrate_survivors).
            return [
                {
                    "chunk_id": row["id"],
                    "source_id": row.get("source_id", ""),
                    "score": 1.0,  # provenance chunks get max score
                    "retrieval_origin": "provenance",
                    _UNHYDRATED_ROW: row,
                }
                for row in chunk_rows
                if not source_ids or row.get("source_id", "") in source_ids
            ]
        except Exception:
            logger.warning("graphrag_provenance_retrieval_failed", exc_info=True)
            record_retrieval_fault("provenance_chunks")
            return []

    # ------------------------------------------------------------------
    # Pipeline step 6: Retrieve vector/hybrid chunks
    # ------------------------------------------------------------------

    async def _retrieve_vector_chunks(
        self,
        query: str,
        limit: int,
        source_ids: list[str] | None,
        stats: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve document chunks via hybrid (semantic + keyword) search.

        With ``graphrag.question_parts`` (:attr:`levers`), a two-part question
        (:func:`~chaoscypher_core.question_shapes.question_parts`) is searched
        whole and part by part, the hits interleaved (:func:`_interleave_hits`:
        the whole's first, then each part's first, ...) so each part keeps its
        share of the arm and of the hybrid reserve; ``stats["question_parts"]``
        counts the parts. The scope check reads the whole question's search.

        Args:
            query: User query text.
            limit: Target number of chunks.
            source_ids: Optional source scope filter.
            stats: The call's retrieval stats, for ``question_parts``.

        Returns:
            List of hydrated chunk dicts.

        """
        if not self.indexing:
            return []
        try:
            # Chunks only, in both arms: a search over every type filtered to
            # chunks afterwards let nodes and templates take the slots (6-9
            # chunks instead of 10 on seven benchmark questions, audit R3).
            # The scope in both arms, before their cut: filtered here afterwards, a
            # chat scoped to one of two sources got 6-9 chunks instead of 10 on 41
            # of 120 benchmark questions (audit wave 5).
            parts = question_parts(query) if self.levers.question_parts else [query]
            searches = [query, *parts] if len(parts) > 1 else [query]
            hit_lists = [
                await self.search.hybrid_search(
                    text,
                    k=limit * HYBRID_OVERFETCH,
                    embedding_provider_callback=self.embedding_callback,
                    min_similarity=HYBRID_MIN_SIMILARITY,
                    item_type="chunk",
                    source_ids=source_ids,
                )
                for text in searches
            ]
            search_results = hit_lists[0]
            if stats is not None and len(parts) > 1:
                stats["question_parts"] = len(parts)

            chunk_hits = [
                (result_id[6:], score)
                for result_id, score in _interleave_hits(hit_lists)
                if result_id.startswith("chunk:")
            ]
            # One batch SELECT instead of one per hit; missing ids are
            # silently absent from the lookup map, matching the old loop.
            chunks_by_id = {
                row["id"]: row
                for row in self.indexing.get_chunks_by_ids_batch([cid for cid, _ in chunk_hits])
            }

            # a search that ignored the scope is filtered in the hydration (belt and braces)
            chunks = self._hydrate_chunks(
                (
                    (chunks_by_id[chunk_uuid], score)
                    for chunk_uuid, score in chunk_hits
                    if chunks_by_id.get(chunk_uuid)
                ),
                source_ids,
                origin="vector",
            )
            if source_ids:
                self._check_scope_filled(len(chunks), limit, search_results)
            return chunks
        except Exception:
            logger.warning("graphrag_vector_retrieval_failed", query=query, exc_info=True)
            record_retrieval_fault("vector_chunks")
            return []

    def _check_scope_filled(self, found: int, limit: int, hits: Any) -> None:
        """Record :data:`~chaoscypher_core.utils.retrieval_faults.SCOPE_SHORT` when a scoped hybrid arm came up short.

        Short is fewer chunks than ``min(limit, candidates)``, where the
        candidates are what the search's arms found in the scope before their
        cut (``hits.candidates_seen``,
        :class:`~chaoscypher_core.ports.search.SearchHits`): keyword hits and
        vector hits at or above the similarity floor. A scope whose
        candidates did not reach the search's ``k`` was read to the end
        (``scope_exhausted``), so a result as short as its candidates is the
        scope holding no more that answers, never a fault; fewer chunks than
        that is a hit lost on the way (a scope the search ignored, a chunk
        that would not hydrate). Until 2026-10-06 the arm was compared with
        the number of chunks in the scope, which ignores the floor and
        keyword misses, so "What did the regiment eat?" recorded the fault
        scoped (two chunks) and not unscoped (five) (audit wave 6). A search
        that does not say what it saw is not checked.
        """
        seen = getattr(hits, "candidates_seen", None)
        if not isinstance(seen, int) or isinstance(seen, bool):
            return
        if found < min(limit, seen):
            logger.warning(
                "graphrag_scoped_hybrid_short",
                found=found,
                limit=limit,
                candidates_seen=seen,
                scope_exhausted=getattr(hits, "scope_exhausted", None),
            )
            record_retrieval_fault(SCOPE_SHORT)

    # ------------------------------------------------------------------
    # Pipeline step 6b: the evidence arm (the chunks the graph block cites)
    # ------------------------------------------------------------------

    async def _retrieve_evidence_chunks(
        self,
        chunk_ids: Sequence[str],
        source_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        """The evidence arm's own chunks: the first ``graphrag.evidence_chunks`` of ``chunk_ids``, hydrated.

        ``chunk_ids`` are cited chunks in block order (:meth:`_evidence_arm`
        passes those the hybrid reserve does not already hold); each is read
        once, in that order, with origin ``evidence`` and score 1.0. Lever 0
        retrieves nothing (storage is not asked). A cited chunk the index no
        longer holds, or one outside ``source_ids``, is absent (the pipeline
        counts it in ``evidence_left_out``); a lookup that raises records the
        retrieval fault ``evidence_chunks`` and the arm is empty.
        """
        cap = self.levers.evidence_chunks
        if cap <= 0 or not chunk_ids or not self.indexing:
            return []
        wanted = list(dict.fromkeys(chunk_ids))[:cap]
        try:
            rows = _in_lookup_order(
                self.indexing.get_chunks_by_ids_batch(wanted),
                wanted,
                key=lambda row: row.get("id"),
            )
            return self._hydrate_chunks(((row, 1.0) for row in rows), source_ids, origin="evidence")
        except Exception:
            logger.warning("graphrag_evidence_retrieval_failed", exc_info=True)
            record_retrieval_fault("evidence_chunks")
            return []

    async def _evidence_arm(
        self,
        graph_context: Mapping[str, Any],
        vector_chunks: list[dict[str, Any]],
        limit: int,
        source_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        """The evidence arm the merge ranks first: the chunks the graph block cites, in block order.

        The cited chunks are the aggregate rows', then the walk hops'
        (:func:`cited_chunk_ids`). One the hybrid arm's reserve already
        holds (its top ``min(vector_reserve, limit)``, the ids
        :meth:`_reserve_hybrid_hits` protects) costs no evidence slot and is
        not read again: the hybrid arm's own chunk joins the arm, so the
        merge tags it ``both`` (review focus 3). The others take the
        ``graphrag.evidence_chunks`` slots in block order
        (:meth:`_retrieve_evidence_chunks`). Lever 0 is an empty arm, the
        two-arm merge of before.
        """
        if self.levers.evidence_chunks <= 0:
            return []
        cited = cited_chunk_ids(graph_context)
        hybrid = {c["chunk_id"]: c for c in vector_chunks}
        reserved = set(_reserved_hybrid_ids(list(hybrid), self.levers.vector_reserve, limit))
        fetched = {
            c["chunk_id"]: c
            for c in await self._retrieve_evidence_chunks(
                [cid for cid in cited if cid not in reserved], source_ids
            )
        }
        return [
            hybrid[cid] if cid in reserved else fetched[cid]
            for cid in cited
            if cid in reserved or cid in fetched
        ]

    # ------------------------------------------------------------------
    # Pipeline step 7: Merge and rank via RRF
    # ------------------------------------------------------------------

    def _merge_and_rank(
        self,
        evidence_chunks: list[dict[str, Any]],
        provenance_chunks: list[dict[str, Any]],
        vector_chunks: list[dict[str, Any]],
        limit: int,
        seed_labels: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Merge the three chunk arms via Reciprocal Rank Fusion, evidence first.

        The arms fuse in the order evidence, provenance, hybrid; a chunk in
        several keeps the first arm's copy and is tagged ``both``, one in a
        single arm is tagged with it. The evidence chunks enter the budget by
        right (:func:`_budget_cut`: up to ``limit`` of them, wherever fusion
        ranked them), the hybrid reserve then keeps ``graphrag.vector_reserve``
        hybrid hits in it without ever evicting an evidence chunk (see
        :meth:`_reserve_hybrid_hits`), and sequential chunk aliases are
        assigned. With the evidence arm empty (``graphrag.evidence_chunks``
        0) it is the two-arm merge of before, chunk for chunk.

        Args:
            evidence_chunks: Chunks the graph block cites (:meth:`_evidence_arm`).
            provenance_chunks: Chunks from citation-based retrieval.
            vector_chunks: Chunks from hybrid search.
            limit: Maximum chunks to return.
            seed_labels: The seed entities' labels, for ``rank_chunks_by_seeds``.

        Returns:
            Merged and ranked chunk list.

        """
        arms = (
            ("evidence", evidence_chunks),
            ("provenance", provenance_chunks),
            ("vector", vector_chunks),
        )
        try:
            # rank fusion reads the ranks alone, the arms in this order
            rrf_ranked = reciprocal_rank_fusion(
                *([(c["chunk_id"], c.get("score", 0.0)) for c in chunks] for _, chunks in arms)
            )

            # Build lookup by chunk_id (the first arm wins on duplicates)
            chunk_lookup: dict[str, dict[str, Any]] = {}
            membership: dict[str, list[str]] = {}
            for origin, chunks in arms:
                for chunk in chunks:
                    chunk_lookup.setdefault(chunk["chunk_id"], chunk)
                    found_in = membership.setdefault(chunk["chunk_id"], [])
                    if origin not in found_in:
                        found_in.append(origin)

            merged: list[dict[str, Any]] = []
            for chunk_id, rrf_score in rrf_ranked:
                found = chunk_lookup.get(chunk_id)
                if not found:
                    continue
                found_in = membership[chunk_id]
                merged_chunk = dict(found)
                merged_chunk["retrieval_origin"] = "both" if len(found_in) > 1 else found_in[0]
                merged_chunk["rrf_score"] = rrf_score
                merged.append(merged_chunk)

            kept, rest, entered = _budget_cut(
                merged, limit, {c["chunk_id"] for c in evidence_chunks}
            )
            kept = self._reserve_hybrid_hits(
                kept,
                rest,
                [c["chunk_id"] for c in vector_chunks],
                self.levers.vector_reserve,
                evidence_ids=entered,
            )
            kept = self._hydrate_survivors(kept)
            if self.levers.rank_chunks_by_seeds and seed_labels:
                kept = _rank_by_seed_mentions(kept, seed_labels, self.name_conventions)
            assign_chunk_aliases(kept)
            return kept
        except Exception:
            logger.warning("graphrag_merge_failed", exc_info=True)
            record_retrieval_fault("merge")
            # Fallback: concatenate and deduplicate manually
            seen: set[str] = set()
            fallback: list[dict[str, Any]] = []
            for chunk in [*evidence_chunks, *provenance_chunks, *vector_chunks]:
                if chunk["chunk_id"] not in seen:
                    seen.add(chunk["chunk_id"])
                    fallback.append(chunk)
                if len(fallback) >= limit:
                    break
            fallback = self._hydrate_survivors(fallback)
            assign_chunk_aliases(fallback)
            return fallback

    @staticmethod
    def _reserve_shortfall(
        chunks: list[dict[str, Any]], vector_chunks: list[dict[str, Any]], reserve: int
    ) -> int:
        """How many of the hybrid reserve's chunks a merged chunk list does not hold.

        The reserve's ids are those :meth:`_reserve_hybrid_hits` protected
        for a budget of ``len(chunks)`` (:func:`_reserved_hybrid_ids`); one is
        missing only when no unprotected chunk was left to give way (the
        evidence chunks hold the slots), and the pipeline reports the count
        as ``retrieval_stats["reserve_shortfall"]``.
        """
        present = {c["chunk_id"] for c in chunks}
        reserved = _reserved_hybrid_ids(
            [c["chunk_id"] for c in vector_chunks], reserve, len(chunks)
        )
        return sum(1 for cid in reserved if cid not in present)

    @staticmethod
    def _reserve_hybrid_hits(
        kept: list[dict[str, Any]],
        rest: list[dict[str, Any]],
        hybrid_order: list[str],
        reserve: int,
        evidence_ids: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        """Keep the hybrid arm's best ``reserve`` chunks in the budget, never at an evidence chunk's cost.

        Rank fusion weights the two arms equally, so when the top entities
        cite many chunks the hybrid arm's hits fall off the end of the cut
        and GraphRAG returns a worse chunk set than hybrid search alone
        (measured on Book One, 2026-10-01: 44 questions reached by the
        chunks against 46 for plain hybrid search, and four facts only the
        hybrid arm found). The guarantee is by rank: each of the hybrid
        arm's top ``reserve`` chunks missing from the cut replaces the
        lowest-ranked kept chunk outside that reserve, a provenance-only
        chunk first, then any other (a chunk both arms found but the hybrid
        arm ranked below the reserve). Until 2026-10-06 only provenance-only
        chunks could be replaced, so with ``reserve`` equal to the budget
        the hybrid top-k was still not a subset on 22 of 120 questions
        (audit R5). Now hybrid's top ``min(reserve, len(kept))`` is always
        kept. The reserve is cut to the budget first: with ``chunk_limit`` 8
        and ``vector_reserve`` 10, hybrid's ninth and tenth chunks were
        protected too, so they could hold slots no swap could free and
        hybrid's own top 8 was not inside graphrag's 8 on 17 of 120 benchmark
        questions (audit wave 4). The kept order is otherwise untouched;
        ``reserve`` 0 is plain rank fusion.

        The evidence chunks (``evidence_ids``, the chunks the graph block
        cites that entered by right) are protected like the reserve: the
        victim is always an unprotected chunk, provenance-only first, then
        any other, never an evidence one. When every kept chunk is protected
        the reserved hit stays out (the evidence keeps the slot; the
        pipeline counts it, :meth:`_reserve_shortfall`).

        Args:
            kept: The fused list cut to the budget, best first.
            rest: The fused list beyond the cut, best first.
            hybrid_order: The hybrid arm's chunk ids, best first.
            reserve: ``graphrag.vector_reserve``.
            evidence_ids: The evidence chunks in ``kept``, which never give way.

        Returns:
            The kept list, with swaps applied.

        """
        result = list(kept)
        reserved = _reserved_hybrid_ids(hybrid_order, reserve, len(kept))
        protected = set(reserved) | set(evidence_ids)
        present = {c["chunk_id"] for c in result}
        beyond = {c["chunk_id"]: c for c in rest}

        def victim() -> int | None:
            """Index of the lowest-ranked unprotected kept chunk, provenance-only first."""
            outside = [i for i in range(len(result)) if result[i]["chunk_id"] not in protected]
            provenance_only = [
                i for i in outside if result[i].get("retrieval_origin") == "provenance"
            ]
            pool = provenance_only or outside
            return pool[-1] if pool else None

        for chunk_id in reserved:
            if chunk_id in present:
                continue
            chunk = beyond.get(chunk_id)
            if chunk is None:
                continue
            lowest = victim()
            if lowest is None:
                break
            del result[lowest]
            result.append(chunk)
            present.add(chunk_id)
        return result

    # ------------------------------------------------------------------
    # The hybrid arm alone (the benchmark's chunks-only baseline)
    # ------------------------------------------------------------------

    async def hybrid_retrieval(
        self,
        query: str,
        limit: int | None = None,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """The hybrid (semantic + keyword) arm's top ``limit`` chunks, without the graph, with its stats.

        What ``graphrag_search`` would merge from the hybrid side, alone:
        hydrated, headed ``[CHUNK Cn | file]`` and aliased from ``C0``, so
        they render exactly as the full pipeline's chunks do. The benchmark's
        ``hybrid`` retrieval mode and the retrieval-mode tool use it as the
        plain-RAG baseline over the product's own chunks.

        Guarded like the full pipeline: a fault collector is open around the
        search, and every stage that swallowed an exception (the keyword
        arm's ``keyword_search:chunk``, the vector arm, the hybrid search
        itself) is named in ``retrieval_stats["faults"]``, which
        :func:`~chaoscypher_core.benchmark.retrieval.require_complete_retrieval`
        refuses. Until 2026-10-06 the hybrid rung opened no collector and
        was checked by its chunk count alone: with the chunk keyword index
        dropped, all 120 benchmark retrievals were accepted, every one
        changed (audit wave 3).

        Args:
            query: User search query.
            limit: Number of chunks to return; ``graphrag.chunk_limit`` when None.
            source_ids: Optional source scope filter.

        Returns:
            ``{"success", "chunks", "retrieval_stats", "query"}``: up to ``limit``
            chunk dicts, best first; the stats' ``mode`` is ``"hybrid"``.
        """
        if limit is None:
            limit = self._graphrag.chunk_limit
        query = normalize_query(query)
        with collect_retrieval_faults() as faults:
            self._check_index_ready()
            found = await self._retrieve_vector_chunks(query, limit, source_ids)
        top = [dict(c) for c in found][:limit]
        assign_chunk_aliases(top)
        if faults:
            logger.warning("hybrid_search_degraded", query=query, faults=list(faults))
        return {
            "success": True,
            "chunks": top,
            "retrieval_stats": {
                "mode": HYBRID_MODE,
                "vector_chunks": len(found),
                "total_chunks_returned": len(top),
                "faults": list(faults),
            },
            "query": query,
        }

    async def hybrid_chunk_search(
        self,
        query: str,
        limit: int | None = None,
        source_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """The chunks of :meth:`hybrid_retrieval` alone, without its stats.

        Kept for callers that render the chunks only. A caller that checks
        the retrieval (a pin build, a live benchmark run) uses
        :meth:`hybrid_retrieval`, whose ``retrieval_stats`` carry the faults.
        """
        result = await self.hybrid_retrieval(query, limit, source_ids)
        chunks: list[dict[str, Any]] = result["chunks"]
        return chunks

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def lever_scope(self) -> Iterator[None]:
        """One scope of a question's shape: the levers the pipeline sets, reset on the way out.

        The pipeline decides the shape after the seeds and sets its levers
        (:attr:`levers`); the evidence markers and the rendering read them
        too, and the scope is reset in a ``finally``, so a failed call never
        leaks its levers into the next.
        """
        scope = _ACTIVE_LEVERS.set(None)
        try:
            yield
        finally:
            _ACTIVE_LEVERS.reset(scope)

    async def retrieve_shaped(
        self,
        query: str,
        *,
        limit: int | None = None,
        seed_limit: int = 10,
        source_ids: list[str] | None = None,
        router: str | None = None,
    ) -> dict[str, Any]:
        """The retrieval pipeline's result in its shape, annotated, without ``context_text``.

        What :meth:`graphrag_search` renders for the chat model and what the
        memory tools render in their own shapes (spec 2026-10-20 decision 4):
        one pipeline call, one lever scope (:meth:`lever_scope`), the
        evidence and the markers annotated here. ``router`` overrides
        ``GraphRAGSettings.router`` for this call alone (``rules`` makes no
        chat call). The caller that renders must do so inside its own scope,
        as :meth:`graphrag_search` does, since the levers are read then too.
        """
        result = await self._graphrag_search_pipeline(
            query, limit=limit, seed_limit=seed_limit, source_ids=source_ids, router=router
        )
        if isinstance(result, dict) and result.get("success", True):
            if self.levers.relationship_evidence:
                _annotate_evidence(result, self.name_conventions)
            annotate_markers(result)  # the block rows' and hops' sentences, final aliases
        return result

    async def graphrag_search(
        self,
        query: str,
        limit: int | None = None,
        seed_limit: int = 10,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """The retrieval pipeline (:meth:`retrieve_shaped`) plus ``context_text``.

        ``limit`` (the chunk budget) defaults to ``graphrag.chunk_limit``.

        ``context_text`` is the one rendering of the result a chat model reads
        (:func:`~chaoscypher_core.services.workflows.tools.engine.context_render.render_retrieved_context`):
        the app's chat loop hands it to the model as the tool result and the
        benchmark prompts with it, so both see the same graph.

        The call is one scope of the question's shape (:meth:`lever_scope`):
        the rendering reads the shape's levers, so it happens inside it.

        The query is composed first
        (:func:`~chaoscypher_core.utils.query_text.normalize_query`), as
        every query entry point does: its words, its keywords and its
        embedding are then those of the stored (NFC) spelling (audit wave 5).
        """
        query = normalize_query(query)
        with self.lever_scope():
            result = await self.retrieve_shaped(
                query, limit=limit, seed_limit=seed_limit, source_ids=source_ids
            )
            if isinstance(result, dict) and result.get("success", True):
                levers = self.levers
                result["context_text"] = render_retrieved_context(
                    result,
                    explain=levers.explain_lines,
                    graph_last=levers.graph_block_last,
                )
        return result

    async def _graphrag_search_pipeline(
        self,
        query: str,
        limit: int | None = None,
        seed_limit: int = 10,
        source_ids: list[str] | None = None,
        router: str | None = None,
    ) -> dict[str, Any]:
        """Execute the full GraphRAG retrieval pipeline.

        The pipeline degrades gracefully:

        - **full_graphrag**: Embedding + seed entities + PPR + provenance + vector.
        - **vector_only**: Embedding succeeded but no graph seeds found.
        - **keyword_only**: Embedding failed; falls back to keyword search.

        Every stage that swallowed an exception on the way (here or in the
        search repository) is named in ``retrieval_stats["faults"]``
        (:mod:`chaoscypher_core.utils.retrieval_faults`), so a degraded
        retrieval is visible even when it still filled its budget.

        Args:
            query: User search query.
            limit: Maximum number of chunks to return; the shape's
                ``chunk_limit`` (:attr:`levers`) when None.
            seed_limit: Maximum seed entities for PPR.
            source_ids: Optional source scope filter.
            router: A router mode for this call alone (``rules``: no chat call); None: the settings'.

        Returns:
            Dict with ``success``, ``graph_context``, ``chunks``,
            ``retrieval_stats``, ``shape`` and ``query`` keys.

        """
        with collect_retrieval_faults() as faults:
            self._check_index_ready()
            result = await self._pipeline_steps(query, limit, seed_limit, source_ids, router)
        result["retrieval_stats"]["faults"] = list(faults)
        if faults:
            logger.warning("graphrag_search_degraded", query=query, faults=list(faults))
        return result

    async def _pipeline_steps(
        self,
        query: str,
        limit: int | None,
        seed_limit: int,
        source_ids: list[str] | None,
        router: str | None = None,
    ) -> dict[str, Any]:
        """The pipeline's steps, in order (see :meth:`_graphrag_search_pipeline`).

        Step 2b sets the shape's levers for the rest of the call
        (:attr:`levers`); :meth:`graphrag_search` owns that scope and resets it.
        """
        stats: dict[str, Any] = {
            "mode": "keyword_only",
            "seed_entities_found": 0,
            "ppr_entities_explored": 0,
            "provenance_chunks": 0,
            "vector_chunks": 0,
            "evidence_chunks": 0,
            "evidence_left_out": 0,
            "reserve_shortfall": 0,
            "graph_listing_capped": 0,
            "descriptions_dropped": 0,
            "total_chunks_returned": 0,
            "deduplicated": 0,
        }

        # Step 1: Embed query
        embedding = await self._embed_query(query)

        # Step 2: Match seed entities
        seeds: list[tuple[str, float]] = []
        if embedding:
            seeds = self._match_seed_entities(embedding, seed_limit, source_ids)
        if self._graphrag.keyword_seeds:
            seeds = self._add_keyword_seeds(query, seeds, seed_limit, source_ids)
        stats["seed_entities_found"] = len(seeds)

        # Step 2b: the question's shape (rules, then the model when they abstain); its levers
        # apply from the graph context on (the caller, graphrag_search, resets them)
        decision = await self._decide_shape(query, seeds, router=router)
        stats["shape"] = decision.shape.value
        stats["shape_reason"] = decision.reason
        stats["shape_source"] = decision.source
        if decision.shape == QuestionShape.GLOBAL:  # spec § 4.2 step 5: the lookup, on its bundle
            _note_shape_reason(stats, "no community block until phase B")
        _ACTIVE_LEVERS.set(self._shape_levers(decision.shape.value))
        if limit is None:
            limit = self.levers.chunk_limit
        stats["chunk_limit"] = limit

        # Steps 3-4: PPR + graph context (only if seeds found)
        graph_context: dict[str, Any] = {}
        ppr_top_ids: list[str] = []
        if seeds:
            # the whole graph, loaded once: PageRank's and the walk's
            graph_nodes, graph_edges = self._load_graph(source_ids, stats)
            ppr_scores = self._run_ppr(seeds, graph_nodes, graph_edges, source_ids)
            if ppr_scores:
                graph_context = self._assemble_graph_context(
                    ppr_scores,
                    seeds,
                    query=query,
                    source_ids=source_ids,
                    shape=decision.shape,
                    relations=self._question_relations(query),
                    graph=(graph_nodes, graph_edges),
                    stats=stats,
                )
                ppr_top_ids = _provenance_lookup_ids(graph_context, ppr_scores)
                stats["mode"] = "full_graphrag"
                stats["ppr_entities_explored"] = len(ppr_scores)

        # Step 5: Provenance chunks (if we have graph entities)
        provenance_chunks: list[dict[str, Any]] = []
        if ppr_top_ids and self.source_storage and self.indexing:
            provenance_chunks = await self._retrieve_provenance_chunks(ppr_top_ids, source_ids)
            stats["provenance_chunks"] = len(provenance_chunks)

        # Step 6: Vector/hybrid chunks
        vector_chunks = await self._retrieve_vector_chunks(query, limit, source_ids, stats)
        stats["vector_chunks"] = len(vector_chunks)

        # Step 6b: the evidence arm, the chunks the graph block cites (after the hybrid
        # arm: a cited chunk its reserve holds costs no evidence slot)
        evidence_chunks = await self._evidence_arm(graph_context, vector_chunks, limit, source_ids)

        # Step 7: Merge
        if evidence_chunks or provenance_chunks or vector_chunks:
            seed_labels = [e["label"] for e in graph_context.get("seed_entities", [])]
            chunks = self._merge_and_rank(
                evidence_chunks, provenance_chunks, vector_chunks, limit, seed_labels=seed_labels
            )
        else:
            chunks = []
        # nothing silent: the cited chunks left out of the context (past the lever, unread,
        # out of scope or past the budget) and the reserved hybrid hits the evidence displaced
        final_ids = {c["chunk_id"] for c in chunks}
        stats["evidence_chunks"] = sum(c["chunk_id"] in final_ids for c in evidence_chunks)
        stats["evidence_left_out"] = sum(
            cid not in final_ids for cid in cited_chunk_ids(graph_context)
        )
        stats["reserve_shortfall"] = self._reserve_shortfall(
            chunks, vector_chunks, self.levers.vector_reserve
        )

        # Determine final mode
        if not seeds:
            stats["mode"] = "vector_only" if embedding else "keyword_only"
        elif not ppr_top_ids:
            stats["mode"] = "vector_only"

        stats["total_chunks_returned"] = len(chunks)
        stats["deduplicated"] = (
            len(evidence_chunks) + len(provenance_chunks) + len(vector_chunks)
        ) - len(chunks)

        logger.info(
            "graphrag_search_completed",
            query=query,
            mode=stats["mode"],
            seeds=stats["seed_entities_found"],
            chunks=stats["total_chunks_returned"],
        )

        return {
            "success": True,
            "graph_context": graph_context,
            "chunks": chunks,
            "retrieval_stats": stats,
            "shape": stats["shape"],
            "query": query,
        }

    def _shape_levers(self, shape: str) -> GraphRAGSettings:
        """The settings the rest of the call runs with: the shape's bundle on the base, or the base.

        A bundle the settings model rejects when applied (a value out of a
        field's range; the names were checked when configured) records the
        retrieval fault ``shape_levers`` and the call runs on the base
        settings: a bad bundle never fails a retrieval mid-call.
        """
        try:
            return apply_shape_levers(self._graphrag, shape)
        except Exception:
            logger.warning("graphrag_shape_levers_rejected", shape=shape, exc_info=True)
            record_retrieval_fault("shape_levers")
            return self._graphrag

    async def _decide_shape(
        self, query: str, seeds: list[tuple[str, float]], *, router: str | None = None
    ) -> ShapeDecision:
        """The question's shape: the rules over its named seeds and relation words, then the router.

        ``router`` overrides ``GraphRAGSettings.router`` for this call.

        The rules (:func:`classify_by_rules`) count the seeds the question
        names (:meth:`_named_seed_count`) and the relations its words name
        (:meth:`_question_relations`); :func:`route` applies
        ``GraphRAGSettings.router`` and asks the chat model (:meth:`_router_chat`)
        when the mode says so, recording the ``router`` fault when it fails.
        Seed nodes that cannot be read count as none named and record
        ``shape_seeds``.
        """
        try:
            nodes = (
                self.graph.get_nodes_batch([nid for nid, _ in seeds], include_embedding=False)
                if seeds
                else []
            )
        except Exception:
            logger.warning("graphrag_shape_seeds_unreadable", exc_info=True)
            record_retrieval_fault("shape_seeds")
            nodes = []
        rules = classify_by_rules(
            query,
            named_seeds=self._named_seed_count(query, nodes),
            relations=self._question_relations(query),
        )
        return await route(
            query, mode=router or self._graphrag.router, rules=rules, chat=self._router_chat()
        )
