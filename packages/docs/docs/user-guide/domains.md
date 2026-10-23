---
id: domains
title: Extraction Domains
description: Every source extracts with the floor — eight core entity types and the core relationships — plus the packs it chooses. 18 built-in packs (literary, legal, medical, scientific, and more) add subtypes, relationships and guidance.
---

# Extraction Domains

Extraction domains configure how the AI extracts entities and relationships from your documents. Every source extracts with the **floor** — the eight core entity types and the core relationship labels — plus the **packs** it chooses. A pack adds to the floor: subtypes of the core types, relationship labels the core lacks, guidance, examples and exclusions for its kind of document. Nothing a pack declares replaces something of the floor's. Each domain is a `.jsonld` file -- **no Python code required**.

:::note[API port]

The `curl` examples on this page use `http://localhost:8080`, which is the API port for the multi-container development stack. The all-in-one container (the primary install) serves the API on port **80** instead — use `http://localhost/api/v1/...` there.

:::

## The Floor

The floor is the built-in `generic` domain. It is always there: a source with no pack extracts with the floor alone.

**Core entity types.** Every node is stored under one of eight types (`graph_nodes.entity_type`):

| Core type | Covers |
|-----------|--------|
| **Person** | An individual human being |
| **Organization** | A company, institution, government body, agency or other formally constituted body |
| **Location** | A place, city, country, region, or geographical area |
| **Event** | An occurrence, incident, or milestone |
| **Work** | A document, book, article, paper, or report — a published or formal piece of writing |
| **Concept** | An idea, theory, principle, or abstract notion |
| **Object** | A physical or digital object, artifact, or thing |
| **Group** | A family, household, social class, faction, crowd or other collective of people without a formal charter or membership |

Anything more specific is a **subtype**, stored on the node as `properties.entity_subtype` (for example `character`, `military_unit`). The extraction prompt lists each core type's subtypes from the selected packs, and the model writes one as `Core/subtype` in the type column (`Person/character`, `Organization/military_unit`); a subtype written under the wrong core type is stored under its own, and one no selected pack offers is kept on the written core type, flagged `unplaced_type`. The interface shows both: an entity chip reads `Person · Character`, and the node takes its core type's colour and template. The commit creates one template per core type a source uses (at most eight), with the floor's icon and colour.

**Core relationship labels.** The floor declares 41 relationship labels — family and personal (`parent_of`, `child_of`, `sibling_of`, `spouse_of`, `friend_of`, `rival_of`, `mentor_of`, ...), membership and work (`member_of`, `leader_of`, `employee_of`, `founder_of`, `owner_of`, `part_of`), place and time (`located_in`, `born_in`, `died_in`, `occurred_at`, `travelled_to`, `preceded_by`, ...), works and ideas (`author_of`, `created_by`, `about`, `describes`, `mentions`, `references`, `influences`, `instance_of`) and others. `related_to` is the fallback when no label fits.

**Limits.** The floor's relationship caps are `max_entity_degree` 80 and `max_same_source_type` 20. A pack may raise a limit, never lower one.

## Packs

A pack is a domain with `"role": "pack"`. The 18 built-in packs:

| Pack | For | Subtypes | Extra labels | Filtering preset |
|------|-----|----------|:------------:|------------------|
| **biographical** | Biographies, memoirs, personal histories, and biographical documents | 11 (e.g. Life Event, Achievement, Personal Relationship, Institution) | 17 | `lenient` |
| **cybersecurity** | Cybersecurity, threat intelligence, vulnerability research, incident response, and information security | 20 (e.g. Threat Actor, Malware, Vulnerability, Attack Technique) | 35 | `strict` |
| **design** | UI/UX design books, design systems, typography, color theory, accessibility guidelines, interaction design | 15 (e.g. Designer, Design System, Component, Design Principle) | 21 | `balanced` |
| **educational** | Educational materials, textbooks, courses, and instructional content | 24 (e.g. Learning Objective, Prerequisite, Lesson, Module) | 23 | `balanced` |
| **financial** | Financial documents, market analysis, investment, and economic reports | 19 (e.g. Financial Instrument, Company, Market, Transaction) | 29 | `balanced` |
| **historical** | Historical documents, narratives, primary sources, and historiography | 19 (e.g. Historical Event, Historical Figure, Era, Institution) | 21 | `lenient` |
| **intelligence** | Declassified intelligence documents, FOIA releases, briefings, agency cables, dossiers, UAP/UFO reports | 14 (e.g. Witness, Source, Human Source, Agency) | 24 | `strict` |
| **investigation** | Criminal and civil investigations: case files, evidence, witness statements, forensic reports | 10 (e.g. Evidence, Crime Scene, Forensic Report, Legal Filing) | 21 | `strict` |
| **legal** | Legal documents, court cases, statutes, contracts, and regulations | 18 (e.g. Statute, Case, Legal Doctrine, Court) | 20 | `strict` |
| **literary** | Literary texts, fiction, poetry, drama, and literary criticism | 16 (e.g. Literary Work, Author, Structural Unit, Character) | 38 | the floor's |
| **medical** | Medical and clinical documents, healthcare literature, and pharmaceutical information | 20 (e.g. Condition, Symptom, Treatment, Drug) | 19 | `strict` |
| **news** | News articles, journalism, press releases, and current events | 11 (e.g. News Event, Quote, Source, Date) | 22 | `minimal` |
| **philosophical** | Philosophical texts from global traditions (Western, Eastern, African, Islamic, Indigenous) | 18 (e.g. Philosopher, Philosophical Tradition, School of Thought, Philosophical Concept) | 30 | `balanced` |
| **political** | Political documents, governance, policy, and political theory | 17 (e.g. Political Entity, Institution, Political Figure, Political Party) | 19 | `balanced` |
| **reference** | Standards (RFC / ISO / IEEE / W3C), specifications, API references, manuals, configuration guides, release notes | 15 (e.g. Standard, Standards Body, Specification, Section) | 22 | `balanced` |
| **scientific** | Scientific research papers, studies, experiments, and academic publications | 22 (e.g. Hypothesis, Experiment, Method, Finding) | 22 | `strict` |
| **technical** | Technical documentation, code, APIs, and software libraries | 18 (e.g. Module, Class, Function, Method) | 21 | `balanced` |
| **theological** | Religious texts, theology, sacred writings, and spiritual traditions | 18 (e.g. Deity, Prophet, Sacred Text, Doctrine) | 28 | `balanced` |

**Several packs on one source.** A source may use more than one pack (for example `historical` + `literary` for a historical novel; the source shows the label `historical+literary`). The packs stack on the floor:

- the prompt lists each core type once, with every pack's subtypes of it beside it, then the floor's relationship labels and each pack's extra ones (a label once);
- guidance is the floor's, then each pack's under `Pack <name>:`; examples and entity exclusions are the floor's, then each pack's;
- content exclusions are every member's categories and patterns together;
- where two members set the same limit, the larger value holds;
- the filtering preset is the packs' when they name one; packs that name different presets fall back to the floor's (`balanced`).

The more packs, the longer the prompt: the pack chooser shows the prompt size against the model's context window and warns when it would not fit.

### What a pack may add

A pack only adds to the floor. A pack may declare:

- **subtypes** of a core type (`"name": "Character", "of": "Person"`), each with a description, properties, an icon and colour, a quality score, and rescue keywords;
- **extra properties of a core type** (`core_properties`, for example an Event's host and date) and **rescue keywords for a core type** itself (`core_rescue_keywords`, for example `battle` for Event);
- **relationship labels the core lacks**, whose `source_types` and `target_types` are core types;
- **type aliases**: a written name read as one of its subtypes or a core type (`"Historical Figure": "Character"`);
- detection keywords and patterns, entity and relationship guidance, examples, entity exclusions, content exclusions, name conventions (titles, name forms, relation words), quality scores, a filtering preset, and extraction limits at or above the floor's.

A pack may **not** declare a node template, a subtype named like a core type, a core relationship label (such as `created_by`), a limit below the floor's, or `strict_entity_types` (the core types are always strict). A config that does is refused at load and logged as `domain_schema_invalid` with every problem, for example:

```
edge label 'created_by' is a core label; a pack adds labels the core lacks;
extraction_limits.max_entity_degree 15 is below the floor (80); a pack may raise a limit, never lower one
```

## Choosing Packs

### Detection proposes, you confirm

When a document is indexed, detection samples its content (first 5 chunks + 3 chunks from ~40% into the document, up to 12,000 characters total, 1,500 per chunk) and scores each pack:

| Factor | What It Checks | Example |
|--------|---------------|---------|
| **Keywords** | Pack-specific terms in text | "plaintiff", "verdict" for legal |
| **File extensions** | Source file type | `.py` for technical |
| **Doc type** | Metadata-based type hints | "research_paper" for scientific |
| **Regex patterns** | Structural patterns | Birth/death date ranges for biographical |

Every pack whose evidence (its score above its own base score) reaches 0.30 is proposed, highest score first, **at most two**. When no pack clears the bar the proposal is the floor alone, marked low confidence.

Detection is a hint: by default extraction waits until you confirm the packs (see [Confirm the Extraction Packs](sources.md#confirm-the-extraction-packs)).

- **Confirm dialog** — *Confirm extraction packs* shows "Detection proposes X + Y" and the proposed packs as chips, pre-selected. Add or remove packs; no chip is the floor alone. A low-confidence proposal starts with the floor alone selected.
- **Upload dialog** — the *Packs* chooser offers every pack with its description and prompt size. With *Detect packs automatically* on, detection decides (and you confirm later); off, the chips you pick are used and nothing waits for confirmation. With no chip the helper reads *Floor only*.
- **API and CLI** — send `packs` (a list; repeat the form field on uploads) or pass `--pack` once per pack. `domain` is still accepted as a one-element list: `generic` is the floor alone, `__auto__` detects.

### Changing a source's packs

**Change packs…** in a source's actions menu opens the same chooser, seeded with the source's packs. Confirming re-extracts the source with the new packs: a committed source's graph data is deleted first. The same is available as `POST /sources/{id}/reclassify`:

```bash
# Reclassify src_abc123 under two packs (every source also has the floor)
curl -X POST http://localhost:8080/api/v1/sources/src_abc123/reclassify \
  -H "Content-Type: application/json" \
  -d '{"packs": ["historical", "literary"]}'

# The floor alone
curl -X POST http://localhost:8080/api/v1/sources/src_abc123/reclassify \
  -H "Content-Type: application/json" \
  -d '{"packs": []}'
```

**Eligible source states:** `indexed` (extraction never run or was cancelled) or `committed` (full re-extraction). The source page also marks a source one of whose packs has changed since it was extracted.

## How an Entity Gets Its Type

The model writes a type for every entity. Junk records and property-like types aside, the type rescue decides in this order which core type (and subtype) the entity is stored under:

1. a core type is itself;
2. a pack's type alias, then the mapping table of every type name the built-in domains ever declared (`Character` → Person / `character`, `Military Unit` → Organization / `military_unit`), then a pack's own subtype names;
3. the packs' rescue keywords, matched in the entity's description and then its written type;
4. the floor's keywords;
5. an entity called by a reference whose words name a person ("the old count") is a Person.

An entity no core type answers to is **kept**, not dropped: it is stored as a `Concept` flagged `unplaced_type`, with the type as written under `written_type`, and counted on the source (`unplaced_type_entities_flagged`).

## The Unresolved Region

The pipeline keeps the records it is unsure about instead of deleting them, and marks each with a flag on the node: unconnected (`orphan`), labelled by a reference (`descriptor_reference`), low plausibility, unplaced type, unevidenced, or an excluded name. The graph canvas lays out every flagged node together in one labelled corral, **Unresolved**, beside the main graph, with a badge per flag on each node. The canvas's Unresolved button collapses the region into one summary node, *Unresolved (N)*, and expands it again; the choice is remembered per browser. Collapsing is presentation only: no node or edge is removed. See [Pipeline Flags](../architecture/extraction-pipeline/commit.md#pipeline-flags-on-nodes-and-edges) for what each flag means.

## Subtypes and Relationships in Chat

The chat's graph context writes each entity with its subtype when it has one, else its core type:

```
Pierre (character) child_of Count Bezúkhov (character).
```

Relationships the extraction kept flagged `unevidenced` (no cited sentence states them) stay out of the chat context by default. **Settings → General → Chat context** has the switch **Use unevidenced relationships in chat context** (off by default); turned on, they are read after every evidenced line. The switch reaches running chats at once. See [Search](search.md) for the setting (`graphrag.include_unevidenced_edges`).

## Extraction Limits

Hard caps keep the model from over-connecting a graph:

| Setting | What It Controls |
|---------|-----------------|
| `max_entity_degree` | Max relationships per entity (in + out combined); the floor's is 80 |
| `max_same_source_type` | Max relationships with the same (source entity, relationship type) pair; the floor's is 20 |
| `max_relationship_ratio` | Max relationships as a multiplier of entity count (the built-in packs set 5.0 to 8.0) |
| `loop_max_entity_count` | Max entities per chunk before the LLM stream is aborted (the built-in packs set 25 to 50) |

A relationship past a cap is kept, flagged `capped`, and drawn thin. A pack sets its own values on top of the filtering preset, except that its `loop_max_entity_count` (and `minimum_alias_length`) moves with a [filtering mode](../reference/filtering-modes.md#domain-packs-and-the-slider) you choose away from `balanced`; for the two floor limits it may only raise the floor's value.

## Entity and Content Exclusions

Each domain lists what the LLM should *not* extract because it carries no fact — pronouns and vague references (the floor), bare measurements, citation formatting, boilerplate. A person called only by a title or a role is never on the list: it is kept, and folded or flagged. An entity whose name is one of an exclusion rule's examples is kept when it carries evidence, flagged `excluded_name`.

Domains also name [content categories](../architecture/extraction-pipeline/overview.md#content-filtering) to strip before extraction. The floor strips `legal`, `boilerplate` and `web_artifacts`; the `technical` pack adds `toc`, `changelog`, `metadata` and `advertising`. A source's stack strips every member's categories.

```json
"content_exclusions": {
    "categories": ["toc", "changelog", "legal", "boilerplate"],
    "custom_patterns": [
        {
            "regex": "^\\s*v?\\d+\\.\\d+",
            "mode": "count",
            "threshold": 3,
            "description": "Version number lists"
        }
    ]
}
```

- **categories** -- References built-in category names (toc, changelog, legal, bibliography, acknowledgments, boilerplate, metadata, code_blocks, data_tables, math, api_tables, procedural, advertising, web_artifacts, bulk_lists)
- **custom_patterns** -- Domain-specific regex patterns with mode (`count` to exclude whole chunks, `line_ratio` to strip matching lines) and threshold

## Custom Packs

Place a `.jsonld` file in `data/plugins/domains/` and it is discovered on startup. A user domain is always a pack: it declares `"role": "pack"` and `"extends": "core"`, and only the built-in `generic` is the floor.

```
data/
  plugins/
    domains/
      culinary.jsonld
```

**Minimal pack:**

```json
{
  "@context": { "@vocab": "https://chaoscypher.io/schema/domain#" },
  "@type": "ExtractionDomain",
  "name": "culinary",
  "version": "1.0.0",
  "description": "Recipes, cooking techniques, ingredients, and food science",
  "role": "pack",
  "extends": "core",
  "builtin": false,

  "detection": {
    "keywords": {
      "cooking": { "terms": ["recipe", "ingredient", "tablespoon", "preheat"], "weight": 1.2 }
    },
    "confidence": { "base_score": 0.2, "min_threshold": 0.4 }
  },

  "entity_guidance": "Extract recipes, ingredients and chefs. DO NOT extract bare measurements.",

  "subtypes": [
    { "name": "Recipe", "of": "Work", "description": "A specific dish or preparation",
      "rescue_keywords": ["recipe", "dish"] },
    { "name": "Ingredient", "of": "Object", "description": "A food ingredient used in recipes" },
    { "name": "Chef", "of": "Person", "names_a_person": true,
      "description": "A chef, cook, or culinary author" }
  ],

  "templates": {
    "edge_templates": [
      { "name": "contains_ingredient", "description": "Recipe contains an ingredient",
        "source_types": ["Work"], "target_types": ["Object"] }
    ]
  }
}
```

A recipe is then stored as a Work with `entity_subtype` `recipe`, and the prompt's Work line lists `recipe` among its subtypes. For the full schema — detection scoring, subtypes, aliases, rescue keywords, name conventions, filtering presets — see [Building Extraction Domains](../developer-guide/building-domains.md).

## Databases From Before the Core Types

A database whose graph was extracted before the core types is **not migrated in place**: its nodes keep their old type names (`Character`, `Military Unit`) and templates. Opening it is not an error and rewrites nothing; the dashboard shows a one-line notice, and the log carries one `database_predates_core_types` warning per database.

To bring it onto the core types, export it with the previous version, update, and import the export (see [Upgrading](../getting-started/upgrading.md#databases-from-before-the-core-entity-types) and [Import & Export](import-export.md)). The import maps the old types: every node is stored under its core type with the old name as its subtype (read with the packs the bundle's sources were extracted with), a name the mapping table does not hold is kept as a `Concept` flagged `unplaced_type`, the old per-type templates give way to one per core type, and relationships are imported as they were. The import's result lists what it mapped, old type by old type (`core_types_mapped`).
