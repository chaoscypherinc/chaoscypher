---
id: building-domains
title: Building Extraction Domains
description: Create a custom extraction pack for Chaos Cypher with a JSON-LD config file — add subtypes of the core entity types, relationship labels, detection rules and LLM guidance on top of the floor, without Python code.
---

# Building Extraction Domains

Extraction domains control how Chaos Cypher's AI extracts entities and relationships from documents. Every source extracts with the **floor** (the built-in `generic` domain: the eight core entity types and the core relationship labels) plus the **packs** it chooses. A custom domain is always a pack: a single JSON-LD file that adds to the floor -- keyword-based content detection, LLM guidance, subtypes of the core types, relationship labels, and rescue keywords. No Python code is required.

For what the floor holds and how packs are chosen, see [Extraction Domains](../user-guide/domains.md).

## What a Pack Does

When a source extracts with a pack, the pack:

1. **Guides the LLM** -- its entity and relationship guidance follow the floor's in the prompt, under `Pack <name>:`.
2. **Adds subtypes** -- kinds of the core types relevant to its documents (e.g. `Hypothesis` of Concept, `Experiment` of Event for scientific papers). A node is stored under its core type with the subtype in `properties.entity_subtype`.
3. **Adds relationship labels** -- labels the core lacks (e.g. `supports`, `contradicts`), between core types.
4. **Rescues types** -- type aliases and rescue keywords map what the model wrote onto its subtypes and the core types.
5. **Tunes extraction** -- a filtering preset, extraction limits at or above the floor's, exclusions, name conventions and quality scores.

Chaos Cypher ships the floor and 18 built-in packs, including `technical`, `scientific`, `legal`, `medical`, `news`, `literary` and `investigation`.

## The JSON-LD Pack File Format

Every pack is a single `.jsonld` file. Here is the minimal structure:

```json
{
  "@context": {
    "@vocab": "https://chaoscypher.io/schema/domain#",
    "schema": "https://schema.org/",
    "name": "schema:name",
    "description": "schema:description",
    "author": "schema:author",
    "version": "schema:version"
  },
  "@type": "ExtractionDomain",
  "@id": "domain:my_pack",

  "name": "my_pack",
  "version": "1.0.0",
  "description": "Description of what this pack covers",
  "role": "pack",
  "extends": "core",
  "author": "Your Name",
  "builtin": false,

  "detection": { ... },
  "entity_guidance": "...",
  "subtypes": [ ... ],
  "templates": { "edge_templates": [ ... ] }
}
```

The `@context`, `@type`, and `@id` fields are standard JSON-LD metadata. `"role": "pack"` and `"extends": "core"` are required: a config without a `role` is refused, and only the built-in `generic` may be the floor. The functional fields are described in the sections below.

## Step-by-Step Example: Creating a Culinary Pack

This example creates a pack for extracting knowledge from cookbooks, recipe collections, and food science documents.

### 1. Create the pack file

Create `culinary.jsonld`. The key sections are metadata, detection keywords, guidance for the LLM, subtypes of the core types, and relationship labels:

```json
{
  "@context": {
    "@vocab": "https://chaoscypher.io/schema/domain#",
    "schema": "https://schema.org/",
    "name": "schema:name",
    "description": "schema:description"
  },
  "@type": "ExtractionDomain",
  "@id": "domain:culinary",

  "name": "culinary",
  "version": "1.0.0",
  "description": "Recipes, cooking techniques, ingredients, and food science",
  "role": "pack",
  "extends": "core",
  "builtin": false,

  "detection": {
    "keywords": {
      "cooking": {
        "terms": ["recipe", "ingredient", "tablespoon", "preheat", "bake", "simmer"],
        "weight": 1.2
      }
    },
    "patterns": [
      { "regex": "\\d+\\s*(cups?|tbsp|tsp|oz|grams?)", "weight": 1.5 }
    ],
    "confidence": { "base_score": 0.2, "min_threshold": 0.4 }
  },

  "entity_guidance": "Extract recipes, ingredients, techniques, and equipment.\nDO NOT extract bare measurements or generic actions.",

  "subtypes": [
    {
      "name": "Recipe", "of": "Work",
      "description": "A specific dish or preparation",
      "requires_named_referent": true, "quality_score": 25,
      "rescue_keywords": ["recipe", "dish", "preparation"]
    },
    {
      "name": "Ingredient", "of": "Object",
      "description": "A food ingredient used in recipes",
      "quality_score": 18,
      "rescue_keywords": ["ingredient", "spice"]
    }
  ],

  "templates": {
    "edge_templates": [
      {
        "id": "culinary_edge_contains", "name": "contains_ingredient",
        "description": "Recipe contains an ingredient",
        "inverse": "ingredient_in",
        "source_types": ["Work"], "target_types": ["Object"]
      }
    ]
  }
}
```

This is a minimal working pack. A `Recipe` is stored as a `Work` with `entity_subtype` `recipe`; an `Ingredient` as an `Object` with `entity_subtype` `ingredient`. Real packs typically include more keyword groups, more subtypes and labels, and extraction examples.

<details>
<summary>Full culinary pack with all features</summary>

The complete version adds multiple keyword groups with weights, regex patterns for measurements, separate entity and relationship guidance, six subtypes (Recipe, Ingredient, Technique, Equipment, Cuisine, Chef) with properties and rescue keywords, an extra property and rescue keywords for a core type, type aliases, seven relationship labels, LLM examples, entity exclusions, a filtering preset, quality scoring, and extraction limits.

```json
{
  "@context": {
    "@vocab": "https://chaoscypher.io/schema/domain#",
    "schema": "https://schema.org/",
    "name": "schema:name",
    "description": "schema:description",
    "author": "schema:author",
    "version": "schema:version"
  },
  "@type": "ExtractionDomain",
  "@id": "domain:culinary",

  "name": "culinary",
  "version": "1.0.0",
  "description": "Recipes, cooking techniques, ingredients, and food science",
  "role": "pack",
  "extends": "core",
  "author": "Your Name",
  "builtin": false,
  "icon": "Restaurant",
  "extraction_filtering_mode": "balanced",

  "detection": {
    "@type": "DetectionConfig",
    "keywords": {
      "cooking": {
        "terms": ["recipe", "ingredient", "tablespoon", "teaspoon", "preheat",
                  "bake", "simmer", "sauté", "dice", "mince", "marinate"],
        "weight": 1.2
      },
      "food": {
        "terms": ["flour", "sugar", "butter", "olive oil", "seasoning",
                  "cuisine", "dish", "meal", "appetizer", "dessert"],
        "weight": 1.0
      },
      "technique": {
        "terms": ["blanch", "braise", "deglaze", "emulsify", "ferment",
                  "julienne", "reduce", "temper", "caramelize"],
        "weight": 0.8
      }
    },
    "file_extensions": [".pdf", ".txt", ".md"],
    "doc_types": ["recipe", "cookbook"],
    "patterns": [
      {
        "regex": "\\d+\\s*(cups?|tbsp|tsp|oz|lbs?|grams?|ml|minutes?|hours?)",
        "weight": 1.5,
        "description": "Measurement patterns"
      },
      {
        "regex": "(?i)preheat\\s+(oven|to)\\s+\\d+",
        "weight": 1.3,
        "description": "Oven temperature instructions"
      }
    ],
    "confidence": {
      "base_score": 0.2,
      "per_keyword_boost": 0.05,
      "extension_boost": 0.1,
      "doc_type_boost": 0.25,
      "pattern_boost": 0.15,
      "min_threshold": 0.4
    }
  },

  "entity_guidance": "ENTITY EXTRACTION RULES:\n- Extract recipes, ingredients, techniques, and equipment\n- Include quantities and measurements in properties\n- Capture cuisine styles and regional origins\n\nDO NOT EXTRACT:\n- Individual measurement values as entities (e.g., '2 cups')\n- Generic actions as entities: 'mix', 'stir', 'serve' (a named technique is a Technique)\n- Tableware: plates, forks, napkins",

  "relationship_guidance": "RELATIONSHIP EXTRACTION RULES:\n- COMPOSITION: Use contains_ingredient for recipe-ingredient links\n- TECHNIQUE: Use uses_technique for recipe-technique links\n- ORIGIN: Use originates_from for cuisine links\n- VARIATION: Use variation_of for recipe variants\n- AUTHORSHIP: Use the core created_by for a recipe's chef",

  "entity_exclusions": [
    {
      "description": "Bare measurement values",
      "examples": ["2 cups", "350°F", "30 minutes"]
    },
    {
      "description": "Generic kitchen actions without a specific technique name",
      "examples": ["mix", "stir", "serve"]
    },
    {
      "description": "Tableware and serving items",
      "examples": ["plates", "bowls", "utensils"]
    }
  ],

  "subtypes": [
    {
      "name": "Recipe",
      "of": "Work",
      "description": "A specific dish or preparation",
      "requires_named_referent": true,
      "quality_score": 25,
      "icon": "MenuBook",
      "properties": [
        { "name": "cook_time", "display_name": "Cook Time", "property_type": "text" },
        { "name": "servings", "display_name": "Servings", "property_type": "text" }
      ],
      "rescue_keywords": ["recipe", "dish", "preparation", "how to make"]
    },
    {
      "name": "Ingredient",
      "of": "Object",
      "description": "A food ingredient used in recipes",
      "requires_named_referent": true,
      "quality_score": 18,
      "rescue_keywords": ["ingredient", "food item", "produce", "spice"]
    },
    {
      "name": "Technique",
      "of": "Concept",
      "description": "A cooking technique or method",
      "quality_score": 18,
      "rescue_keywords": ["technique", "cooking method", "procedure"]
    },
    {
      "name": "Equipment",
      "of": "Object",
      "description": "A cooking tool or appliance",
      "quality_score": 15,
      "rescue_keywords": ["equipment", "tool", "appliance", "utensil"]
    },
    {
      "name": "Cuisine",
      "of": "Concept",
      "description": "A culinary tradition or regional style",
      "quality_score": 18,
      "rescue_keywords": ["cuisine", "culinary tradition", "food culture"]
    },
    {
      "name": "Chef",
      "of": "Person",
      "names_a_person": true,
      "description": "A chef, cook, or culinary author",
      "quality_score": 18,
      "rescue_keywords": ["chef", "cook", "baker"]
    }
  ],

  "core_properties": {
    "Event": [
      { "name": "venue", "display_name": "Venue", "property_type": "text" }
    ]
  },

  "core_rescue_keywords": {
    "Event": ["banquet", "tasting", "dinner service"]
  },

  "type_aliases": {
    "Dish": "Recipe",
    "Cooking Method": "Technique",
    "Cook": "Chef"
  },

  "templates": {
    "edge_templates": [
      {
        "@type": "EdgeTemplate",
        "id": "culinary_edge_contains",
        "name": "contains_ingredient",
        "description": "Recipe contains an ingredient",
        "inverse": "ingredient_in",
        "quality_score": 15,
        "source_types": ["Work"],
        "target_types": ["Object"]
      },
      {
        "@type": "EdgeTemplate",
        "id": "culinary_edge_technique",
        "name": "uses_technique",
        "description": "Recipe uses a cooking technique",
        "inverse": "technique_used_in",
        "quality_score": 25,
        "source_types": ["Work"],
        "target_types": ["Concept"]
      },
      {
        "@type": "EdgeTemplate",
        "id": "culinary_edge_equipment",
        "name": "requires_equipment",
        "description": "Recipe or technique requires equipment",
        "inverse": "equipment_for",
        "quality_score": 15,
        "source_types": ["Work", "Concept"],
        "target_types": ["Object"]
      },
      {
        "@type": "EdgeTemplate",
        "id": "culinary_edge_origin",
        "name": "originates_from",
        "description": "Recipe originates from a cuisine or a region",
        "inverse": "origin_of",
        "quality_score": 15,
        "source_types": ["Work"],
        "target_types": ["Concept", "Location"]
      },
      {
        "@type": "EdgeTemplate",
        "id": "culinary_edge_variation",
        "name": "variation_of",
        "description": "Recipe is a variation of another recipe",
        "inverse": "has_variation",
        "quality_score": 25,
        "source_types": ["Work"],
        "target_types": ["Work"]
      },
      {
        "@type": "EdgeTemplate",
        "id": "culinary_edge_pairs_with",
        "name": "pairs_with",
        "description": "Ingredient pairs well with another ingredient",
        "symmetric": true,
        "quality_score": 15,
        "source_types": ["Object"],
        "target_types": ["Object"]
      },
      {
        "@type": "EdgeTemplate",
        "id": "culinary_edge_substitutes",
        "name": "substitutes_for",
        "description": "Ingredient can substitute for another",
        "inverse": "substituted_by",
        "quality_score": 25,
        "source_types": ["Object"],
        "target_types": ["Object"]
      }
    ]
  },

  "examples": {
    "alias_examples": [
      {
        "canonical": "Béchamel Sauce",
        "aliases": ["white sauce", "béchamel"],
        "note": "Classic and common names both become aliases"
      }
    ],
    "relationship_examples": [
      {
        "source": "Beef Bourguignon",
        "target": "Red Wine",
        "type": "contains_ingredient",
        "note": "Use contains_ingredient for recipe-ingredient links"
      },
      {
        "source": "Crème Brûlée",
        "target": "French",
        "type": "originates_from",
        "note": "Use originates_from for cuisine origin"
      }
    ]
  },

  "title_words": [
    "chef", "the", "of", "de", "la", "le", "al"
  ],

  "quality_scoring": {
    "default_entity_score": 18,
    "default_relationship_score": 15
  },

  "extraction_limits": {
    "max_relationship_ratio": 6.0,
    "loop_max_entity_count": 35
  }
}
```

</details>

### 2. Place the file

Drop the file into the user plugin directory:

```
data/plugins/domains/culinary.jsonld
```

:::note[Two file patterns are supported]

The domain registry supports two discovery patterns:

- **Single file** (preferred): `data/plugins/domains/culinary.jsonld`
- **Folder with config** (legacy): `data/plugins/domains/culinary/domain.jsonld`

The single-file pattern is simpler and recommended for new packs.

:::

### 3. Restart the application

The `DomainRegistry` discovers domains at startup. Restart the services to load your pack.

```bash
make docker-dev   # Restart services
```

Your pack will appear in the logs:

```
domain_registered  domain=culinary  version=1.0.0  builtin=False  path_type=user
```

:::note

`domain_registered` is emitted at DEBUG level -- set `LOG_LEVEL=DEBUG` to see it (unlike `loader_registered` and `tool_registered`, which are INFO). A pack the schema refuses is logged at WARNING as `domain_schema_invalid`, with every problem in `error_message`, and is not registered.

:::

## Pack Fields Reference

### Core Metadata

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique pack identifier (lowercase, no spaces). Used as lookup key; `generic` is the floor's and is refused. |
| `role` | Yes | `"pack"` for every user domain (`"floor"` is the built-in `generic` alone). |
| `extends` | Yes | `"core"` for every pack. |
| `version` | No | Semantic version string (default: `"1.0.0"`). |
| `description` | Yes | Human-readable description shown in the pack chooser. |
| `author` | No | Author or team name. |
| `builtin` | No | Set to `false` for user packs. |
| `icon` | No | Material icon name shown beside the pack in the interface (e.g. `"Restaurant"`). |
| `extraction_filtering_mode` | No | Filtering preset that controls which quality filters are active and how strict they are. One of: `maximum`, `strict`, `balanced`, `lenient`, `minimal`, `unfiltered`. Without one, the floor's (`balanced`) applies. See [Filtering Modes](#filtering-modes) below. |

### Detection

The `detection` object controls how Chaos Cypher decides whether to propose this pack for a document.

```json
"detection": {
  "keywords": {
    "group_name": {
      "terms": ["keyword1", "keyword2"],
      "weight": 1.0
    }
  },
  "file_extensions": [".pdf"],
  "doc_types": ["recipe"],
  "patterns": [
    {"regex": "\\d+ cups?", "weight": 1.5, "description": "Measurements"}
  ],
  "confidence": {
    "base_score": 0.2,
    "per_keyword_boost": 0.05,
    "extension_boost": 0.1,
    "doc_type_boost": 0.25,
    "pattern_boost": 0.15,
    "min_threshold": 0.4
  }
}
```

| Sub-field | Description |
|-----------|-------------|
| `keywords` | Named groups of terms to match. Each match adds `per_keyword_boost * weight` to confidence. Short terms (< 4 chars, alphabetic) are automatically matched with word boundaries to avoid false positives. |
| `file_extensions` | File extensions that boost confidence by `extension_boost`. |
| `doc_types` | Document type metadata values that boost confidence by `doc_type_boost`. |
| `patterns` | Regex patterns with weights. Each match adds `pattern_boost * weight`. |
| `confidence.base_score` | Starting confidence before any matches. |
| `confidence.min_threshold` | Minimum confidence for the pack to be a candidate at all. |

Detection proposes every pack whose **evidence** -- its score minus its own `base_score` -- reaches 0.30, highest score first, at most two. A high `base_score` therefore does not get a pack proposed; its keyword and pattern hits do.

### Guidance

Guidance strings are injected into LLM prompts during entity extraction, after the floor's, under `Pack <name>:`:

| Field | Description |
|-------|-------------|
| `entity_guidance` | Guidance for entity extraction. |
| `relationship_guidance` | Guidance for relationship extraction. |

### Subtypes

A subtype is a kind of one of the core types: Person, Organization, Location, Event, Work, Concept, Object, Group. It is stored on the node as `entity_subtype`, the name in snake case (`Military Unit` → `military_unit`).

```json
{
  "name": "Subtype Name",
  "of": "Work",
  "description": "What this subtype covers",
  "requires_named_referent": true,
  "quality_score": 25,
  "icon": "MenuBook",
  "color": "#d641b0",
  "properties": [
    { "name": "property_name", "display_name": "Display Name", "property_type": "text" }
  ],
  "rescue_keywords": ["word that rescues a written type to this subtype"]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | The subtype's name (e.g. `"Recipe"`). Must not be a core type's name. |
| `of` | Yes | The core type it is a kind of. |
| `description` | No | What the subtype covers. The prompt lists the subtype by its key under its core type (`(subtypes: recipe — write Work/recipe)`), and the model writes it as `Work/recipe`; the description is for readers of the pack and is checked by the same prompt guards as the rendered text. |
| `names_a_person` | No | `true` for a Person subtype whose names are people's names: a possessive in such a name (`"Natásha's mother"`) describes someone through another person rather than being part of a name. Only a Person subtype may set it. |
| `requires_named_referent` | No | If `true`, entities of this subtype must have a proper name. |
| `quality_score` | No | Score for graph quality ranking, keyed by the stored `entity_subtype`. |
| `icon`, `color` | No | The subtype's own icon and colour; the interface shows the icon on its entity chips (the node keeps its core type's colour). |
| `properties` | No | Properties the subtype offers: `name`, `display_name`, `property_type`. |
| `rescue_keywords` | No | Words that rescue a written type the mapping table does not know to this subtype, matched as whole words in the entity's description and then in the written type (e.g., literary's `Character` lists `prince` and `countess`). Read after the mapping table and before the floor's keywords. Pronouns (`he`, `she`, `a man`, ...) are ignored. |

### Core Properties, Core Rescue Keywords and Type Aliases

| Field | Type | Description |
|-------|------|-------------|
| `core_properties` | `object` | Core type → extra properties the pack offers on it (e.g., literary's `Event` gains `host`, `date`, `place`). Listed on that core type's prompt line after the floor's. |
| `core_rescue_keywords` | `object` | Core type → words that rescue a written type to that core type with no subtype (e.g., literary's `Event` lists `battle` and `duel`). Read with the subtypes' `rescue_keywords`. |
| `type_aliases` | `object` | Maps a name the pack does not declare to one of its subtypes or to a core type (e.g., the literary pack maps `"Historical Figure"` to its `"Character"` subtype). The per-chunk type rescue reads an alias before the mapping table, so an entity written `Historical Figure` is stored as Person with the `entity_subtype` `character`. An alias to a core type keeps the table's subtype of the written name when that subtype belongs to the same core type. Matching is exact first, then case-insensitive. An alias may not shadow a core type or one of the pack's subtypes. |

### Relationship Labels

A pack's `templates` block holds `edge_templates` only: relationship labels the core lacks. It never declares `node_templates` (the core types are the floor's).

```json
{
  "id": "unique_edge_id",
  "name": "relationship_name",
  "description": "What this relationship means",
  "inverse": "inverse_relationship_name",
  "symmetric": false,
  "quality_score": 15,
  "source_types": ["Work"],
  "target_types": ["Object"]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Relationship label (lowercase, snake_case by convention). Must not be one of the floor's core labels (`created_by`, `part_of`, `located_in`, ...): use the core label instead. |
| `id` | No | Template ID. |
| `description` | No | Describes the relationship. Included in LLM prompts. |
| `inverse` | No | Name of the inverse relationship (e.g., `"contains_ingredient"` / `"ingredient_in"`). |
| `symmetric` | No | If `true`, the relationship is bidirectional (e.g., `"pairs_with"`). Symmetric edges map to themselves as inverse. |
| `quality_score` | No | Score for graph quality ranking. |
| `source_types` | No | Core types a source entity may have. Used for constraint validation after extraction. |
| `target_types` | No | Core types a target entity may have. |
| `two_endpoints_only` | No | `true` for a label whose evidence must name both endpoints (with `two_endpoints_only_reason`). |

A relationship whose endpoints its label refuses is kept, flagged `type_mismatch`; one whose label no relationship type answers to is kept as `related_to`, flagged `unmatched_label` (every extraction plan runs the check strict).

### Additional Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `entity_exclusions` | `list[object]` | Structured rules describing what the LLM should NOT extract. Each rule requires a `description` (injected as a "SKIP" rule in the extraction prompt) and a non-empty `examples` list (an entity named like an example is kept only when it carries evidence, flagged `excluded_name`). Unknown keys are rejected at config load. |
| `title_words` | `list[str]` | Lowercase title/honorific words excluded during entity deduplication (e.g., `["chef", "the", "dr"]`). |
| `feminine_titles` | `list[str]` | Titles that say a woman (e.g., `["queen", "countess"]`). The name rule never merges a name carrying one with a name carrying a masculine title. When omitted, the universal ones are used (`mrs`, `ms`, `miss`, `madam`, `dame`). Any gendered word in `title_words` should be listed here or in `masculine_titles`, or two people of opposite genders can merge. |
| `masculine_titles` | `list[str]` | Titles that say a man (e.g., `["king", "count"]`). When omitted, the universal ones are used (`mr`, `mister`, `sir`). |
| `generation_words` | `object` | Words that say which of two same-named people is meant, mapped to a generation label (e.g., `{"old": "elder", "young": "younger"}`). Names with different generations never merge. When omitted, the universal `elder`/`senior`/`sr` and `younger`/`junior`/`jr` are used. |
| `name_forms` | `object` | Name-form endings by language code, each `{"ending", "stem", "gender"}` (e.g., `{"ru": [{"ending": "ova", "stem": "ov", "gender": "f"}]}`, so "Rostova" and "Rostov" are one family and the `-ova` form says a woman). `gender` is `"f"`, `"m"` or `null`. When omitted, no endings are folded. The literary pack declares the Russian surname and patronymic forms. |
| `role_words` | `list[str]` | Words naming a role one person has to another (`"valet"`, `"steward"`): with the `relation_vocabulary` words they make "X's valet" a descriptor, never X's name. |
| `relation_vocabulary` | `object` | The words prose uses to state each relation, by relation name: `{"sibling": {"labels": ["sibling_of"], "words": ["brother", "sister"]}}`. Evidence validation accepts a relationship whose cited sentences name only one endpoint when they contain one of its words. A word made only of function words ("with", "of the") is refused. When omitted, only words of the relationship label itself count. |
| `examples` | `object` | Example extractions to include in LLM prompts, after the floor's. Contains `alias_examples` and `relationship_examples` lists. |
| `quality_scoring` | `object` | Default quality scores: `default_entity_score` and `default_relationship_score`. |
| `content_exclusions` | `object` | Content categories and custom patterns to strip before extraction (see [Extraction Domains](../user-guide/domains.md#entity-and-content-exclusions)); a source strips every member's. |
| `extraction_limits` | `object` | Per-pack overrides applied on top of the filtering mode preset, except `loop_max_entity_count` and `minimum_alias_length`, which move with a mode the user chooses away from `balanced` ([Domain packs and the slider](../reference/filtering-modes.md#domain-packs-and-the-slider)). Despite the name, **any `FilteringConfig` field name** is a valid key -- the most common are the relationship density limits `max_relationship_ratio`, `max_entity_degree`, `max_same_source_type`. `max_entity_degree` and `max_same_source_type` may only be raised above the floor's (80 and 20); a lower value refuses the pack. Where two packs of a source set the same limit, the larger holds. |

### What a Pack May Not Declare

The schema refuses a pack that re-declares something of the floor's, and the keys the restructure removed:

| Key or shape | Why / what instead |
|--------------|--------------------|
| `templates.node_templates` | The core types are the floor's; declare `subtypes`. |
| A subtype named like a core type, or `of` a non-core type | A subtype is a kind of a core type. |
| An edge label that is a core label | Use the core label. |
| `source_types` / `target_types` that are not core types | Endpoints are core types. |
| `max_entity_degree` / `max_same_source_type` below the floor's | A pack may raise a limit, never lower one. |
| `strict_entity_types` | The floor's: the core types are always strict. |
| `guidance` | Write `entity_guidance` and `relationship_guidance`. |
| `entity_templates` | Relationship labels go under `templates`. |
| `strict_validation` | The schema validates every domain strictly. |
| `normalizer_overrides` | Set the normalizer in the settings. |
| `allow_template_fallback` | The floor's templates always apply. |
| `extraction_overrides` | Set the system prompt in the extraction settings. |
| `vocabulary_exclusions` | Mark the edge template `"two_endpoints_only": true`. |
| `evidence_validation_mode` | Set it in the extraction settings. |

An older per-domain plugin (with its own node templates and no `role`) can be converted with `python -m chaoscypher_core.services.sources.engine.extraction.domains.convert_packs`, which maps every old template name onto its core type.

## Filtering Modes

The `extraction_filtering_mode` field selects a preset that controls which quality filters are active and how strict they are. Instead of individually tuning evidence validation, type constraints, plausibility thresholds, and relationship limits, a pack declares a single preset.

### Available Presets

| Preset | Description |
|--------|-------------|
| `maximum` | All filters on, strict evidence, tighter plausibility threshold |
| `strict` | Strict evidence + type constraints, flag mismatches |
| `balanced` | All filters active with fall-throughs (the floor's) |
| `lenient` | Narrative evidence for pronoun-heavy prose, lower plausibility thresholds |
| `minimal` | Most filters disabled, elevated limits |
| `unfiltered` | Data integrity only (dedup + index validation) |

Orphan protection (keeping even the entities with no relationships that carry no description or evidence, and exempting endpoints with fewer than 2 edges from relationship caps) is enabled only by the `minimal` and `unfiltered` presets. An orphan that carries a description or an evidence sentence is committed under every preset, flagged `properties.orphan`.

Legacy preset names (`standard`, `precise`, `narrative`, `permissive`, `raw`) are no longer accepted and raise `ValueError` -- use one of the six canonical presets above. Some callers (e.g. MCP) silently fall back to the default preset when an invalid mode is found on a stored source row.

For detailed filter-by-filter behavior of each preset, see [Architecture > Entity Extraction > Filtering Modes](../architecture/extraction-pipeline/entity-extraction.md#filtering-modes).

### Built-in Pack Mapping

| Preset | Packs |
|--------|-------|
| `balanced` | design, educational, financial, philosophical, political, reference, technical, theological |
| `strict` | cybersecurity, intelligence, investigation, legal, medical, scientific |
| `lenient` | biographical, historical |
| `minimal` | news |

The floor's preset is `balanced`; the `literary` pack names none and takes the floor's. A source with two packs takes their preset when they name the same one (or only one names one), else the floor's.

### A Preset With Optional Overrides

A pack config needs only its preset; `extraction_limits` overrides a value when the preset does not exactly match your needs:

```json
{
  "name": "my_pack",
  "role": "pack",
  "extends": "core",
  "extraction_filtering_mode": "strict",
  "extraction_limits": {
    "max_relationship_ratio": 6.0
  }
}
```

The preset sets all filter parameters to coherent values. Only override specific fields when you need to deviate from the preset's defaults. An override of `loop_max_entity_count` or `minimum_alias_length` is your value at `balanced` and moves with a mode the user chooses ([Domain packs and the slider](../reference/filtering-modes.md#domain-packs-and-the-slider)); the others hold at every mode.

### Per-Source Override

The filtering mode can also be overridden per-source when adding a document, without changing the pack configuration:

- **API** -- Include `filtering_mode` in the upload form or extraction request body (note: `extraction_filtering_mode` is the settings/domain-config key, not the API field -- an `extraction_filtering_mode` body field is silently ignored)
- **CLI** -- Pass `--filtering-mode <preset>` when adding a document
- **UI** -- Select a filtering mode from the dropdown in the upload dialog

The per-source override takes precedence over the packs' preset, which in turn takes precedence over the floor's (`balanced`).

## How Packs Are Loaded and Chosen

### Discovery Process

The `DomainRegistry` (defined in `packages/core/src/chaoscypher_core/services/sources/engine/extraction/domains/registry.py`) discovers domains at startup:

1. **Scan built-in directory** -- `packages/core/src/chaoscypher_core/services/sources/engine/extraction/domains/plugins/` for `.jsonld` files (the floor and the built-in packs).
2. **Scan user plugin directory** -- `data/plugins/domains/` for `.jsonld` files and subfolders containing `domain.jsonld`.
3. **Validate** -- each config is checked against the schema (`DomainConfigModel`): a pack that does not only add to the floor is refused and logged (`domain_schema_invalid`). A user config that names itself `generic` or declares `"role": "floor"` is refused: the built-in floor is the only one.
4. **Create `ConfigurableDomain`** -- The `ConfigurableDomain` class wraps the parsed config, implementing the `DomainAnalyzer` protocol entirely from configuration.
5. **Register by name** -- Each domain is registered under its `name` field.

### Choosing Process

When a document is indexed:

1. The registry scores every pack (`can_analyze(text, filename, metadata)`): keyword matches, file extension, doc type, and regex patterns.
2. Every pack whose evidence reaches 0.30 is proposed, highest score first, at most two (`propose_packs`); none is the floor alone.
3. The proposal waits for confirmation (or extraction runs at once when packs were chosen at upload); the source records the packs it was extracted with (`extraction_packs`).

You can also choose the packs when adding a source through the UI, API (`packs`) or CLI (`--pack`), and change them later with **Change packs…** (`POST /sources/{id}/reclassify`).

## Best Practices

- **Start with detection tuning.** Before writing subtypes, ensure your keywords and patterns get the pack proposed for documents of its kind. Test with a few real documents and check which packs detection proposes.

- **Use weighted keyword groups.** Group related terms together and assign higher weights to more distinctive terms. Terms that could appear in many domains (like "data" or "analysis") should have lower weights.

- **Pick the core type first.** Each subtype is a kind of exactly one core type. If a kind does not fit Person, Organization, Location, Event, Work, Concept, Object or Group, it is usually a property, not an entity.

- **Keep subtypes focused.** Define the 5-20 subtypes that matter for your documents. Too many lead to inconsistent extraction; the core types already cover the rest.

- **Write clear descriptions.** The `description` of relationship labels is included in LLM prompts (a subtype is listed by its key). Clear, specific descriptions produce better extraction results.

- **Use `rescue_keywords` and `type_aliases`.** They map what the model writes back onto your subtypes. If the LLM outputs "Cooking Method" but your subtype is "Technique", add an alias `"Cooking Method": "Technique"` or the keyword `cooking method`.

- **Reuse the core labels.** Add a relationship label only when no core label states it; `created_by`, `part_of`, `located_in` and the rest are already there and are refused if re-declared.

- **Set `requires_named_referent` appropriately.** Set this to `true` for subtypes that should always have a proper name (e.g., "Recipe", "Chef"). Leave it unset for subtypes that can be descriptive (e.g., "Technique", "Finding").

- **Define `source_types` and `target_types` on labels.** These core-type constraints flag nonsensical relationships. For example, `contains_ingredient` goes from a `Work` (a recipe) to an `Object` (an ingredient).

- **Use `entity_exclusions` for junk only.** Tell the LLM what NOT to extract when it carries no fact: measurement values that belong in a property, formatting artifacts, generic labels with no referent. Never exclude something the text states a fact about: a person called only by a title or a role ("the father", "the plaintiff"), a section the text cites, or "X unless Y". The model never writes what the prompt excludes, so nothing downstream can keep it.

- **Provide examples.** The `examples` section helps the LLM understand domain-specific patterns like alias recognition and relationship typing. Even 2-3 examples make a noticeable difference.

- **Set `builtin` to `false`.** User-created packs should always set `"builtin": false` to distinguish them from packaged ones.

- **Test with real documents.** After creating your pack, upload a representative document and check the extraction results. Iterate on your guidance, subtypes, and detection keywords based on actual output.

## See also

- [Architecture: Plugin System](../architecture/plugins.md) — registry pattern, plugin types, and auto-discovery mechanism overview
- [User guide: Extraction Domains](../user-guide/domains.md) — the floor, the built-in packs, choosing packs, and content exclusions
