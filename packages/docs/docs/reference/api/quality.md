---
title: Quality API
description: Score and monitor extraction quality — grade sources, compare domains, analyze batch results, and track quality trends across your knowledge graph.
---

# Quality

Evaluate and monitor the quality of entity and relationship extractions across sources. The Quality API provides scoring, analysis, and comparison tools to identify high- and low-performing extractions and track quality trends across domains.

**Base path:** `/api/v1/quality`

---

## Score Source

```
GET /api/v1/quality/sources/{source_id}
```

Score a single source's extraction quality. Returns quality metrics including entity and relationship contributions, connectivity, density, and pollution indicators. Uses cached scores when available for performance.

### Path Parameters

| Parameter   | Type   | Required | Description                 |
|-------------|--------|----------|-----------------------------|
| `source_id` | string | Yes      | ID of the source to score   |

### Query Parameters

| Parameter           | Type | Required | Default | Description                                    |
|---------------------|------|----------|---------|------------------------------------------------|
| `force_recalculate` | bool | No       | `false` | Bypass cache and recalculate fresh scores      |

### Example

```bash
# Score a source (uses cache if available)
curl "http://localhost/api/v1/quality/sources/src-abc123"

# Force recalculation
curl "http://localhost/api/v1/quality/sources/src-abc123?force_recalculate=true"
```

### Response

`200 OK`

```json
{
  "source_id": "src-abc123",
  "source_title": "Research Paper on Neural Networks",
  "domain": "science",
  "entity_count": 45,
  "relationship_count": 62,
  "entity_contribution": 2847.5,
  "relationship_contribution": 3102.0,
  "connectivity_bonus": 150.0,
  "total_score": 6099.5,
  "avg_entity_quality": 63.28,
  "avg_relationship_quality": 50.03,
  "connectivity_ratio": 0.82,
  "quality_grade": 72.5,
  "quality_label": "Good",
  "low_quality_entity_count": 3,
  "low_quality_relationship_count": 8,
  "density_ratio": 1.38,
  "density_score": 68.9,
  "topology_score": 75.4,
  "pollution_penalty": 2.5,
  "structural_penalty": 0.0,
  "hub_skew": 1.4,
  "reciprocal_rate": 0.05,
  "coverage_score": 61.3
}
```

#### SourceQualityScoreResponse

| Field                            | Type         | Description                                                    |
|----------------------------------|--------------|----------------------------------------------------------------|
| `source_id`                      | string       | ID of the source                                               |
| `source_title`                   | string\|null | Title of the source                                            |
| `domain`                         | string\|null | Extraction domain used                                         |
| `entity_count`                   | int          | Number of entities                                             |
| `relationship_count`             | int          | Number of relationships                                        |
| `entity_contribution`            | float        | Sum of quality-weighted entity scores                          |
| `relationship_contribution`      | float        | Sum of quality-weighted relationship scores                    |
| `connectivity_bonus`             | float        | Bonus for connected entities                                   |
| `total_score`                    | float        | Richness score (unbounded, quantity-driven)                    |
| `avg_entity_quality`             | float        | Average quality per entity (0--100)                            |
| `avg_relationship_quality`       | float        | Average quality per relationship (0--100)                      |
| `connectivity_ratio`             | float        | Ratio of connected entities (0--1)                             |
| `quality_grade`                  | float        | Quality rating 0--100 (independent of volume)                  |
| `quality_label`                  | string       | Quality label: Outstanding, Excellent, Good, Fair, or Low      |
| `low_quality_entity_count`       | int          | Entities with score below 40 (inflation indicator)             |
| `low_quality_relationship_count` | int          | Relationships with score below 40                              |
| `density_ratio`                  | float        | Relationships per entity ratio                                 |
| `density_score`                  | float        | Density score (bell-shaped around target, 0--100; over-dense graphs are penalized) |
| `topology_score`                 | float        | Combined connectivity + density score (0--100)                 |
| `pollution_penalty`              | float        | Penalty for low-quality items (0--15)                          |
| `structural_penalty`             | float        | Penalty for graph-shape noise: hub skew + reciprocal rate (0--15) |
| `hub_skew`                       | float        | max_entity_degree ÷ median_entity_degree (≥1.0; high = one entity over-connected) |
| `reciprocal_rate`                | float        | Fraction of edges with a same-type reciprocal partner (0--1)   |
| `coverage_score`                 | float        | Entities per chunk normalized to 0--100                        |

### Errors

| Status | Description        |
|--------|--------------------|
| `404`  | Source not found    |

---

## Score Source Details

```
GET /api/v1/quality/sources/{source_id}/details
```

Score a source with detailed entity and relationship breakdowns. Returns the same top-level metrics as the score endpoint plus individual score breakdowns for every entity and relationship.

:::note[Cache behavior]

Detail view always requires calculation as individual breakdowns are not cached.

:::

### Path Parameters

| Parameter   | Type   | Required | Description                 |
|-------------|--------|----------|-----------------------------|
| `source_id` | string | Yes      | ID of the source to score   |

### Query Parameters

| Parameter           | Type | Required | Default | Description                                    |
|---------------------|------|----------|---------|------------------------------------------------|
| `force_recalculate` | bool | No       | `false` | Bypass cache and recalculate fresh scores      |

### Example

```bash
curl "http://localhost/api/v1/quality/sources/src-abc123/details"
```

### Response

`200 OK`

Returns a [SourceQualityScoreResponse](#sourcequalityscoreresponse) with two additional fields — `entity_scores` and `relationship_scores`:

```json
{
  "source_id": "src-abc123",
  "source_title": "Research Paper on Neural Networks",
  "...": "... same fields as SourceQualityScoreResponse ...",
  "entity_scores": [
    {
      "entity_name": "Convolutional Neural Network",
      "entity_type": "Concept",
      "description_score": 18.0,
      "confidence_score": 13.5,
      "cross_chunk_score": 12.0,
      "properties_score": 10.5,
      "aliases_score": 7.0,
      "type_value_score": 25.0,
      "total_score": 86.0
    }
  ],
  "relationship_scores": [
    {
      "relationship_type": "trained_on",
      "source_entity": "Convolutional Neural Network",
      "target_entity": "ImageNet",
      "justification_score": 30.0,
      "confidence_score": 22.5,
      "specificity_score": 20.0,
      "valid_refs_score": 15.0,
      "total_score": 87.5
    }
  ]
}
```

#### SourceQualityDetailResponse

Extends [SourceQualityScoreResponse](#sourcequalityscoreresponse) with two additional fields:

| Field                 | Type                              | Description                              |
|-----------------------|-----------------------------------|------------------------------------------|
| `entity_scores`       | `EntityQualityScoreResponse[]`    | Individual entity score breakdowns       |
| `relationship_scores` | `RelationshipQualityScoreResponse[]` | Individual relationship score breakdowns |

#### EntityQualityScoreResponse

| Field              | Type   | Description                                  |
|--------------------|--------|----------------------------------------------|
| `entity_name`      | string | Name of the entity                           |
| `entity_type`      | string | Type of the entity                           |
| `description_score`| float  | Score for description richness (0--20)       |
| `confidence_score` | float  | Score for extraction confidence (0--15)      |
| `cross_chunk_score`| float  | Score for cross-chunk mentions (0--15)       |
| `properties_score` | float  | Score for property richness (0--15)          |
| `aliases_score`    | float  | Score for alias count (0--10)                |
| `type_value_score` | float  | Score based on entity type tier (0--25)      |
| `total_score`      | float  | Sum of all component scores (0--100)         |

#### RelationshipQualityScoreResponse

| Field                | Type   | Description                                      |
|----------------------|--------|--------------------------------------------------|
| `relationship_type`  | string | Type of the relationship                         |
| `source_entity`      | string | Name of source entity                            |
| `target_entity`      | string | Name of target entity                            |
| `justification_score`| float  | Score for justification richness (0--35)         |
| `confidence_score`   | float  | Score for extraction confidence (0--25)          |
| `specificity_score`  | float  | Score based on relationship type tier (0--25)    |
| `valid_refs_score`   | float  | Score for valid entity references (0--15)        |
| `total_score`        | float  | Sum of all component scores (0--100)             |

### Errors

| Status | Description        |
|--------|--------------------|
| `404`  | Source not found    |

---

## Recalculate Scores

```
POST /api/v1/quality/recalculate
```

Recalculate and cache quality scores for all sources, or for sources in a specific domain.

### Use Cases

- After updating scoring configuration (domain `quality_scoring` settings)
- After upgrading to a new scoring algorithm version
- Initial migration of existing data

### Request Body

| Field    | Type         | Required | Default | Description                                            |
|----------|--------------|----------|---------|--------------------------------------------------------|
| `domain` | string\|null | No       | `null`  | Only recalculate sources in this extraction domain     |

### Example

```bash
# Recalculate all sources
curl -X POST "http://localhost/api/v1/quality/recalculate" \
  -H "Content-Type: application/json" \
  -d '{}'

# Recalculate only science domain
curl -X POST "http://localhost/api/v1/quality/recalculate" \
  -H "Content-Type: application/json" \
  -d '{"domain": "science"}'
```

### Response

`200 OK`

```json
{
  "recalculated_count": 42,
  "errors": []
}
```

With errors:

```json
{
  "recalculated_count": 40,
  "errors": [
    {
      "source_id": "src-broken1",
      "error": "No graph data found for source"
    },
    {
      "source_id": "src-broken2",
      "error": "Failed to read extraction results"
    }
  ]
}
```

#### RecalculateResponse

| Field                | Type     | Description                                        |
|----------------------|----------|----------------------------------------------------|
| `recalculated_count` | int      | Number of sources successfully recalculated        |
| `errors`             | dict[]   | List of errors encountered during recalculation    |

---

## Outdated Sources

```
GET /api/v1/quality/outdated
```

Get sources with outdated or missing cached quality scores. Returns sources that need recalculation due to missing cached scores (never calculated) or an outdated scoring version (algorithm changed since caching).

### Example

```bash
curl "http://localhost/api/v1/quality/outdated"
```

### Response

`200 OK`

```json
{
  "outdated_count": 5,
  "sources": [
    {
      "id": "src-abc123",
      "title": "Research Paper on Neural Networks",
      "cached_scores_version": 1,
      "current_version": 3
    }
  ]
}
```

A `cached_scores_version` of `null` indicates the source has never been scored.

#### OutdatedSourcesResponse

| Field            | Type                       | Description                                     |
|------------------|----------------------------|-------------------------------------------------|
| `outdated_count` | int                        | Number of sources with outdated scores           |
| `sources`        | `OutdatedSourceResponse[]` | List of sources needing recalculation            |

#### OutdatedSourceResponse

| Field                   | Type       | Description                              |
|-------------------------|------------|------------------------------------------|
| `id`                    | string     | Source ID                                |
| `title`                 | string\|null | Source title                           |
| `cached_scores_version` | int\|null  | Version of cached scores (null if never calculated) |
| `current_version`       | int        | Current scoring algorithm version        |

---

## Batch Analysis

```
POST /api/v1/quality/analyze
```

Analyze quality across multiple sources with optional filters. Returns all matching sources with aggregated average metrics.

### Request Body

| Field          | Type           | Required | Default | Description                                      |
|----------------|----------------|----------|---------|--------------------------------------------------|
| `source_ids`   | string[]\|null | No       | `null`  | Specific source IDs to analyze (null = all)      |
| `domain`       | string\|null   | No       | `null`  | Filter by extraction domain                      |
| `min_entities` | int            | No       | `0`     | Minimum entity count to include                  |

### Example

```bash
# Analyze all sources
curl -X POST "http://localhost/api/v1/quality/analyze" \
  -H "Content-Type: application/json" \
  -d '{}'

# Analyze specific sources
curl -X POST "http://localhost/api/v1/quality/analyze" \
  -H "Content-Type: application/json" \
  -d '{"source_ids": ["src-abc123", "src-def456"]}'

# Filter by domain with minimum entity count
curl -X POST "http://localhost/api/v1/quality/analyze" \
  -H "Content-Type: application/json" \
  -d '{"domain": "science", "min_entities": 10}'
```

### Response

`200 OK`

Each item in `sources` is a [SourceQualityScoreResponse](#sourcequalityscoreresponse).

```json
{
  "sources": [
    {
      "source_id": "src-abc123",
      "source_title": "Research Paper on Neural Networks",
      "...": "... same schema as SourceQualityScoreResponse ..."
    }
  ],
  "total_sources": 2,
  "avg_score": 4699.75,
  "avg_entity_quality": 59.14,
  "avg_relationship_quality": 49.02
}
```

#### QualityAnalysisResponse

| Field                    | Type                            | Description                                |
|--------------------------|---------------------------------|--------------------------------------------|
| `sources`                | `SourceQualityScoreResponse[]`  | Quality scores for each source             |
| `total_sources`          | int                             | Total sources analyzed                     |
| `avg_score`              | float                           | Average total score across sources         |
| `avg_entity_quality`     | float                           | Average entity quality across sources      |
| `avg_relationship_quality` | float                         | Average relationship quality across sources|

---

## Paginated Analysis

```
GET /api/v1/quality/analyze
```

Analyze quality across sources with pagination, sorting, and filtering. Returns a single page of results with pagination metadata and aggregated averages computed across all matching sources.

### Query Parameters

| Parameter      | Type         | Required | Default       | Description                                                          |
|----------------|--------------|----------|---------------|----------------------------------------------------------------------|
| `domain`       | string\|null | No       | `null`        | Filter by extraction domain                                         |
| `min_entities` | int          | No       | `0`           | Minimum entity count to include (min: 0)                            |
| `page`         | int          | No       | `1`           | Page number (min: 1)                                                |
| `page_size`    | int\|null    | No       | server default| Items per page (min: 1, capped at server max)                       |
| `sort_by`      | string       | No       | `total_score` | Sort field: `total_score`, `avg_entity_quality`, `avg_relationship_quality`, or `entity_count` |
| `sort_order`   | string       | No       | `desc`        | Sort order: `asc` or `desc`                                        |

:::note[Page size defaults]

When `page_size` is not provided, the server default page size is used. Values exceeding the server maximum are clamped automatically.

:::

### Example

```bash
# Default paginated analysis (sorted by total_score descending)
curl "http://localhost/api/v1/quality/analyze"

# Filter by domain with custom pagination
curl "http://localhost/api/v1/quality/analyze?domain=science&page=2&page_size=10"

# Sort by entity quality ascending
curl "http://localhost/api/v1/quality/analyze?sort_by=avg_entity_quality&sort_order=asc"

# Filter sources with at least 5 entities
curl "http://localhost/api/v1/quality/analyze?min_entities=5"
```

### Response

`200 OK`

Each item in `sources` is a [SourceQualityScoreResponse](#sourcequalityscoreresponse).

```json
{
  "sources": [
    {
      "source_id": "src-abc123",
      "source_title": "Research Paper on Neural Networks",
      "...": "... same schema as SourceQualityScoreResponse ..."
    }
  ],
  "total_sources": 42,
  "avg_score": 4200.3,
  "avg_entity_quality": 57.5,
  "avg_relationship_quality": 46.8,
  "pagination": {
    "page": 1,
    "page_size": 50,
    "total": 42,
    "total_pages": 1,
    "has_next": false,
    "has_prev": false
  }
}
```

#### QualityAnalysisPaginatedResponse

| Field                      | Type                           | Description                                       |
|----------------------------|--------------------------------|---------------------------------------------------|
| `sources`                  | `SourceQualityScoreResponse[]` | Quality scores for the current page               |
| `total_sources`            | int                            | Total sources analyzed                            |
| `avg_score`                | float                          | Average total score across all sources            |
| `avg_entity_quality`       | float                          | Average entity quality across all sources         |
| `avg_relationship_quality` | float                          | Average relationship quality across all sources   |
| `pagination`               | `PaginationInfo`               | Pagination metadata                               |

#### PaginationInfo

| Field         | Type | Description                          |
|---------------|------|--------------------------------------|
| `page`        | int  | Current page number                  |
| `page_size`   | int  | Items per page                       |
| `total`       | int  | Total items                          |
| `total_pages` | int  | Total number of pages                |
| `has_next`    | bool | Whether there is a next page         |
| `has_prev`    | bool | Whether there is a previous page     |

---

## Domain Comparison

```
GET /api/v1/quality/domains
```

Compare quality performance across extraction domains. Returns aggregated quality metrics for each domain, sorted by average total score descending.

### Example

```bash
curl "http://localhost/api/v1/quality/domains"
```

### Response

`200 OK`

One entry per domain, sorted by `avg_total_score` descending:

```json
{
  "domains": [
    {
      "domain": "science",
      "source_count": 15,
      "avg_total_score": 5200.8,
      "avg_entity_quality": 62.4,
      "avg_relationship_quality": 51.3,
      "avg_connectivity_ratio": 0.78,
      "total_entities": 680,
      "total_relationships": 920
    }
  ]
}
```

#### DomainComparisonResponse

| Field     | Type                          | Description                        |
|-----------|-------------------------------|------------------------------------|
| `domains` | `DomainPerformanceResponse[]` | Performance metrics per domain     |

#### DomainPerformanceResponse

| Field                    | Type   | Description                               |
|--------------------------|--------|-------------------------------------------|
| `domain`                 | string | Domain name                               |
| `source_count`           | int    | Number of sources in this domain          |
| `avg_total_score`        | float  | Average total score                       |
| `avg_entity_quality`     | float  | Average entity quality                    |
| `avg_relationship_quality` | float | Average relationship quality             |
| `avg_connectivity_ratio` | float  | Average connectivity ratio                |
| `total_entities`         | int    | Total entities across all sources         |
| `total_relationships`    | int    | Total relationships across all sources    |

---

## Database Summary

```
GET /api/v1/quality/summary
```

Get an overall quality summary for the entire database. Provides high-level statistics and identifies the top 5 and bottom 5 sources by total score.

### Example

```bash
curl "http://localhost/api/v1/quality/summary"
```

### Response

`200 OK`

```json
{
  "total_sources": 42,
  "total_entities": 1490,
  "total_relationships": 1875,
  "avg_total_score": 4150.6,
  "avg_entity_quality": 55.3,
  "avg_relationship_quality": 45.9,
  "avg_quality_grade": 62.1,
  "avg_connectivity_ratio": 0.66,
  "top_sources": [
    {
      "source_id": "src-top1",
      "source_title": "Comprehensive Biology Textbook",
      "...": "... same schema as SourceQualityScoreResponse ..."
    }
  ],
  "bottom_sources": [
    {
      "source_id": "src-bottom1",
      "source_title": "Brief Meeting Notes",
      "...": "... same schema as SourceQualityScoreResponse ..."
    }
  ]
}
```

#### QualitySummaryResponse

| Field                      | Type                           | Description                                  |
|----------------------------|--------------------------------|----------------------------------------------|
| `total_sources`            | int                            | Total sources with extractions               |
| `total_entities`           | int                            | Total entities extracted                     |
| `total_relationships`      | int                            | Total relationships extracted                |
| `avg_total_score`          | float                          | Average total score                          |
| `avg_entity_quality`       | float                          | Average entity quality                       |
| `avg_relationship_quality` | float                          | Average relationship quality                 |
| `avg_quality_grade`        | float                          | Average quality grade (0--100)               |
| `avg_connectivity_ratio`   | float                          | Average connectivity ratio                   |
| `top_sources`              | `SourceQualityScoreResponse[]` | Top 5 sources by total score                 |
| `bottom_sources`           | `SourceQualityScoreResponse[]` | Bottom 5 sources by total score              |
