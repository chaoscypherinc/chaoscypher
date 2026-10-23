# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""What the extraction prompts must never say, checked on the prompts as sent.

Two guards from the 2026-10-06 benchmark pipeline audit:

- E7: the relationship prompt asked for "at least N relationships" for N
  entities and licensed relationships "strongly implied", against its own rule
  "do NOT infer"; a quota makes the model invent edges to meet it.
- E9: the prompt examples and the literary domain's examples came from War and
  Peace, the benchmark's own corpus, so the fixture's answers were in the
  prompt. Examples come from an invented text instead.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from chaoscypher_core.services.sources.engine.extraction.domains.registry import DomainRegistry
from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
from chaoscypher_core.services.sources.engine.extraction.extraction_plan import prompt_inputs
from chaoscypher_core.services.sources.engine.extraction.utils import prompts
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    AIEntityExtractor,
    render_plan_prompts,
)
from chaoscypher_core.settings import EngineSettings


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
        ConfigurableDomain,
    )


_LITERARY = (
    Path(__file__).resolve().parents[7]
    / "src/chaoscypher_core/services/sources/engine/extraction/domains/plugins/literary.jsonld"
)

CORPUS_NAMES = (
    "Andrei", "Andrew", "Bolkónski", "Bolkonsky", "Napoleon", "Rousseau", "Contrat Social",
    "Pierre", "Bezúkhov", "Rostóv", "Natásha", "Kutúzov", "Schérer", "Anna Pávlovna", "Moscow",
    "Petersburg", "Austerlitz", "Bagration", "War and Peace", "war_and_peace", "Tolstoy",
    "Hélène", "Sónya", "Véra", "Borís", "Kurágin", "Anatole", "Lise", "Vasíli", "Grande Armée",
)  # fmt: skip
"""War and Peace names (Book One's people and places, the title and its file) no prompt may carry.

Matched at the start of a word (:func:`_corpus_names_in`): "Rostóva" carries "Rostóv", while
"several" does not carry "Véra" nor "realise" "Lise"."""

CHUNK = (
    "Captain Orsolya Darnell walked the harbour wall at dusk. "
    "The lamplighter Bram Darnell was her father. "
    "Margrave Oswin Kell camped beyond the marsh."
)


def _fold(text: str) -> str:
    """Lowercase without accents, so "Bolkonski" and "Bolkónski" are one name."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c)
    )


def _corpus_names_in(text: str) -> list[str]:
    """The :data:`CORPUS_NAMES` ``text`` writes, folded, each at the start of a word.

    The one scan every corpus guard here reads (the rendered prompts, the chat prompts and tool
    descriptions, the literary domain).
    """
    folded = _fold(text)
    return [
        name
        for name in CORPUS_NAMES
        if re.search(r"(?<![a-z0-9])" + re.escape(_fold(name)), folded)
    ]


def _literary() -> DomainStack:
    """The floor plus the shipped literary pack."""
    return DomainStack.resolve(DomainRegistry(), ["literary"])


def _uncapped() -> EngineSettings:
    """The default settings with the examples uncapped, so every example a member carries renders."""
    settings = EngineSettings()
    return settings.model_copy(
        update={"llm": settings.llm.model_copy(update={"extraction_examples_max_chars": 100_000})}
    )


def _rendered_prompts() -> dict[str, str]:
    """Every extraction prompt as sent for the floor plus literary, examples on and uncapped.

    The plan's prompt inputs (guidance and exclusions included: a quota or a
    licence to infer hidden in literary's relationship guidance was found there
    on 2026-10-06) through the extractor's one renderer.
    """
    settings = _uncapped()
    rendered = AIEntityExtractor(settings=settings).render_chunk_prompts(
        prompt_inputs(_literary(), settings),
        CHUNK,
        [{"name": "Orsolya Darnell", "type": "Person"}],
    )
    return {
        "system": rendered.system_prompt,
        "entity": rendered.entity_prompt,
        "relationship": rendered.relationship_prompt,
        "rules": prompts.EXTRACTION_RULES_TEMPLATE,
    }


def test_the_rendered_examples_are_the_literary_domains() -> None:
    """The guard reads the domain examples: they render into the prompts it checks."""
    rendered = _rendered_prompts()
    assert "Lady Maren Ashby" in rendered["entity"]
    assert "Corvin Lusk" in rendered["relationship"]
    assert "Pack literary:" in rendered["entity"] and "Pack literary:" in rendered["relationship"]


@pytest.mark.parametrize("which", ["system", "entity", "relationship", "rules"])
def test_no_prompt_sets_a_relationship_quota_or_licenses_inference(which: str) -> None:
    """No "at least N", no "implied": a relationship is one a cited sentence states."""
    text = _fold(_rendered_prompts()[which])
    assert "at least" not in text
    assert "implied" not in text
    assert "aim for at least" not in text


def test_the_relationship_prompt_keeps_its_evidence_requirement() -> None:
    """Removing the quota keeps the rule that every R| cites its sentence."""
    text = _rendered_prompts()["relationship"]
    assert "do NOT infer" in text
    assert "Every R| MUST include sent_ref" in text


@pytest.mark.parametrize("which", ["system", "entity", "relationship", "rules"])
def test_no_prompt_carries_the_benchmark_corpus(which: str) -> None:
    """The fixture's corpus (War and Peace) is not in any prompt as sent."""
    assert _corpus_names_in(_rendered_prompts()[which]) == []


@pytest.mark.parametrize(
    "which", ["policy", "benchmark chat prompt", "chat system prompt", "chat tool schemas"]
)
def test_the_chat_policy_and_prompt_carry_no_corpus_name(which: str) -> None:
    """The chat's prompt text carries no corpus name either.

    The grounded answer policy and the benchmark's chat prompt (2026-10-12: the policy gained two
    bullets written from a read of the fixture's rows); the app's chat system prompt and the tool
    descriptions sent beside it (final review 2 of 2026-10-12, M3: they named the corpus's cast
    and its file).
    """
    from chaoscypher_core.benchmark.chat_prompt import grounded_chat_prompt
    from chaoscypher_core.services.chat.engine.constants import (
        GROUNDED_ANSWER_POLICY,
        SYSTEM_PROMPT,
    )
    from chaoscypher_core.services.workflows.tools.engine.schema_registry import TOOL_SCHEMAS

    text = {
        "policy": GROUNDED_ANSWER_POLICY,
        "benchmark chat prompt": grounded_chat_prompt("<question>", "<context>"),
        "chat system prompt": SYSTEM_PROMPT,
        "chat tool schemas": json.dumps(TOOL_SCHEMAS, ensure_ascii=False),
    }[which]
    assert _corpus_names_in(text) == []


def test_the_corpus_scan_matches_at_a_word_start_only() -> None:
    """A corpus name inside another word is no hit; a name with an ending still is."""
    assert _corpus_names_in("several realised it") == []
    assert _corpus_names_in("Countess Rostova's ball") == ["Rostóv"]


def test_the_literary_domain_carries_no_corpus_name() -> None:
    """No part of the literary domain (examples, template descriptions, guidance) names War and Peace."""
    domain_text = json.dumps(json.loads(_LITERARY.read_text("utf-8")), ensure_ascii=False)
    assert _corpus_names_in(domain_text) == []


def test_no_domain_tells_the_extractor_to_skip_a_bare_title() -> None:
    """A person called only by a title is a reference to keep and fold, never a non-entity.

    Until 2026-10-07 the literary domain's "Bare titles without names" rule
    ("The Count") made its extractor skip ``the count``, and with it the
    Archives fact a v4 benchmark question needs; the generic arm wrote it.
    The last three (biographical "the father", historical "the king",
    theological "the Lord") went with the suppression audit the same day.
    """
    from chaoscypher_core.services.sources.engine.deduplication import name_identity as ni
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry

    conventions = ni.DEFAULT_NAME_CONVENTIONS
    registry = get_domain_registry(EngineSettings())
    found = set()
    for info in registry.list_domain_info():
        name = str(info["name"])
        for rule in registry.get_domain(name).get_entity_exclusions() or []:
            for example in rule.examples:
                titles = set(ni.fold(example).split()) & conventions.gendered_titles
                if titles and ni.reference_key(example, conventions, "person"):
                    found.add((name, example.lower()))
    assert found == set()


# --- keep, flag, never delete: what the prompt tells the model not to write ------------
#
# The prompt is the one deletion site no counter sees: a record the model is told
# not to write never reaches the pipeline that keeps and flags. Audit 2026-10-07
# (internal/TODO.md, "Prompt instructions that suppress records"): an exclusion
# or a rule may suppress only what carries no fact (junk: pronouns, page
# furniture, self-references to the document) or constrain how a record is
# written (format); one that stops a record carrying a fact is removed.

FORBIDDEN_PHRASINGS: tuple[tuple[str, str], ...] = (
    (r"\bbare titles?\b", "a bare title is a reference the pipeline keeps (TITLES rule)"),
    (r"\b(?:generic|occupational) (?:role|famil\w*|party|occupation\w*)", "a role word"),
    (r"\brole (?:words?|references?)\b", "a role word"),
    (r"\bwithout (?:a |their )?names?\b", "a person the passage does not name"),
    (r"\bunnamed\b", "a person the passage does not name"),
    (r"\bminor\b", "minor characters carry facts"),
    (r"\bonly the main\b", "only the main ..."),
    (r"\bunless\b", "do not extract X unless Y"),
    (r"\bat most \d+", "a limit on how many"),
    (r"\bmore than \d+", "a limit on how many"),
    (r"\b(?:expect roughly|aim for \d+\s*-\s*\d+|then stop|over-extract)", "a limit on how many"),
    (r"\blimit (?:the |structural )?(?:relationships|entities|properties|aliases)\b",
     "a limit on how many"),
    (r"\bquality over quantity\b", "a limit on how many"),
    (r"\bavoid (?:creating|extracting|writing) (?:too )?many\b", "a limit on how many"),
    (r"\bskip (?:it|them)\b", "an entity the model is told to leave out"),
    (r"\bonly extract entities\b|\bnamed entities only\b", "an entity without a proper name"),
    (r"\bsignificant\b", "a threshold the model applies before writing"),
)  # fmt: skip
"""Phrasings of an instruction that suppresses a record carrying a fact, with why.

Checked on everything the prompts of the floor and of the floor plus each
shipped pack render (the type lines' and labels' descriptions, guidance,
exclusions, examples: :func:`_stack_texts`), on every subtype's description,
on every plugin's guidance and example notes, and on the extraction
templates. Extend it when a new phrasing of the same kind is found."""

ALLOWED_EXCLUSIONS = frozenset({
    # junk: no referent, no fact
    'Pronouns and vague references: "he", "she", "they", "it", "someone"',
    'Single common words without specific meaning: "the thing", "the item"',
    'Source citations and footnote markers: "[1]", "(ibid.)"',
    'Vague time references: "in his youth", "later in life", "during this period"',
    'Generic security boilerplate: "stay safe online", "update your software", "best practices"',
    'Generic design references without specifics: "the button", "the user", "the designer", '
    '"the interface", "the screen"',
    'Figure and chapter markers: "fig. 1", "figure 2", "see chapter 3", "as shown above"',
    'Instructional boilerplate: "In this chapter you will learn", "Key takeaways"',
    'Generic student references: "the student", "learners", "you"',
    'Boilerplate disclaimers: "past performance", "not financial advice", '
    '"forward-looking statements"',
    'Vague date references: "at that time", "in those days", "later"',
    'Generic intelligence boilerplate: "this document", "the report", "classified information", '
    '"intelligence community"',
    'Standalone redaction markers without context: "[REDACTED]", "(b)(1)", "(b)(3)", "DELETED"',
    'Form instructions: "check one", "sign below", "see reverse", "circle applicable"',
    'Procedural boilerplate: "hereby", "whereas", "notwithstanding", "witnesseth"',
    'Citation formatting: "Id.", "supra", "infra", "op. cit."',
    'Furniture/atmosphere: "the door", "the room", "the night", "the darkness"',
    'Generic measurements: "normal range", "elevated levels", "within limits"',
    'Study references: "Figure 1", "Table 2", "Reference [15]"',
    'Editorial commentary: "it is worth noting", "importantly"',
    'Wire service boilerplate: "CITY (AP) -"',
    'Editorial markers: "breaking", "developing", "update", "exclusive"',
    'Section markers: "Chapter 1", "Part II", "Section 3"',
    'Footnote references: "[1]", "(ibid.)", "op. cit."',
    'Generic academic terms: "this paper", "the author", "as mentioned above"',
    'Vague references: "some philosophers", "it is argued that"',
    'Generic doc references: "the document", "this specification", "this manual", '
    '"the standard", "the reference"',
    'Boilerplate page furniture: "page 1 of 10", "see footnote", "table of contents", '
    '"copyright notice"',
    'Figure and table references: "Figure 1", "Table S2", "Supplementary Figure S3"',
    'Generic labels: "this study", "the results", "our findings", "the data"',
    'Citation markers: "[1]", "(Smith et al., 2020)", "et al."',
    'Generic variable names: "x", "temp", "result", "data", "i", "j"',
    'Chapter:verse references: "John 3:16", "Quran 2:255", "Psalm 23:1"',
    'Liturgical responses: "Amen", "Hallelujah", "Peace be upon him"',
    # format: a value is a property of the entity it belongs to, not an entity
    'Bare date ranges without context: "1920-1985", "1800s"',
    'Standalone CSS values: "16px", "1rem", "100%", "0 auto"',
    'Raw numbers alone: "$5M", "15%", "Q3 2024"',
    'Dosage numbers alone: "500mg", "2x daily", "10ml"',
    'Standalone normative keywords without context: "MUST", "SHALL", "SHOULD", "MAY", "REQUIRED"',
    'Statistical notation as entities: "p < 0.05", "n = 100", "CI 95%"',
})  # fmt: skip
"""Every entity exclusion a shipped plugin may carry, by its exact prompt text (junk or format).

A new exclusion fails the guard until it is reviewed against the keep rule and added."""


def _shipped_domains() -> list[tuple[str, ConfigurableDomain]]:
    registry = DomainRegistry()
    return [
        (name, cast("ConfigurableDomain", registry.get_domain(name))) for name in registry.names()
    ]


def _suppressions(text: str) -> list[str]:
    return [
        f"{why}: {match.group(0)!r}"
        for pattern, why in FORBIDDEN_PHRASINGS
        for match in re.finditer(pattern, text, re.IGNORECASE)
    ]


def _stack_texts(packs: list[str]) -> dict[str, str]:
    """What the model reads from the floor plus ``packs``: both prompts rendered, and each subtype's description.

    The plan's prompt inputs through the one renderer
    (:func:`render_plan_prompts`, examples uncapped), so a node or edge
    template description, a subtype key, a guidance line, an exclusion or an
    example is scanned as it is sent; the subtypes' descriptions, which the
    type lines list by key, beside them. Until 2026-10-08 the guard read
    hand-picked fields and missed the floor's Event line ("A significant
    occurrence"), which every prompt renders.
    """
    stack = DomainStack.resolve(DomainRegistry(), packs)
    pass1, pass2 = render_plan_prompts(
        prompt_inputs(stack, _uncapped()), numbered_text="", entity_list="", max_entity_index=0
    )
    texts = {"pass1": pass1, "pass2": pass2}
    texts.update({f"subtype {s.name}": s.description or "" for s in stack.subtypes()})
    return texts


_PACKS = sorted(name for name in DomainRegistry().names() if name != "generic")


@pytest.mark.parametrize("packs", [[], *([p] for p in _PACKS)], ids=["floor", *_PACKS])
def test_no_rendered_prompt_suppresses_a_record_that_carries_a_fact(packs: list[str]) -> None:
    """Everything the prompts render, and every subtype's description: no forbidden phrasing."""
    hits = {where: _suppressions(text) for where, text in _stack_texts(packs).items()}
    assert {where: found for where, found in hits.items() if found} == {}


def test_the_rendered_scan_reads_the_type_lines_and_the_label_lines() -> None:
    """The scan sees the node and edge template descriptions and the subtypes', as sent."""
    texts = _stack_texts(["literary"])
    assert "- Event: An occurrence, incident, or milestone" in texts["pass1"]
    assert "- possesses: Character possesses or owns" in texts["pass2"]
    assert "subtype Character" in texts


def test_no_shipped_exclusion_suppresses_a_record_that_carries_a_fact() -> None:
    """Every shipped entity exclusion is an allow-listed junk or format rule, and each is used."""
    used = set()
    unreviewed = []
    for name, domain in _shipped_domains():
        for rule in domain.get_entity_exclusions():
            text = rule.as_prompt_text()
            used.add(text)
            if text not in ALLOWED_EXCLUSIONS:
                unreviewed.append((name, text))
    assert unreviewed == []
    assert ALLOWED_EXCLUSIONS - used == set()  # the allow-list holds no stale entry


@pytest.mark.parametrize("name", sorted(DomainRegistry().names()))
def test_no_shipped_guidance_suppresses_a_record_that_carries_a_fact(name: str) -> None:
    """No guidance line or example note of a shipped plugin is a forbidden phrasing."""
    domain = dict(_shipped_domains())[name]
    texts = [domain.get_entity_guidance() or "", domain.get_relationship_guidance() or ""]
    examples = domain.get_examples() or {}
    texts += [
        str(item.get("note") or "")
        for items in examples.values()
        if isinstance(items, list)
        for item in items
        if isinstance(item, dict)
    ]
    assert [hit for text in texts for hit in _suppressions(text)] == []


@pytest.mark.parametrize(
    "template",
    [
        "SYSTEM_PROMPT",
        "ENTITY_HARVEST_TEMPLATE",
        "RELATIONSHIP_HARVEST_TEMPLATE",
        "EXTRACTION_RULES_TEMPLATE",
        "EXCLUSIONS_HEADER",
        "STRICT_TYPE_INSTRUCTION",
        "STRICT_SUBTYPE_INSTRUCTION",
    ],
)
def test_no_template_suppresses_a_record_that_carries_a_fact(template: str) -> None:
    assert _suppressions(getattr(prompts, template)) == []


def test_the_strict_type_instruction_renders_from_the_shared_constant() -> None:
    """The rendered prompt carries the constant the guard checks (no second copy in the builder).

    Floor + literary offers subtypes, so its strict line is the subtype one; the
    floor alone renders the core-type one (``prompts.strict_type_instruction``).
    """
    assert prompts.STRICT_SUBTYPE_INSTRUCTION in _rendered_prompts()["entity"]
    assert prompts.STRICT_TYPE_INSTRUCTION in _entity_prompt([])
    assert prompts.EXCLUSIONS_HEADER in _rendered_prompts()["entity"]


_TITLES_HEADER = "TITLES, ROLES AND REFERENCES:"
"""The floor's TITLES rule opens with this header (one line of ``entity_guidance``)."""


def _titles_rule() -> str:
    """The floor's TITLES line: the one shared rule for a person called only by a title."""
    guidance = dict(_shipped_domains())["generic"].get_entity_guidance() or ""
    lines = [line for line in guidance.splitlines() if line.startswith(f"- {_TITLES_HEADER}")]
    assert len(lines) == 1, lines
    return lines[0]


def _entity_prompt(packs: list[str]) -> str:
    settings = EngineSettings()
    return (
        AIEntityExtractor(settings=settings)
        .render_chunk_prompts(
            prompt_inputs(DomainStack.resolve(DomainRegistry(), packs), settings), CHUNK, []
        )
        .entity_prompt
    )


@pytest.mark.parametrize("packs", [[], ["literary"]], ids=["floor", "floor+literary"])
def test_the_prompt_carries_the_titles_rule_once(packs: list[str]) -> None:
    """Floor alone and floor plus literary: the TITLES rule renders once, from the floor."""
    rule = _titles_rule()
    prompt = _entity_prompt(packs)
    assert prompt.count(rule) == 1
    assert prompt.count(_TITLES_HEADER) == 1
    assert "write the phrase as the passage does" in rule


def test_no_pack_carries_its_own_titles_rule() -> None:
    """One shared rule in the floor's guidance, not a copy per pack."""
    for name, domain in _shipped_domains():
        if name != "generic":
            assert "TITLES" not in (domain.get_entity_guidance() or ""), name


@pytest.mark.parametrize("packs", [[], ["literary"]], ids=["floor", "floor+literary"])
def test_pass1_binds_a_known_name_to_the_passage(packs: list[str]) -> None:
    """Spec D8 (the instruction): the passage itself must name a known entity.

    Its name, or a distinctive alias of it, appears in this passage; a title
    or kinship word alone never identifies a KNOWN ENTITY, in the NAME
    rule and in the floor's TITLES rule, whose example passage names its
    entity. The list alone ("If this passage or KNOWN ENTITIES makes clear")
    or a reference alone ("refers to that entity by a title") never licenses
    a known name.
    """
    prompt = " ".join(_entity_prompt(packs).split())  # the NAME rule wraps its lines
    rule = _titles_rule()
    names_it = "this passage itself names that entity"
    distinctive = "(its name, or a distinctive alias of it, appears in this passage)"
    assert prompt.count(names_it) == 2 and names_it in rule
    assert prompt.count(distinctive) == 2 and distinctive in rule
    assert "a title or kinship word alone never identifies a KNOWN ENTITY." in prompt
    assert "A title or kinship word alone (" in rule
    assert "never identifies a KNOWN ENTITY: write the phrase as the passage does." in rule
    assert "makes clear which one" not in prompt
    assert "refers to that entity by a title" not in prompt
    # The example: a passage that writes the name, then the title, joined under the name.
    example = rule.split("write that entity's known name as NAME", 1)[1].split(")", 1)[0]
    assert "Count Oswin Kell" in example.split("NAME", 1)[0]
    assert 'alias "the count"' in example


def test_pass1_rules_name_known_entities_and_the_floor_titles_rule_covers_every_type() -> None:
    """Pass 1's NAME and ALIASES rules name KNOWN ENTITIES; the floor's TITLES rule covers every type."""
    prompt = _entity_prompt([])
    assert "NAME may be a KNOWN ENTITIES name" in prompt
    assert "a name KNOWN ENTITIES gives to someone else" in prompt
    assert (
        "TITLES, ROLES AND REFERENCES:" in prompt
        and "the regiment, the capital, the company" in prompt
    )
    assert "Two entries of KNOWN ENTITIES are never one entity" in prompt
