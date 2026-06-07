// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

const STAGE_META: Record<string, { label: string; color: 'info' | 'error' | 'warning' }> = {
  entity_evidence_filter: { label: 'Evidence Filter', color: 'info' },
  entity_exclusion_filter: { label: 'Exclusion Filter', color: 'info' },
  type_rescue: { label: 'Type Rescue', color: 'info' },
  implausible_entity_filter: { label: 'Implausible Filter', color: 'info' },
  relationship_index_validation: { label: 'Index Validation', color: 'error' },
  relationship_type_constraint: { label: 'Type Constraint', color: 'error' },
  relationship_evidence_filter: { label: 'Evidence Filter', color: 'error' },
  relationship_limit_enforcement: { label: 'Limit Enforcement', color: 'error' },
  structural_entity_filter: { label: 'Structural Filter', color: 'warning' },
  exact_entity_dedup: { label: 'Exact Dedup', color: 'warning' },
  semantic_entity_dedup: { label: 'Semantic Dedup', color: 'warning' },
  relationship_dedup: { label: 'Relationship Dedup', color: 'warning' },
  descriptor_alias_cleaning: { label: 'Alias Cleaning', color: 'warning' },
};

export function getStageMeta(stage: string) {
  return STAGE_META[stage] ?? { label: stage.replace(/_/g, ' '), color: 'info' as const };
}
