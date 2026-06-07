# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM prompt templates for entity extraction.

Contains all pipe-delimited format prompt templates used by the
AIEntityExtractor for 2-pass extraction: entities+properties first,
then relationships.

Evidence-gated format specifications (sentence-referenced):
- E|name|type|aliases|confidence|sent_ref|description (entities)
- R|source_index|target_index|type|confidence|sent_ref|justification (relationships)
- P|entity_index|key|value (properties)
"""

# System prompt for extraction
SYSTEM_PROMPT = (
    "You are an expert at extracting structured knowledge from text. "
    "Output ONLY the requested format with no additional text."
)

# ==========================================================================
# PASS 1: ENTITY + PROPERTY HARVEST PROMPT
# ==========================================================================

ENTITY_HARVEST_TEMPLATE = """Extract entities and their properties from the numbered sentences below.

<numbered_sentences>
{numbered_sentences}
</numbered_sentences>

OUTPUT FORMAT:
- E|name|type|aliases|confidence|sent_ref|description
- P|entity_index|key|value

RULES:
1. Output each entity's E| line followed immediately by its P| lines.
2. Only extract facts explicitly stated in the sentences — do NOT infer.
3. Every E| MUST include sent_ref pointing to supporting sentence(s). Valid forms:
   - single: ``S3``
   - range:  ``S2-S5``
   - list:   ``S1, S5`` (comma-separated, for non-adjacent sentences)
   - mixed:  ``S1-S3, S7``
4. P| lines MUST appear immediately after their parent E| line. Use the entity INDEX (0-based from your output order). Do NOT output a second batch of P| lines.

Entity types:
{node_templates}
{strict_type_instruction}

ENTITY GUIDELINES:
- Extract NAMED entities only — each entity MUST have a proper name or specific identifier
- NAME must be a real name from the text, NOT a description or phrase you invented
  GOOD names: "Prince Andrei", "Moscow", "Battle of Austerlitz"
  BAD names: "The emotional state of X", "The relationship between X and Y", "Feelings of sadness"
- Characters known by title+name ("Princess Mary", "Prince Andrew") or recurring titles
  ("the old prince", "the little princess") ARE named entities — extract them using their
  most complete name form (e.g., "Prince Nikolai Bolkonsky" not "the old prince")
- DESCRIPTION: Rich, factual summary (2-3 sentences) covering identity, role, and key attributes from the text
  Aim for 100+ characters. Include nationality, occupation, family ties, and distinguishing traits.
- ALIASES: Only alternate PROPER NAMES for the same individual (semicolon-separated)
  GOOD aliases: "Andrei; Prince Andrew; Andrew Bolkonsky" (name variants of the same person)
  BAD aliases: "Nieces; The soldiers; Friend" (descriptions, roles, group nouns)
  NEVER use descriptions, group references, relationships, or roles as aliases
  Include ALL name variants: formal names, nicknames, diminutives, patronymics, titles with names.
  Aim for 2-4 aliases for major entities.
- PROPERTIES: Extract relevant properties — titles, roles, family positions, occupations,
  nationalities, physical descriptions. Aim for 3-5 properties per major entity, then stop.
  Do NOT repeat the same property key for the same entity.
- Confidence: 1.0 explicit, 0.7-0.9 implied, 0.5-0.6 uncertain
- Do NOT extract pronouns, vague references, structural markers, or generic objects
- Do NOT extract abstract concepts, emotions, states, or actions as entities
  BAD: "The act of being in a state of sorrow", "Feelings of anger", "A moment of reflection"
- Do NOT create entities from descriptions — if it reads like a sentence fragment, it is NOT an entity name
- Expect roughly 5-15 entities per text passage. If you have extracted more than 20, you are likely over-extracting.
{entity_exclusions}
- Only extract entities with PROPER NAMES or specific identifiers

EXAMPLE (3 entities, indices 0-2):
E|Prince Andrei|Character|Andrei; Prince Andrew; Andrew Bolkonsky|0.9|S1-S2|Military officer and nobleman from a prominent Russian aristocratic family, eldest son of old Prince Bolkonsky. Serves in the army and struggles with questions of purpose and glory.
P|0|title|Prince
P|0|occupation|Military Officer
P|0|family_role|Eldest son
P|0|nationality|Russian
E|Napoleon Bonaparte|Character|Napoleon; Emperor Napoleon|1.0|S3|Emperor of France and military commander who led the French forces across Europe. Known for his strategic brilliance and ambition to dominate the continent.
P|1|title|Emperor
P|1|nationality|French
E|Battle of Austerlitz|Event|Austerlitz|1.0|S4|Major battle of the Napoleonic Wars fought in December 1805, also known as the Battle of the Three Emperors. A decisive French victory over Russian and Austrian forces."""

# ==========================================================================
# PASS 2: RELATIONSHIP HARVEST PROMPT
# ==========================================================================

RELATIONSHIP_HARVEST_TEMPLATE = """Extract relationships between the entities listed below, based on the numbered sentences.

<numbered_sentences>
{numbered_sentences}
</numbered_sentences>

ENTITIES (use these index numbers for source and target):
<entity_list>
{entity_list}
</entity_list>

OUTPUT FORMAT:
- R|source_index|target_index|type|confidence|sent_ref|justification

RULES:
1. R| indices MUST be valid (0 to {max_entity_index}). No self-relationships.
2. Only extract facts explicitly stated in the sentences — do NOT infer.
3. Every R| MUST include sent_ref pointing to sentence(s) containing BOTH entities. Valid forms:
   - single: ``S3``
   - range:  ``S2-S5``
   - list:   ``S1, S5`` (comma-separated, for non-adjacent sentences)
   - mixed:  ``S1-S3, S7``

CRITICAL: The entity list above is the COMPLETE and ONLY set of entities.
- Do NOT reference entities you remember from the text that are not in the list above.
- If an entity you expect is missing from the list, skip relationships involving it.
- Only use index numbers 0 through {max_entity_index}. Any index outside this range is INVALID.

Relationship types:
{edge_templates}

RELATIONSHIP GUIDELINES:
- Extract ALL relationships stated or strongly implied between entities — do not skip obvious connections
- Every entity should have at least one relationship. If you have N entities, aim for at least N relationships.
- Prefer specific types (spouse_of, wrote, parent_of) over generic ones (related_to, explores)
- JUSTIFICATION: Write a full sentence (50+ characters) explaining the relationship with evidence from the text

EXAMPLE (using indices from entity list):
R|0|2|participates_in|0.9|S4|Prince Andrei fought in the Battle of Austerlitz as part of the Russian forces against Napoleon
R|1|2|commands|0.8|S4|Napoleon commanded the French forces to a decisive victory at the Battle of Austerlitz
R|0|1|enemy_of|0.8|S3-S4|Prince Andrei fought against Napoleon's forces at Austerlitz, placing them on opposing sides of the conflict"""

# Extraction rules for UI display
EXTRACTION_RULES_TEMPLATE = """Output format: Pipe-delimited lines (evidence-gated)
- E|name|type|aliases|confidence|sent_ref|description
- R|source_index|target_index|type|confidence|sent_ref|justification
- P|entity_index|key|value"""
