// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { describe, it, expect } from 'vitest';
import { buildStageStats } from '../stageStats';
import type { Source, ExtractionTaskStats } from '../../../../../types';

const src = (qm: Record<string, unknown>): Source => ({ quality_metrics: qm } as unknown as Source);

describe('buildStageStats', () => {
  it('emits non-zero clean + filter counters and drops zeros', () => {
    const res = buildStageStats(
      src({ cleaner_chars_removed: 5000, cleaner_lines_removed: 18, structural_entities_filtered: 0, orphan_entities_filtered: 15, evidence_less_entities_dropped: 2 }),
      null,
    );
    expect(res.clean.map((i) => i.label)).toContain('Removed');
    expect(res.clean.map((i) => i.label)).toContain('Lines');
    // structural=0 dropped; orphans kept flagged, then the evidence-less ones dropped
    expect(res.filter.map((i) => i.label)).toEqual(['Unconnected', 'No evidence']);
  });

  it('shows the per-chunk entities and the edges kept flagged instead of dropped', () => {
    const res = buildStageStats(
      src({ plausibility_entities_flagged: 4, unplaced_type_entities_flagged: 2, relationships_dropped_capped: 3 }),
      null,
    );
    expect(res.filter.map((i) => i.label)).toEqual(['Low plausibility', 'Unplaced type', 'Capped rels']);
    expect(res.filter.find((i) => i.label === 'Capped rels')?.description).toContain('kept');
  });

  it('shows the unevidenced relationships kept before the ones still dropped', () => {
    const res = buildStageStats(
      src({ relationships_unevidenced_kept: 157, evidence_relationships_dropped: 1 }),
      null,
    );
    expect(res.filter.map((i) => [i.label, i.value])).toEqual([
      ['Unevidenced', 157],
      ['No-entity rels', 1],
    ]);
  });

  it('shows the entities and relationships the last three filters keep flagged', () => {
    const res = buildStageStats(
      src({
        entities_unevidenced_kept: 1,
        evidence_entities_dropped: 2,
        excluded_entities_kept: 3,
        relationships_unmatched_label_kept: 4,
        relationships_type_mismatch_kept: 5,
      }),
      null,
    );
    expect(res.filter.map((i) => [i.label, i.value])).toEqual([
      ['Unevidenced ents', 1],
      ['No-evidence ents', 2],
      ['Excluded names', 3],
      ['Unmatched labels', 4],
      ['Type mismatches', 5],
    ]);
    expect(res.filter.find((i) => i.label === 'Unmatched labels')?.description).toContain('kept');
  });

  it('shows what the known-entities guards took off pass 1', () => {
    const res = buildStageStats(
      src({ excluded_entities_kept: 1, aliases_crossed_known_dropped: 2, referents_unevidenced_demoted: 3 }),
      null,
    );
    expect(res.filter.map((i) => [i.label, i.value])).toEqual([
      ['Excluded names', 1],
      ['Crossed aliases', 2],
      ['Demoted or flagged names', 3],
    ]);
  });

  it('drops vector_indexing_status when indexed, keeps it when degraded', () => {
    expect(buildStageStats(src({ vector_indexing_status: 'indexed' }), null).commit).toEqual([]);
    const degraded = buildStageStats(src({ vector_indexing_status: 'degraded' }), null).commit;
    expect(degraded.map((i) => i.label)).toContain('Search index');
  });

  it('summarizes extract from llmStats (chunks + retried)', () => {
    const stats = { total_tasks: 31, total_retries: 2 } as unknown as ExtractionTaskStats;
    const ex = buildStageStats(src({}), stats);
    expect(ex.extract.map((i) => i.label)).toEqual(['Chunks', 'Retried']);
  });
});
