---
title: Quality Commands
description: Evaluate and report on extraction quality from the CLI — score individual sources, batch analyze databases, and recalculate grades with chaoscypher source quality.
---

# Quality Commands

The `quality` command group evaluates extraction quality for sources. It provides scoring, batch analysis, reporting, and score recalculation.

```bash
chaoscypher source quality --help
```

## Database Selection

The `analyze` and `recalculate` commands accept `--database` / `-d` to target a specific database. The `score` and `report` commands use the active database from `chaoscypher db current` (or the `CHAOSCYPHER_DATABASE` environment variable).

```bash
# Use a specific database
chaoscypher source quality analyze --database my-project
chaoscypher source quality recalculate -d research

# Set the active database for score/report
chaoscypher db switch my-project
chaoscypher source quality score if_abc123
```

---

## Score a Source

Get quality metrics for a single source. The quality grade (v7) is calculated as:

```
With relationships:    Weighted = (R * 0.50) + (E * 0.35) + (T * 0.15)
Without relationships: Weighted = (E * 0.55) + (T * 0.45)
Grade = max(0, Weighted - Pollution Penalty - Structural Penalty)
```

Where **R** = relationship quality, **E** = entity quality, **T** = topology score.
The **Pollution Penalty** (0-15) deducts for low-quality items. The **Structural
Penalty** (0-15, v7+) deducts for graph-shape noise — hub skew and reciprocal
edges — so verbose models can't inflate the grade by padding the graph.

```bash
chaoscypher source quality score SOURCE_ID
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--details` | `-d` | Show individual entity and relationship breakdowns (top 10 each) |
| `--json` | | Output as JSON |

### Examples

**Basic score:**

```bash
chaoscypher source quality score if_abc123
```

```
╭──────── Source ────────╮
│ Research Paper v2      │
│ scientific domain      │
╰────────────────────────╯
╭──── Quality Grade (v7) ──────╮
│      72/100 Excellent        │
╰──────────────────────────────╯
  Grade Calculation: (R*0.50) + (E*0.35) + (T*0.15)
┌──────────────────────────┬───────┬────────┬──────────────┐
│ Component                │ Score │ Weight │ Contribution │
├──────────────────────────┼───────┼────────┼──────────────┤
│ Relationship Quality (R) │  78.3 │    50% │         39.2 │
│ Entity Quality (E)       │  65.0 │    35% │         22.8 │
│ Topology Score (T)       │  71.4 │    15% │         10.7 │
│ Final Grade              │       │        │           72 │
└──────────────────────────┴───────┴────────┴──────────────┘
     Topology Score Breakdown
┌──────────────┬───────────────────────────────┐
│ Metric       │                         Value │
├──────────────┼───────────────────────────────┤
│ Connectivity │  87.5% of entities connected  │
│ Density …    │  1.84 edges/node (target: 2…  │
│ Density Sc…  │                      62.1/100 │
│ Topology S…  │                   71.4/100    │
└──────────────┴───────────────────────────────┘
   Richness Score (Volume Metric)
┌──────────────────────────┬────────┐
│ Metric                   │  Value │
├──────────────────────────┼────────┤
│ Total Score              │ 847.32 │
│ Entity Count             │     24 │
│ Relationship Count       │     31 │
│ Entity Contribution      │ 412.50 │
│ Relationship Contribution│ 389.62 │
│ Connectivity Bonus       │  45.20 │
└──────────────────────────┴────────┘
```

**With entity and relationship details:**

```bash
chaoscypher source quality score if_abc123 --details
```

In addition to the tables above, this appends:

```
      Entity Scores (Top 10)
┌──────────────────────────┬────────────┬───────┐
│ Name                     │ Type       │ Score │
├──────────────────────────┼────────────┼───────┤
│ Neural Network           │ Concept    │  82.4 │
│ Gradient Descent         │ Algorithm  │  76.1 │
│ Backpropagation          │ Process    │  71.3 │
│ Loss Function            │ Concept    │  68.0 │
│ Training Dataset         │ Resource   │  54.2 │
└──────────────────────────┴────────────┴───────┘
    Relationship Scores (Top 10)
┌──────────────────┬──────────────────┬───────────────────┬───────┐
│ Type             │ From             │ To                │ Score │
├──────────────────┼──────────────────┼───────────────────┼───────┤
│ USES             │ Neural Network   │ Gradient Descent  │  85.2 │
│ IMPLEMENTS       │ Gradient Descent │ Backpropagation   │  74.6 │
│ OPTIMIZES        │ Loss Function    │ Neural Network    │  63.8 │
└──────────────────┴──────────────────┴───────────────────┴───────┘
```

**JSON output:**

```bash
chaoscypher source quality score if_abc123 --json
```

```json
{
  "source_id": "if_abc123",
  "source_title": "Research Paper v2",
  "domain": "scientific",
  "entity_count": 24,
  "relationship_count": 31,
  "entity_contribution": 412.5,
  "relationship_contribution": 389.62,
  "connectivity_bonus": 45.2,
  "total_score": 847.32,
  "avg_entity_quality": 65.0,
  "avg_relationship_quality": 78.3,
  "connectivity_ratio": 0.875,
  "low_quality_entity_count": 2,
  "low_quality_relationship_count": 1,
  "quality_grade": 72.0,
  "quality_label": "Excellent",
  "density_ratio": 1.84,
  "density_score": 62.1,
  "topology_score": 71.4,
  "pollution_penalty": 0.0,
  "structural_penalty": 0.0,
  "hub_skew": 1.6,
  "reciprocal_rate": 0.03
}
```

Pass `--details --json` together to include `entity_scores` and `relationship_scores` arrays in the JSON output.

---

## Batch Analysis

Analyze extraction quality across multiple sources with aggregated metrics.

```bash
chaoscypher source quality analyze
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--domain DOMAIN` | | Filter by extraction domain |
| `--min-entities N` | | Minimum entity count to include (default: 0) |
| `--sort {score,entities,quality}` | `-s` | Sort by total score, entity count, or average quality (default: `score`) |
| `--limit N` | `-n` | Number of sources to show (default: 20) |
| `--json` | | Output as JSON |
| `--database NAME` | `-d` | Database name (default: active database) |

### Examples

**Analyze all sources:**

```bash
chaoscypher source quality analyze
```

```
Quality Analysis Summary
Total sources: 5
Average score: 723.40
Average entity quality: 61.25
Average relationship quality: 69.80

        Top 5 Sources by Score
┌──────────────────────────────┬────────────┬──────────┬──────┬───────┬──────────┬────────┐
│ Title                        │ Domain     │ Entities │ Rels │ Score │ Avg Qual │ Conn % │
├──────────────────────────────┼────────────┼──────────┼──────┼───────┼──────────┼────────┤
│ Research Paper v2            │ scientific │       24 │   31 │   847 │     65.0 │    88% │
│ Architecture Overview        │ technical  │       18 │   22 │   756 │     62.4 │    83% │
│ Project Requirements         │ technical  │       15 │   19 │   690 │     58.1 │    80% │
│ Historical Analysis          │ historical │       12 │   14 │   652 │     55.8 │    75% │
│ Meeting Notes                │ generic    │        8 │    6 │   372 │     49.0 │    63% │
└──────────────────────────────┴────────────┴──────────┴──────┴───────┴──────────┴────────┘
* indicates >5 low-quality entities
```

**Filter by domain and sort by quality:**

```bash
chaoscypher source quality analyze --domain technical --sort quality --limit 10
```

**JSON output:**

```bash
chaoscypher source quality analyze --json
```

```json
{
  "sources": [
    {
      "source_id": "if_abc123",
      "title": "Research Paper v2",
      "domain": "scientific",
      "entity_count": 24,
      "relationship_count": 31,
      "total_score": 847.32,
      "avg_entity_quality": 65.0,
      "avg_relationship_quality": 78.3,
      "connectivity_ratio": 0.875,
      "low_quality_entity_count": 2
    }
  ],
  "total_sources": 5,
  "avg_score": 723.4,
  "avg_entity_quality": 61.25,
  "avg_relationship_quality": 69.8
}
```

**Target a specific database:**

```bash
chaoscypher source quality analyze --database my-project --min-entities 5
```

---

## Quality Report

Export a comprehensive quality report including summary statistics, per-source scores, and optional domain comparison.

```bash
chaoscypher source quality report
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--format {table,json,csv}` | `-f` | Output format (default: `table`) |
| `--output FILE` | `-o` | Write to file instead of stdout |
| `--domain DOMAIN` | `-d` | Filter by extraction domain |
| `--include-domains` | | Include domain comparison section in report |

### Examples

**Table report (default):**

```bash
chaoscypher source quality report
```

```
Quality Report Summary
Total sources: 5
Total entities: 77
Total relationships: 92
Average grade: 63.4/100
Average entity quality: 61.25

         Sources by Quality Grade
┌──────────────────────────────┬────────────┬───────┬─────────────┬──────────┬──────┐
│ Title                        │ Domain     │ Grade │ Label       │ Entities │ Rels │
├──────────────────────────────┼────────────┼───────┼─────────────┼──────────┼──────┤
│ Research Paper v2            │ scientific │    72 │ Excellent   │       24 │   31 │
│ Architecture Overview        │ technical  │    68 │ Good        │       18 │   22 │
│ Project Requirements         │ technical  │    61 │ Good        │       15 │   19 │
│ Historical Analysis          │ historical │    58 │ Good        │       12 │   14 │
│ Meeting Notes                │ generic    │    42 │ Fair        │        8 │    6 │
└──────────────────────────────┴────────────┴───────┴─────────────┴──────────┴──────┘
```

**With domain comparison:**

```bash
chaoscypher source quality report --include-domains
```

This appends a domain comparison table:

```
         Domain Comparison
┌────────────┬─────────┬───────────┬──────────┬─────────────┐
│ Domain     │ Sources │ Avg Grade │ Entities │ Avg Quality │
├────────────┼─────────┼───────────┼──────────┼─────────────┤
│ scientific │       1 │        72 │       24 │        65.0 │
│ technical  │       2 │        65 │       33 │        60.3 │
│ historical │       1 │        58 │       12 │        55.8 │
│ generic    │       1 │        42 │        8 │        49.0 │
└────────────┴─────────┴───────────┴──────────┴─────────────┘
```

**Export as JSON to file:**

```bash
chaoscypher source quality report --format json -o quality.json
```

```
Report written to quality.json
```

The JSON file contains:

```json
{
  "summary": {
    "total_sources": 5,
    "total_entities": 77,
    "total_relationships": 92,
    "avg_grade": 63.4,
    "avg_entity_quality": 61.25
  },
  "sources": [
    {
      "source_id": "if_abc123",
      "title": "Research Paper v2",
      "domain": "scientific",
      "quality_grade": 72.0,
      "quality_label": "Excellent",
      "entity_count": 24,
      "relationship_count": 31,
      "total_score": 847.32,
      "entity_contribution": 412.5,
      "relationship_contribution": 389.62,
      "connectivity_bonus": 45.2,
      "avg_entity_quality": 65.0,
      "avg_relationship_quality": 78.3,
      "connectivity_ratio": 0.875,
      "low_quality_entity_count": 2,
      "low_quality_relationship_count": 1
    }
  ],
  "domains": [
    {
      "domain": "scientific",
      "source_count": 1,
      "total_entities": 24,
      "total_relationships": 31,
      "avg_grade": 72.0,
      "avg_entity_quality": 65.0,
      "avg_relationship_quality": 78.3
    }
  ]
}
```

:::note[The `domains` key is only included when `--include-domains` is passed.]



:::

**Export as CSV:**

```bash
chaoscypher source quality report --format csv -o quality.csv
```

Writes one row per source with columns: `source_id`, `title`, `domain`, `quality_grade`, `quality_label`, `entity_count`, `relationship_count`, `total_score`, `entity_contribution`, `relationship_contribution`, `connectivity_bonus`, `avg_entity_quality`, `avg_relationship_quality`, `connectivity_ratio`, `low_quality_entity_count`, `low_quality_relationship_count`.

---

## Recalculate Scores

Force recalculation and caching of quality scores in the database. This is useful when the scoring algorithm has been updated, or sources have outdated or missing cached scores.

```bash
chaoscypher source quality recalculate
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--domain DOMAIN` | | Only recalculate sources in this domain |
| `--outdated-only` | | Only recalculate sources with outdated or missing cached scores |
| `--source-id ID` | `-s` | Recalculate specific source(s) by ID (can be repeated) |
| `--database NAME` | `-d` | Database name (default: active database) |

### Examples

**Recalculate all sources:**

```bash
chaoscypher source quality recalculate
```

```
Recalculating quality scores for 5 source(s)
Scoring algorithm version: 7

⠋ Processing: Research Paper v2...
⠙ Processing: Architecture Overview...
⠹ Processing: Project Requirements...
⠸ Processing: Historical Analysis...
⠼ Processing: Meeting Notes...

Recalculation Complete
Successfully processed: 5
```

**Only recalculate outdated scores:**

```bash
chaoscypher source quality recalculate --outdated-only
```

```
Recalculating quality scores for 2 source(s)
Scoring algorithm version: 7

⠋ Processing: Historical Analysis...
⠙ Processing: Meeting Notes...

Recalculation Complete
Successfully processed: 2
```

**Recalculate specific sources:**

```bash
chaoscypher source quality recalculate -s if_abc123 -s if_xyz789
```

**Filter by domain:**

```bash
chaoscypher source quality recalculate --domain technical
```

**Target a specific database:**

```bash
chaoscypher source quality recalculate --database my-project --outdated-only
```

---

## Quality Grades

The v7 scoring algorithm produces a grade from 0 to 100 with the following labels:

| Grade Range | Label |
|-------------|-------|
| 85 -- 100 | Outstanding |
| 70 -- 84 | Excellent |
| 50 -- 69 | Good |
| 30 -- 49 | Fair |
| 0 -- 29 | Low |

See the [Quality Analysis user guide](../../user-guide/quality.md) for details on the scoring methodology.
