---
id: domains
title: Extraction Domains
description: Configure AI entity extraction with domain plugins — 19 built-in domains (medical, legal, scientific, and more) define entity types, relationship types, and LLM guidance.
---

# Extraction Domains

Extraction domains configure how the AI extracts entities and relationships from your documents. Each domain is a `.jsonld` file that defines entity types, relationship types, detection rules, quality scoring, and LLM guidance -- **no Python code required**.

## How Domains Work

When a document is processed, Chaos Cypher:

1. **Detects** the best domain by scoring keywords, file extensions, and patterns against the document content
2. **Injects** domain-specific guidance into the LLM extraction prompt
3. **Constrains** extraction output using the domain's templates and rules
4. **Validates** results using quality scoring, deduplication, and type compatibility

If no domain matches with sufficient confidence, the `generic` domain is used as a fallback.

### Domain Detection

Detection works by sampling document content (first 3 chunks + middle 2 chunks, up to 8000 characters) and scoring each domain:

| Factor | What It Checks | Example |
|--------|---------------|---------|
| **Keywords** | Domain-specific terms in text | "plaintiff", "verdict" for legal |
| **File extensions** | Source file type | `.py` for technical |
| **Doc type** | Metadata-based type hints | "research_paper" for scientific |
| **Regex patterns** | Structural patterns | Birth/death date ranges for biographical |

The domain with the highest confidence score above its threshold wins. If none match, `generic` is used.

## Built-In Domains

| Domain | Description | Strict Types | Density |
|--------|-------------|:------------:|:-------:|
| **generic** | General-purpose fallback for any content | No | 1.0 |
| **technical** | Code, APIs, software documentation | Yes | 1.2 |
| **scientific** | Research papers, studies, experiments | Yes | 1.3 |
| **biographical** | Biographies, memoirs, life stories | Yes | 1.0 |
| **literary** | Fiction, poetry, drama | Yes | 1.0 |
| **medical** | Medical and healthcare content | Yes | 1.2 |
| **legal** | Legal documents, contracts, regulations | Yes | 1.25 |
| **financial** | Financial documents, reports, analysis | Yes | 1.2 |
| **historical** | Historical texts and analysis | Yes | 1.1 |
| **political** | Political content, speeches, policy | Yes | 1.1 |
| **educational** | Educational content, courses, curricula | Yes | 1.1 |
| **news** | News articles, journalism | Yes | 0.95 |
| **theological** | Religious texts, theology | Yes | 1.15 |
| **philosophical** | Philosophy, ethics, epistemology | Yes | 1.15 |
| **cybersecurity** | Security documentation, threats, CVEs | Yes | 1.2 |
| **investigation** | Criminal/civil investigations, case files, evidence | Yes | 1.3 |
| **design** | UI/UX design, design systems, typography, color theory, accessibility | Yes | 1.2 |
| **intelligence** | Declassified intel, FOIA releases, briefings, dossiers, UAP/UFO reports | Yes | 1.3 |
| **reference** | Standards (RFC/ISO/IEEE/W3C), specs, API references, manuals, release notes | Yes | 1.2 |

**Strict Types** means only entity types defined in the domain's templates are allowed -- the LLM cannot invent new types. The `generic` domain allows any type.

**Density** controls how many entities/relationships the LLM is expected to extract per chunk. Higher density domains (scientific: 1.3, investigation: 1.3, intelligence: 1.3) produce more detailed graphs. Lower density (news: 0.95) produces leaner, focused graphs.

## Extraction Limits

Each domain defines hard caps that prevent runaway LLM generation and control graph density:

| Setting | What It Controls | Example Range |
|---------|-----------------|---------------|
| `max_entity_degree` | Max relationships per entity (in + out combined) | 15 (news) -- 40 (literary) |
| `max_same_source_type` | Max relationships with same (source, relationship type) pair | 6 (news) -- 12 (literary) |
| `max_relationship_ratio` | Max relationships as a multiplier of entity count | 5.0 (news) -- 8.0 (most domains) |
| `loop_max_entity_count` | Max entities per chunk before aborting LLM streaming | 25 (news) -- 50 (literary) |

These limits are enforced in three passes during extraction finalization:

1. **Same-pair cap** -- Keeps highest-confidence relationships when a (source entity, relationship type) pair exceeds the limit
2. **Degree cap** -- Prevents any single entity from having too many connections, with orphan protection for entities that would otherwise have zero edges
3. **Total cap** -- Limits total relationship count to `max_relationship_ratio x entity_count`, again with orphan protection

The `generic` domain uses global defaults (degree: 25, same-type: 12, ratio: 8.0, loop: 50). Specialized domains tune these based on content characteristics -- news articles get tighter limits to avoid over-connecting, while literary works allow denser graphs.

## Entity Exclusion Rules

Each domain specifies what the LLM should *not* extract to reduce noise:

| Domain | Excluded Items |
|--------|---------------|
| **biographical** | Bare date ranges ("1920--1985"), generic familial roles ("the father"), source citations ("[1]") |
| **educational** | Structural markers ("Chapter 1"), boilerplate ("In this chapter you will learn"), generic refs ("the student") |
| **financial** | Raw numbers alone ("$5M"), ticker symbols without context ("AAPL"), boilerplate disclaimers |
| **legal** | Paragraph numbers ("Section 3.1"), procedural boilerplate ("hereby"), citation formatting ("Id.", "supra") |
| **technical** | Import statements, version numbers, code boilerplate, generic comments |
| **investigation** | Report headers/footers, generic role refs ("the officer"), form instructions |

## Content Exclusions

Domains define which [content categories](../architecture/extraction-pipeline/overview.md#content-filtering) to strip before extraction. For example, the `technical` domain excludes `toc`, `changelog`, `legal`, `boilerplate`, `api_tables`, and `web_artifacts` because these rarely contain extractable entities.

Exclusion configuration in domain `.jsonld` files:

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

- **categories** -- References built-in category names (15 available: toc, changelog, legal, bibliography, acknowledgments, boilerplate, metadata, code_blocks, data_tables, math, api_tables, procedural, advertising, web_artifacts, bulk_lists)
- **custom_patterns** -- Domain-specific regex patterns with mode (`count` to exclude whole chunks, `line_ratio` to strip matching lines) and threshold

## Custom Domains

Place a `.jsonld` file in `data/plugins/domains/` and it will be auto-discovered on startup:

```
data/
  plugins/
    domains/
      my_domain.jsonld
```

**Minimal domain:**

```json
{
  "@context": { "@vocab": "https://chaoscypher.io/schema/domain#" },
  "@type": "ExtractionDomain",
  "name": "my_domain",
  "version": "1.0.0",
  "description": "Custom domain for my content type",
  "extraction_density": 1.0,
  "strict_entity_types": true,

  "detection": {
    "keywords": {
      "primary": { "terms": ["keyword1", "keyword2"], "weight": 1.0 }
    },
    "confidence": {
      "base_score": 0.25,
      "per_keyword_boost": 0.04,
      "min_threshold": 0.4
    }
  },

  "entity_guidance": "Extract entities relevant to my domain...",
  "relationship_guidance": "Focus on these relationship types...",

  "templates": {
    "node_templates": [
      {
        "id": "my_entity",
        "name": "My Entity",
        "description": "Description of this entity type",
        "quality_score": 20
      }
    ],
    "edge_templates": [
      {
        "id": "my_relationship",
        "name": "relates_to",
        "description": "How entities relate",
        "quality_score": 15
      }
    ]
  },

  "extraction_limits": {
    "max_entity_degree": 20,
    "max_same_source_type": 8,
    "max_relationship_ratio": 6.0,
    "loop_max_entity_count": 35
  }
}
```

For the full domain configuration schema and advanced patterns like type compatibility groups, property absorption, and evidence validation modes, see the [Building Extraction Domains](../developer-guide/building-domains.md) guide.

## Reclassifying a Source

If auto-detection chose the wrong domain, or if you want to re-run extraction with a different domain after reviewing the initial results, use the **reclassify** action on the source detail page — or call the API directly.

Reclassification:
1. Resets any prior graph artifacts for committed sources (atomically — the graph stays consistent even if the process is interrupted).
2. Queues a new extraction pass using the specified domain.

```bash
# Reclassify src_abc123 under the "medical" domain
curl -X POST http://localhost:8080/api/v1/sources/src_abc123/reclassify \
  -H "Content-Type: application/json" \
  -d '{"domain": "medical"}'
```

**Eligible source states:** `indexed` (extraction never run or was cancelled) or `committed` (full re-extraction).

This is the preferred approach over passing `domain` at upload time, because domain selection is often only meaningful after you can inspect the document content.
