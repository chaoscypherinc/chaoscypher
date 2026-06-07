// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

// Quality domain type definitions for Chaos Cypher frontend

export interface SourceQualityScore {
  source_id: string;
  source_title?: string;
  domain?: string;
  entity_count: number;
  relationship_count: number;
  entity_contribution: number;
  relationship_contribution: number;
  connectivity_bonus: number;
  total_score: number;           // Richness score (unbounded, quantity-driven)
  avg_entity_quality: number;    // 0-100
  avg_relationship_quality: number; // 0-100
  connectivity_ratio: number;    // 0-1
  quality_grade: number;         // 0-100 (quality independent of volume)
  quality_label: 'Outstanding' | 'Excellent' | 'Good' | 'Fair' | 'Low';
  low_quality_entity_count?: number;
  low_quality_relationship_count?: number;
  // v7 scoring metrics
  density_ratio: number;         // relationships / entities
  density_score: number;         // 0-100 (bell-shaped around target in v7)
  topology_score: number;        // 0-100 (avg of connectivity + density)
  pollution_penalty: number;     // 0-15 (low-quality item inflation)
  structural_penalty?: number;   // 0-15 (hub-skew + reciprocal-rate, v7+)
  hub_skew?: number;             // max_degree / median_degree (≥1.0, v7+)
  reciprocal_rate?: number;      // 0-1 (same-type reciprocal edges, v7+)
  coverage_score?: number;       // 0-100 entities per chunk
}

export interface QualityAnalysisResponse {
  sources: SourceQualityScore[];
  total_sources: number;
  avg_score: number;
  avg_entity_quality: number;
  avg_relationship_quality: number;
}
