---
id: building-domains
title: Building Extraction Domains
description: Create custom extraction domains for Chaos Cypher using a JSON-LD config file — define entity types, relationship types, detection rules, and LLM guidance without Python code.
---

# Building Extraction Domains

Extraction domains control how Chaos Cypher's AI extracts entities and relationships from documents. Each domain provides keyword-based content detection, LLM guidance, entity/relationship templates, and normalization rules -- all defined in a single JSON-LD configuration file. No Python code is required.

## What Domains Do

When a document is processed for entity extraction, Chaos Cypher selects the most appropriate domain by analyzing the document's content. The selected domain then:

1. **Guides the LLM** -- Provides domain-specific instructions for what to extract and what to skip.
2. **Defines entity types** -- Specifies the types of entities relevant to the domain (e.g., `Hypothesis`, `Experiment` for scientific papers).
3. **Defines relationship types** -- Specifies how entities relate to each other (e.g., `supports`, `contradicts`).
4. **Normalizes results** -- Maps variant type names back to canonical types after extraction.
5. **Controls extraction behavior** -- Sets density, strictness, and validation parameters.

Chaos Cypher ships with 19 built-in domains including `generic`, `technical`, `scientific`, `legal`, `medical`, `news`, `literary`, `investigation`, and others.

## The JSON-LD Domain File Format

Every domain is a single `.jsonld` file. Here is the minimal structure:

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
  "@id": "domain:my_domain",

  "name": "my_domain",
  "version": "1.0.0",
  "description": "Description of what this domain covers",
  "author": "Your Name",
  "builtin": false,

  "detection": { ... },
  "templates": { ... },
  "guidance": "..."
}
```

The `@context`, `@type`, and `@id` fields are standard JSON-LD metadata. The functional fields are described in the sections below.

## Step-by-Step Example: Creating a Culinary Domain

This example creates a domain for extracting knowledge from cookbooks, recipe collections, and food science documents.

### 1. Create the domain file

Create `culinary.jsonld`. The key sections are metadata, detection keywords, guidance for the LLM, and entity/relationship templates:

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

  "templates": {
    "node_templates": [
      {
        "id": "culinary_recipe", "name": "Recipe",
        "description": "A specific dish or preparation",
        "requires_named_referent": true, "quality_score": 25,
        "normalization_keywords": ["recipe", "dish", "preparation"]
      },
      {
        "id": "culinary_ingredient", "name": "Ingredient",
        "description": "A food ingredient used in recipes",
        "requires_named_referent": true, "quality_score": 18,
        "normalization_keywords": ["ingredient", "food item", "spice"]
      }
    ],
    "edge_templates": [
      {
        "id": "culinary_edge_contains", "name": "contains_ingredient",
        "description": "Recipe contains an ingredient",
        "inverse": "ingredient_in",
        "source_types": ["Recipe"], "target_types": ["Ingredient"]
      }
    ]
  }
}
```

This is a minimal working domain. Real domains typically include more keyword groups, additional entity/relationship templates, and extraction examples.

<details>
<summary>Full culinary domain with all features</summary>

The complete version adds multiple keyword groups with weights, regex patterns for measurements, separate entity and relationship guidance, 6 entity templates (Recipe, Ingredient, Technique, Equipment, Cuisine, Chef), 8 relationship templates, property definitions with `absorbs_types`, LLM examples, entity exclusions, quality scoring, and extraction limits.

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
  "author": "Your Name",
  "builtin": false,
  "extraction_density": 1.1,
  "strict_entity_types": false,

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

  "entity_guidance": "ENTITY EXTRACTION RULES:\n- Extract recipes, ingredients, techniques, and equipment\n- Include quantities and measurements in properties\n- Capture cuisine styles and regional origins\n\nDO NOT EXTRACT:\n- Individual measurement values as entities (e.g., '2 cups')\n- Generic actions: 'mix', 'stir', 'serve' (unless a named technique)\n- Tableware: plates, forks, napkins",

  "relationship_guidance": "RELATIONSHIP EXTRACTION RULES:\n- COMPOSITION: Use contains_ingredient for recipe-ingredient links\n- TECHNIQUE: Use uses_technique for recipe-technique links\n- ORIGIN: Use originates_from for cuisine-region links\n- VARIATION: Use variation_of for recipe variants",

  "entity_exclusions": [
    "Bare measurement values: '2 cups', '350°F', '30 minutes'",
    "Generic kitchen actions without a specific technique name",
    "Tableware and serving items: plates, bowls, utensils"
  ],

  "templates": {
    "node_templates": [
      {
        "@type": "NodeTemplate",
        "id": "culinary_recipe",
        "name": "Recipe",
        "description": "A specific dish or preparation",
        "requires_named_referent": true,
        "quality_score": 25,
        "compatibility_group": "dishes",
        "normalization_keywords": ["recipe", "dish", "preparation", "how to make"],
        "properties": [
          {
            "name": "cuisine",
            "display_name": "Cuisine",
            "property_type": "text",
            "absorbs_types": ["Cuisine Style"]
          },
          {
            "name": "cook_time",
            "display_name": "Cook Time",
            "property_type": "text",
            "absorbs_types": ["Cook Time", "Cooking Time"]
          },
          {
            "name": "servings",
            "display_name": "Servings",
            "property_type": "text",
            "absorbs_types": ["Serving Size"]
          }
        ]
      },
      {
        "@type": "NodeTemplate",
        "id": "culinary_ingredient",
        "name": "Ingredient",
        "description": "A food ingredient used in recipes",
        "requires_named_referent": true,
        "quality_score": 18,
        "compatibility_group": "ingredients",
        "normalization_keywords": ["ingredient", "food item", "produce", "spice"]
      },
      {
        "@type": "NodeTemplate",
        "id": "culinary_technique",
        "name": "Technique",
        "description": "A cooking technique or method",
        "requires_named_referent": true,
        "quality_score": 18,
        "compatibility_group": "methods",
        "normalization_keywords": ["technique", "method", "cooking method", "procedure"]
      },
      {
        "@type": "NodeTemplate",
        "id": "culinary_equipment",
        "name": "Equipment",
        "description": "A cooking tool or appliance",
        "requires_named_referent": true,
        "quality_score": 15,
        "compatibility_group": "methods",
        "normalization_keywords": ["equipment", "tool", "appliance", "utensil"]
      },
      {
        "@type": "NodeTemplate",
        "id": "culinary_cuisine",
        "name": "Cuisine",
        "description": "A culinary tradition or regional style",
        "requires_named_referent": true,
        "quality_score": 18,
        "compatibility_group": "dishes",
        "normalization_keywords": ["cuisine", "culinary tradition", "food culture"]
      },
      {
        "@type": "NodeTemplate",
        "id": "culinary_chef",
        "name": "Chef",
        "description": "A chef, cook, or culinary author",
        "requires_named_referent": true,
        "quality_score": 18,
        "compatibility_group": "people",
        "normalization_keywords": ["chef", "cook", "author", "baker"]
      }
    ],
    "edge_templates": [
      {
        "@type": "EdgeTemplate",
        "id": "culinary_edge_contains",
        "name": "contains_ingredient",
        "description": "Recipe contains an ingredient",
        "inverse": "ingredient_in",
        "quality_score": 15,
        "source_types": ["Recipe"],
        "target_types": ["Ingredient"]
      },
      {
        "@type": "EdgeTemplate",
        "id": "culinary_edge_technique",
        "name": "uses_technique",
        "description": "Recipe uses a cooking technique",
        "inverse": "technique_used_in",
        "quality_score": 25,
        "source_types": ["Recipe"],
        "target_types": ["Technique"]
      },
      {
        "@type": "EdgeTemplate",
        "id": "culinary_edge_equipment",
        "name": "requires_equipment",
        "description": "Recipe or technique requires equipment",
        "inverse": "equipment_for",
        "quality_score": 15,
        "source_types": ["Recipe", "Technique"],
        "target_types": ["Equipment"]
      },
      {
        "@type": "EdgeTemplate",
        "id": "culinary_edge_origin",
        "name": "originates_from",
        "description": "Recipe originates from a cuisine",
        "inverse": "origin_of",
        "quality_score": 15,
        "source_types": ["Recipe"],
        "target_types": ["Cuisine"]
      },
      {
        "@type": "EdgeTemplate",
        "id": "culinary_edge_variation",
        "name": "variation_of",
        "description": "Recipe is a variation of another recipe",
        "inverse": "has_variation",
        "quality_score": 25,
        "source_types": ["Recipe"],
        "target_types": ["Recipe"]
      },
      {
        "@type": "EdgeTemplate",
        "id": "culinary_edge_created_by",
        "name": "created_by",
        "description": "Recipe created by a chef",
        "inverse": "created",
        "quality_score": 15,
        "source_types": ["Recipe"],
        "target_types": ["Chef"]
      },
      {
        "@type": "EdgeTemplate",
        "id": "culinary_edge_pairs_with",
        "name": "pairs_with",
        "description": "Ingredient pairs well with another ingredient",
        "symmetric": true,
        "quality_score": 15,
        "source_types": ["Ingredient"],
        "target_types": ["Ingredient"]
      },
      {
        "@type": "EdgeTemplate",
        "id": "culinary_edge_substitutes",
        "name": "substitutes_for",
        "description": "Ingredient can substitute for another",
        "inverse": "substituted_by",
        "quality_score": 25,
        "source_types": ["Ingredient"],
        "target_types": ["Ingredient"]
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
    "max_relationship_ratio": 5.0,
    "max_entity_degree": 15,
    "max_same_source_type": 8,
    "_comment": "Optional -- most domains can omit extraction_limits to use generous global defaults"
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

The single-file pattern is simpler and recommended for new domains.

:::

### 3. Restart the application

The `DomainRegistry` discovers domains at startup. Restart the services to load your domain.

```bash
make docker-dev   # Restart services
```

Your domain will appear in the logs:

```
domain_registered  domain=culinary  version=1.0.0  builtin=False  path_type=user
```

## Domain Fields Reference

### Core Metadata

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique domain identifier (lowercase, no spaces). Used as lookup key. |
| `version` | No | Semantic version string (default: `"1.0.0"`). |
| `description` | Yes | Human-readable description shown in the UI. |
| `author` | No | Author or team name. |
| `builtin` | No | Set to `false` for user domains (default: `true` for package-level). |
| `extraction_density` | No | Float multiplier for expected entities per chunk (default: `1.0`). Higher values indicate denser content. |
| `extraction_filtering_mode` | No | Filtering preset that controls which quality filters are active and how strict they are. One of: `maximum`, `strict`, `balanced` (default), `lenient`, `minimal`, `unfiltered`. See [Filtering Modes](#filtering-modes) below. |
| `strict_entity_types` | No | When `true`, only entity types from `node_templates` are allowed; others are filtered out (default: `false`). |

### Detection

The `detection` object controls how Chaos Cypher determines whether this domain applies to a document.

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
| `confidence.min_threshold` | Minimum confidence required for the domain to be considered a match. |

The domain with the highest confidence score above `min_threshold` is selected for a document.

### Guidance

Guidance strings are injected into LLM prompts during entity extraction. You can provide general guidance or separate entity/relationship guidance:

| Field | Description |
|-------|-------------|
| `guidance` | General extraction guidance (fallback for both entity and relationship extraction). |
| `entity_guidance` | Specific guidance for entity extraction. Takes priority over `guidance` for entity prompts. |
| `relationship_guidance` | Specific guidance for relationship extraction. Takes priority over `guidance` for relationship prompts. |

### Templates

Templates define the entity types (nodes) and relationship types (edges) for the domain.

#### Node Templates

```json
{
  "id": "unique_template_id",
  "name": "Entity Type Name",
  "description": "What this entity type represents",
  "requires_named_referent": true,
  "quality_score": 25,
  "compatibility_group": "group_name",
  "normalization_keywords": ["keyword that triggers normalization to this type"],
  "properties": [
    {
      "name": "property_name",
      "display_name": "Display Name",
      "property_type": "text",
      "absorbs_types": ["Invalid Type That Becomes This Property"]
    }
  ]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique template ID (e.g., `"culinary_recipe"`). |
| `name` | Yes | Entity type name used in extraction (e.g., `"Recipe"`). |
| `description` | Yes | Describes what this entity type represents. Included in LLM prompts. |
| `requires_named_referent` | No | If `true`, entities of this type must have a proper name (filters out generic descriptions). |
| `quality_score` | No | Score for graph quality ranking (higher = more valuable). |
| `compatibility_group` | No | Group name for type compatibility during deduplication. Entities with types in the same group can be merged. |
| `normalization_keywords` | No | Keywords in entity descriptions that trigger normalization to this type. |
| `properties` | No | Entity properties with optional `absorbs_types` -- invalid entity types that should become properties on this entity instead. |

#### Edge Templates

```json
{
  "id": "unique_edge_id",
  "name": "relationship_name",
  "description": "What this relationship means",
  "inverse": "inverse_relationship_name",
  "symmetric": false,
  "quality_score": 15,
  "source_types": ["ValidSourceType"],
  "target_types": ["ValidTargetType"]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique template ID. |
| `name` | Yes | Relationship type name (lowercase, snake_case by convention). |
| `description` | Yes | Describes the relationship. Included in LLM prompts. |
| `inverse` | No | Name of the inverse relationship (e.g., `"contains"` / `"contained_in"`). |
| `symmetric` | No | If `true`, the relationship is bidirectional (e.g., `"pairs_with"`). Symmetric edges map to themselves as inverse. |
| `quality_score` | No | Score for graph quality ranking. |
| `source_types` | No | List of valid source entity types. Used for constraint validation after extraction. |
| `target_types` | No | List of valid target entity types. |

### Additional Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `entity_exclusions` | `list[str]` | Descriptions of things the LLM should NOT extract. Injected as "SKIP" rules in the extraction prompt. |
| `title_words` | `list[str]` | Lowercase title/honorific words excluded during entity deduplication (e.g., `["chef", "the", "dr"]`). |
| `examples` | `object` | Example extractions to include in LLM prompts. Contains `alias_examples` and `relationship_examples` lists. |
| `quality_scoring` | `object` | Default quality scores: `default_entity_score` and `default_relationship_score`. |
| `extraction_limits` | `object` | Per-domain overrides for relationship density limits: `max_relationship_ratio`, `max_entity_degree`, `max_same_source_type`. These override the values set by the filtering mode preset. Most domains do not need to set these -- the preset provides appropriate defaults. |
| `evidence_validation_mode` | `string` | Per-domain override for evidence validation: `"strict"`, `"standard"`, `"narrative"`, or `"relaxed"`. Overrides the value set by `extraction_filtering_mode`. Prefer setting `extraction_filtering_mode` instead -- it configures evidence validation along with all other filters as a coherent preset. |
| `strict_edge_type_constraints` | `bool` | Per-domain override for edge type constraint behavior. When `true`, unmatched relationship types and entity type mismatches are dropped. When `false`, they fall through. Overrides the value set by `extraction_filtering_mode`. |

## Filtering Modes

The `extraction_filtering_mode` field selects a preset that controls which quality filters are active and how strict they are. This dramatically simplifies domain configuration -- instead of individually tuning evidence validation, type constraints, plausibility thresholds, and relationship limits, a domain declares a single preset.

### Available Presets

| Preset | Description |
|--------|-------------|
| `maximum` | All filters on, strict evidence, tighter plausibility threshold |
| `strict` | Strict evidence + type constraints, drop on mismatches |
| `balanced` | All filters active with fall-throughs and orphan protection (default) |
| `lenient` | Narrative evidence for pronoun-heavy prose, lower plausibility thresholds |
| `minimal` | Most filters disabled, elevated limits |
| `unfiltered` | Data integrity only (dedup + index validation) |

Legacy names (`standard`, `precise`, `narrative`, `permissive`, `raw`) are accepted as aliases for backwards compatibility.

For detailed filter-by-filter behavior of each preset, see [Architecture > Entity Extraction > Filtering Modes](../architecture/extraction-pipeline/entity-extraction.md#filtering-modes).

### Built-in Domain Mapping

| Preset | Domains |
|--------|---------|
| `balanced` | generic, educational, financial, philosophical, political, technical, theological, design, reference |
| `strict` | cybersecurity, legal, investigation, medical, scientific, intelligence |
| `lenient` | literary, biographical, historical |
| `minimal` | news |

Most of the 19 built-in domains need only their preset declaration, with no additional filter overrides.

### Simplified Domain Configuration

With filtering modes, a domain config only needs to declare its preset. Individual filter fields (`evidence_validation_mode`, `strict_edge_type_constraints`, `extraction_limits`) are still available as per-domain overrides when the preset does not exactly match your needs.

**Before** (manual tuning of individual fields):

```json
{
  "name": "my_domain",
  "evidence_validation_mode": "strict",
  "strict_edge_type_constraints": true,
  "strict_entity_types": true,
  "extraction_limits": {
    "max_relationship_ratio": 6.0,
    "max_entity_degree": 20,
    "max_same_source_type": 10
  }
}
```

**After** (preset with optional overrides):

```json
{
  "name": "my_domain",
  "extraction_filtering_mode": "strict",
  "extraction_limits": {
    "max_entity_degree": 20
  }
}
```

The preset sets all filter parameters to coherent values. Only override specific fields when you need to deviate from the preset's defaults.

### Per-Source Override

The filtering mode can also be overridden per-source when adding a document, without changing the domain configuration:

- **API** -- Include `extraction_filtering_mode` in the source creation request body
- **CLI** -- Pass `--filtering-mode <preset>` when adding a document
- **UI** -- Select a filtering mode from the dropdown in the upload dialog

The per-source override takes precedence over the domain's preset, which in turn takes precedence over the global default (`balanced`).

## How Domains Are Loaded and Selected

### Discovery Process

The `DomainRegistry` (defined in `packages/core/src/chaoscypher_core/services/sources/engine/extraction/domains/registry.py`) discovers domains at startup:

1. **Scan built-in directory** -- `packages/core/src/chaoscypher_core/services/sources/engine/extraction/domains/plugins/` for `.jsonld` files.
2. **Scan user plugin directory** -- `data/plugins/domains/` for `.jsonld` files and subfolders containing `domain.jsonld`.
3. **Parse JSON-LD** -- Each config file is loaded and parsed.
4. **Create `ConfigurableDomain`** -- The `ConfigurableDomain` class wraps the parsed config, implementing the `DomainAnalyzer` protocol entirely from configuration.
5. **Register by name** -- Each domain is registered under its `name` field.

User domains with the same name as a built-in domain will override the built-in version.

### Selection Process

When a document is processed for extraction:

1. The registry calls `can_analyze(text, filename, metadata)` on every registered domain.
2. Each domain calculates a confidence score based on keyword matches, file extension, doc type, and regex patterns.
3. The domain with the highest confidence above `min_threshold` is selected.
4. If no domain matches, the `generic` domain is used as fallback.

You can also force a specific domain when adding a source file through the UI or API.

## Best Practices

- **Start with detection tuning.** Before writing templates, ensure your keywords and patterns correctly identify documents in your domain. Test with a few real documents and check which domain gets selected.

- **Use weighted keyword groups.** Group related terms together and assign higher weights to more distinctive terms. Terms that could appear in many domains (like "data" or "analysis") should have lower weights.

- **Keep entity types focused.** Define 5-15 node templates that cover the core concepts of your domain. Too many types lead to inconsistent extraction; too few miss important distinctions.

- **Write clear descriptions.** The `description` field on both node and edge templates is included in LLM prompts. Clear, specific descriptions produce better extraction results.

- **Use `normalization_keywords`.** These help the post-processing pipeline map variant type names back to your canonical types. For example, if the LLM outputs "Cooking Method" but your type is "Technique", add "cooking method" to the normalization keywords.

- **Set `requires_named_referent` appropriately.** Set this to `true` for entity types that should always have a proper name (e.g., "Recipe", "Chef"). Set it to `false` for types that can be descriptive (e.g., "Technique", "Finding").

- **Define `source_types` and `target_types` on edges.** These constraints prevent nonsensical relationships. For example, `contains_ingredient` should only go from `Recipe` to `Ingredient`, not from `Chef` to `Equipment`.

- **Use `entity_exclusions`.** Explicitly tell the LLM what NOT to extract. This reduces noise significantly -- especially for measurement values, formatting artifacts, and generic labels.

- **Provide examples.** The `examples` section helps the LLM understand domain-specific patterns like alias recognition and relationship typing. Even 2-3 examples make a noticeable difference.

- **Set `builtin` to `false`.** User-created domains should always set `"builtin": false` to distinguish them from packaged domains.

- **Test with real documents.** After creating your domain, upload a representative document and check the extraction results. Iterate on your guidance, templates, and detection keywords based on actual output.

## See also

- [Architecture: Plugin System](../architecture/plugins.md) — registry pattern, plugin types, and auto-discovery mechanism overview
- [User guide: Extraction Domains](../user-guide/domains.md) — built-in domains reference, detection scoring, and content exclusions
