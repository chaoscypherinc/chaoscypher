---
slug: domain-extraction-guide
title: "Extract Smarter: How Domain-Aware AI Builds Better Knowledge Graphs"
authors: [denis]
tags: [tutorials, ai]
date: 2026-03-12
description: How domain-specific extraction in Chaos Cypher produces typed entities and meaningful relationships instead of generic, queryless knowledge graphs.
---

Most AI extraction tools treat every document the same way. Upload a medical paper or a legal contract and you get the same generic entity types, the same vague relationships, the same disappointing graph. Chaos Cypher takes a different approach: it detects what kind of document you uploaded and adapts its entire extraction pipeline to match.

<!-- truncate -->

## The Problem with Generic Extraction

Here's a sentence you might find in a clinical document:

> Patient with hypertension started on lisinopril 10mg daily. The ACE inhibitor is contraindicated with potassium supplements. Side effects include dry cough and dizziness.

A generic extraction pipeline -- the kind most tools use -- will pull out a handful of entities and connect them with whatever relationship labels the LLM feels like inventing. You might get "Lisinopril" typed as an **Item**, "Hypertension" as a **Concept**, and "Dry Cough" as another **Concept**. The relationships between them? Probably `related_to` and `influences`. Maybe `associated_with` if you are lucky.

This is the "garbage in, garbage out" of knowledge graphs. It's not that the AI failed to read the text. It read it fine. The problem is that nobody told it what to look for, what types are valid, or what the relationships between those types should mean.

The graph you get is technically correct and practically useless. You cannot query "which drugs treat hypertension" because the system does not know what a Drug is. You cannot find contraindications because `related_to` could mean anything. Every edge in the graph carries the same semantic weight as a shrug.

Now run the same sentence through Chaos Cypher with the **medical** domain active:

- **Lisinopril** becomes a **Drug** with dosage form and mechanism of action as properties
- **Hypertension** becomes a **Condition**
- **Dry Cough** and **Dizziness** become **Side Effects**
- **Potassium Supplements** gets recognized as a **Drug** (because supplements have drug interactions too)

The relationships are just as precise: `treats`, `contraindicated_with`, `produces_side_effect`. Each one is typed, directional, and constrained. Only a Drug or Treatment can `treat` a Condition. Only a Drug can `produce_side_effect` on a Side Effect. The LLM isn't guessing -- it's following a schema.

That's what domain-aware extraction does. It turns a language model from a general-purpose pattern matcher into a domain specialist.

![Source detail showing entity and relationship distribution charts](/img/screenshots/source-detail-overview.png)

## How It Works: Upload to Knowledge Graph

The workflow is straightforward. You upload a document. Chaos Cypher figures out what domain it belongs to, loads the right extraction rules, and runs the pipeline. You don't need to configure anything upfront -- though you can override the detected domain if you want.

Here's what happens behind the scenes:

1. **Detection** -- Chaos Cypher samples the first few thousand characters of your document and scores it against all registered domains simultaneously. Each domain has weighted keyword groups, regex patterns, and file type signals. The highest-scoring domain wins.

2. **Guidance injection** -- The winning domain's extraction rules get injected into the LLM prompt. This includes entity type definitions, relationship constraints, exclusion rules (what *not* to extract), and worked examples of correct extractions.

3. **Strict type enforcement** -- The LLM is instructed to only use entity types from the domain's template list. After extraction, a code-level filter drops any entity whose type does not match a known template. No hallucinated types survive.

4. **Relationship validation** -- Each relationship is checked against source/target type constraints. A `treats` relationship must flow from a Drug, Treatment, or Procedure to a Condition or Symptom. Anything else gets rejected.

5. **Quality scoring** -- Extracted entities and relationships are scored by domain relevance. Domain-specific types like Drug and Condition score higher than generic fallbacks. This surfaces the most valuable parts of your graph.

![Add Source dialog with URL input and file drag-and-drop](/img/screenshots/sources-upload-dialog.png)

Chaos Cypher ships with **19 built-in domains**, each tuned for a different category of document:

| Domain | Typical Entity Types | Best For |
|--------|---------------------|----------|
| **Biographical** | Person, Life Event, Achievement, Relationship | Biographies, memoirs, personal histories |
| **Cybersecurity** | Threat Actor, Vulnerability, Malware, Attack Technique | Threat intel, incident reports, CVE research |
| **Design** | Designer, Design System, Component, Design Principle, Design Pattern | UI/UX design books, design systems, typography, color theory, accessibility guidelines |
| **Educational** | Course, Learning Objective, Concept, Assessment | Textbooks, curricula, instructional materials |
| **Financial** | Company, Financial Instrument, Market Event, Regulation | Earnings reports, market analysis, SEC filings |
| **Generic** | Person, Organization, Event, Concept, Location | General-purpose fallback for any content |
| **Historical** | Historical Figure, Event, Treaty, Dynasty, Territory | Primary sources, historiography, timelines |
| **Intelligence** | Agency, Program, Operation, Intelligence Report, Source | Declassified intelligence documents, FOIA releases, briefings, agency cables, dossiers |
| **Investigation** | Suspect, Evidence, Witness, Case, Incident | Criminal/civil investigations, case files, forensics |
| **Legal** | Statute, Case, Party, Obligation, Legal Principle | Contracts, court opinions, regulatory filings |
| **Literary** | Character, Setting, Theme, Plot Element | Novels, poetry, drama, literary criticism |
| **Medical** | Drug, Condition, Symptom, Procedure, Side Effect | Clinical documents, pharmaceutical literature |
| **News** | Person, Organization, Event, Statement, Policy | News articles, press releases, journalism |
| **Philosophical** | Philosopher, Argument, Concept, School of Thought | Philosophy texts across global traditions |
| **Political** | Political Entity, Policy, Election, Legislation | Governance docs, political theory, policy analysis |
| **Reference** | Standard, Specification, Requirement, API Reference, Manual | Standards (RFC/ISO/IEEE/W3C), technical specs, API references, user/admin manuals |
| **Scientific** | Hypothesis, Method, Finding, Dataset, Organism | Research papers, experiments, academic publications |
| **Technical** | Module, Class, Function, Endpoint, Design Pattern | API docs, codebases, technical specifications |
| **Theological** | Deity, Scripture, Doctrine, Ritual, Religious Figure | Sacred texts, theology, comparative religion |

Every domain uses strict entity type enforcement by default. The medical domain defines 20 entity types. The technical domain has 18. These aren't suggestions -- they're the only types the LLM is allowed to produce. That constraint is what separates a clean, queryable graph from a noisy soup of ad-hoc labels.

## Under the Hood: Domain Detection and Extraction Quality

### How Detection Works

Domain detection runs a scoring algorithm across all registered domains simultaneously. Each domain defines its detection rules in a JSON-LD config file with three signal types:

**Weighted keyword groups.** The medical domain has six keyword groups: `clinical_core` (weight 1.2), `pharmaceutical` (weight 1.0), `diagnostic` (weight 0.9), `anatomy` (weight 0.8), `procedures` (weight 0.9), and `clinical_terms` (weight 0.8). Each keyword match boosts the confidence score by `per_keyword_boost * weight`. A document full of "diagnosis", "treatment", and "symptoms" racks up points fast in the clinical_core group, while scattered mentions of "cardiac" and "pulmonary" add smaller anatomy-weighted boosts.

**Regex patterns.** Keywords catch common terms, but patterns catch domain-specific notation. The medical domain matches dosage expressions like `\d+\s*(mg|mcg|ml)`, ICD codes like `ICD-10:J45`, and prescription abbreviations like `b.i.d.` and `p.r.n.`. Each pattern match carries its own weight -- dosage notation at 1.4x, ICD codes at 1.5x. A single ICD code in a document is a strong medical signal.

**File and document type signals.** File extensions (`.py` for technical) and document type metadata (`medical_document`, `openapi`) provide additional boosts.

The final confidence score is compared against a per-domain minimum threshold. Medical requires 0.4 minimum confidence. The generic domain has a threshold of 0.0 -- it always matches as a fallback, but with the lowest possible score (0.1), so any specialized domain that passes its threshold will win.

### How Domains Shape Extraction Quality

Detection picks the right domain. But the real value is in what happens next -- how the selected domain controls the extraction pipeline.

**Entity guidance tells the LLM what to extract and what to skip.** The medical domain instructs: "Extract conditions, symptoms, treatments, drugs, procedures, and anatomical locations. Include dosage information as properties on drug entities." It also lists explicit exclusion rules: don't extract dosage numbers alone ("500mg" is a property of a Drug, not a standalone entity), don't extract study references ("Figure 1", "Table 2"), don't extract administrative codes as entities.

**Strict type enforcement prevents hallucinated types.** When strict mode is on -- and it is on for all 18 specialized domains -- the LLM receives a closed list of valid entity types. The medical domain allows exactly 20 types: Condition, Symptom, Treatment, Drug, Procedure, Diagnostic Test, Anatomy, Pathogen, Clinical Trial, Dosage, Side Effect, Risk Factor, Gene, Biomarker, Patient Population, Guideline, Protocol, Outcome, Endpoint, and Mechanism of Action. Anything the LLM produces outside that list gets dropped in post-processing. No more "Medical Concept" or "Health Thing" cluttering your graph.

**Relationship constraints validate source and target combinations.** The medical domain's `treats` relationship is constrained: source must be Drug, Treatment, or Procedure; target must be Condition or Symptom. If the LLM tries to say a Symptom `treats` a Drug, the relationship fails validation. This catches the most common extraction error -- reversed or semantically nonsensical edges.

**Compatibility groups enable smart deduplication.** When the same entity appears in different chunks with slightly different types -- "Hypertension" as a Condition in one chunk and as a "Medical Concept" in another -- the compatibility groups determine whether they can be merged. In the medical domain, Condition and Symptom share the `clinical` group, so they are merge-eligible. Drug, Treatment, and Procedure share the `treatment` group. This prevents duplicate entities without losing type precision.

**Property type mapping rescues mistyped entities.** Sometimes the LLM extracts "Severity" as a standalone entity when it should be a property on a Condition. The medical domain's property mapping knows that "Severity" should be absorbed into Condition as a `severity` property, and "Mechanism" into Drug as a `mechanism` property. Instead of cluttering the graph with orphaned attribute nodes, they get folded into the right place.

## Try It Yourself

Every built-in domain is just a JSON-LD file. No Python, no compilation, no framework code. If you need a domain for your field that doesn't exist yet, you can create one in about 20 minutes.

Let's build a **startup** domain for analyzing pitch decks, funding announcements, and tech industry news.

Create a file called `startup.jsonld` in your `data/plugins/domains/` directory:

```json
{
  "@context": {
    "@vocab": "https://chaoscypher.io/schema/domain#",
    "schema": "https://schema.org/",
    "name": "schema:name",
    "description": "schema:description"
  },
  "@type": "ExtractionDomain",
  "@id": "domain:startup",

  "name": "startup",
  "version": "1.0.0",
  "description": "Startup ecosystem: funding, founders, products, and acquisitions",
  "strict_entity_types": true,

  "detection": {
    "keywords": {
      "funding": {
        "terms": ["series A", "series B", "seed round", "venture capital",
                  "valuation", "fundraise", "runway", "cap table"],
        "weight": 1.3
      },
      "ecosystem": {
        "terms": ["startup", "founder", "co-founder", "incubator",
                  "accelerator", "pivot", "product-market fit", "MVP"],
        "weight": 1.1
      }
    },
    "patterns": [
      {"regex": "\\$\\d+[MBK]\\s+(seed|series|round|valuation)", "weight": 1.5},
      {"regex": "(?i)Y Combinator|Techstars|500 Startups", "weight": 1.3}
    ],
    "confidence": {
      "base_score": 0.2,
      "per_keyword_boost": 0.05,
      "pattern_boost": 0.15,
      "min_threshold": 0.4
    }
  },

  "entity_guidance": "Extract companies, founders, investors, funding rounds, and products. Attach dollar amounts and dates as properties on Funding Round entities, not as standalone entities.",

  "templates": {
    "node_templates": [
      {
        "id": "startup_company", "name": "Company",
        "description": "A startup, corporation, or business entity",
        "requires_named_referent": true,
        "quality_score": 25,
        "properties": [
          {"name": "stage", "display_name": "Stage", "property_type": "text"},
          {"name": "industry", "display_name": "Industry", "property_type": "text"}
        ]
      },
      {
        "id": "startup_person", "name": "Founder",
        "description": "A founder, co-founder, or key executive",
        "requires_named_referent": true,
        "quality_score": 25
      },
      {
        "id": "startup_investor", "name": "Investor",
        "description": "A VC firm, angel investor, or investment entity",
        "requires_named_referent": true,
        "quality_score": 25
      },
      {
        "id": "startup_round", "name": "Funding Round",
        "description": "A specific funding event (seed, Series A, etc.)",
        "requires_named_referent": false,
        "quality_score": 25,
        "properties": [
          {"name": "amount", "display_name": "Amount", "property_type": "text"},
          {"name": "date", "display_name": "Date", "property_type": "date"}
        ]
      },
      {
        "id": "startup_product", "name": "Product",
        "description": "A software product, platform, or service",
        "requires_named_referent": true,
        "quality_score": 18
      }
    ],
    "edge_templates": [
      {
        "id": "startup_founded_by", "name": "founded_by",
        "description": "Company was founded by a person",
        "inverse": "founded",
        "source_types": ["Company"], "target_types": ["Founder"]
      },
      {
        "id": "startup_invested_in", "name": "invested_in",
        "description": "Investor participated in a funding round",
        "inverse": "funded_by",
        "source_types": ["Investor"], "target_types": ["Funding Round"]
      },
      {
        "id": "startup_raised", "name": "raised",
        "description": "Company raised a funding round",
        "inverse": "round_for",
        "source_types": ["Company"], "target_types": ["Funding Round"]
      },
      {
        "id": "startup_acquired_by", "name": "acquired_by",
        "description": "Company was acquired by another company",
        "inverse": "acquired",
        "source_types": ["Company"], "target_types": ["Company"]
      },
      {
        "id": "startup_builds", "name": "builds",
        "description": "Company builds or maintains a product",
        "inverse": "built_by",
        "source_types": ["Company"], "target_types": ["Product"]
      }
    ]
  }
}
```

A few things to notice about this file:

**The `detection` section** defines how Chaos Cypher recognizes startup content. The keyword groups are weighted -- "series A" and "venture capital" in the `funding` group carry more weight (1.3x) than general ecosystem terms (1.1x). The regex patterns catch dollar-amount-plus-round expressions like "$50M Series B" at 1.5x weight. These signals stack: a pitch deck mentioning several funding terms and a dollar figure will score well above the 0.4 threshold.

**The `templates` section** defines the vocabulary. Five entity types, five relationship types. Each entity template has an `id`, a `name` (the type label that appears in the graph), and a `description` that helps the LLM understand what qualifies. The `requires_named_referent` flag tells the system whether an entity needs a proper name -- a Company does, but a Funding Round does not (it can be "Series A round" or just "the seed round"). Properties like `amount` and `stage` get attached to entities rather than floating as separate nodes.

**The `edge_templates`** constrain which entity types can appear on each side of a relationship. `founded_by` only flows from Company to Founder. `invested_in` only flows from Investor to Funding Round. The `inverse` field defines the reverse label for bidirectional traversal.

Restart Chaos Cypher and your domain is live. Upload a TechCrunch article or a pitch deck and watch the detection engine pick it up. Your custom entity types appear in the graph, constrained by the relationships you defined.

<!-- SCREENSHOT: Custom domain JSON-LD file in editor showing structure — detection keywords, node templates, edge templates. -->

![Source extraction view showing domain-specific entity types](/img/screenshots/source-extraction-entities.png)

If you need to go deeper, the built-in domains show what else is possible: normalization keywords that fix LLM type inconsistencies, compatibility groups for smart deduplication, property type mappings that absorb mistyped entities, alias examples that teach the LLM about synonym handling, and extraction limits that tune relationship density. The medical domain is the most comprehensive example -- it defines 20 entity types, 20 relationship types, dosage regex patterns, ICD code detection, and evidence validation in strict mode. Study it when you want the full picture.

## What's Next

We're planning more specialized domains -- supply chain, environmental science, and music theory are on the shortlist. But the real potential is in what users build. Every field has its own vocabulary, its own entity types, its own relationship patterns. A materials scientist cares about Crystal Structure, Synthesis Method, and Property. A genealogist needs Person, Family, Vital Record, and Census Entry. A cybersecurity analyst -- who already has a built-in domain -- might want to fork it and add types specific to their organization's threat model.

If you build a domain for your field, share it. A JSON-LD file is small, portable, and easy to review. Drop it in `data/plugins/domains/` and it works. No pull request required to use it, but we would love to include community domains in the built-in set for others to benefit from.

Domains work identically whether you're running [locally with Ollama](/blog/local-ai-knowledge-graph) or with a cloud provider. The domain system documentation covers the full JSON-LD schema, all available configuration options, and advanced features like extraction density tuning and evidence validation modes. Start with the five-entity example above, test it on your documents, and iterate from there.
