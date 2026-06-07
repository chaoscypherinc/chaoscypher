// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import {
  getActiveStageProgress,
  STAGE_WEIGHTS,
  calculateSegmentedProgress,
  aggregateProgress,
} from '../progressCalculation';
import type { UnifiedSource } from '../../../../types';

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

/** Minimal valid StageProgressRecord (completed — completed_at set). */
function makeCompletedStageRecord(total = 10, processed = 10) {
  return {
    total,
    processed,
    avg_ms: 200,
    started_at: '2026-01-01T00:00:00Z',
    last_activity: '2026-01-01T00:01:00Z',
    completed_at: '2026-01-01T00:01:00Z',
  };
}

/** In-flight stage record (no completed_at). */
function makeActiveStageRecord(total: number, processed: number, avg_ms?: number | null) {
  return {
    total,
    processed,
    avg_ms: avg_ms ?? null,
    started_at: '2026-01-01T00:00:00Z',
    last_activity: '2026-01-01T00:00:30Z',
    completed_at: null,
  };
}

/** Build a minimal UnifiedSource. Only fields read by the module are required. */
function makeSource(overrides: Partial<UnifiedSource> = {}): UnifiedSource {
  return {
    id: 'src-1',
    stage: 'processing',
    title: 'test.pdf',
    source_type: 'pdf',
    size: 1024 * 1024, // 1 MB
    status: 'indexing',
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  } as UnifiedSource;
}

// ---------------------------------------------------------------------------
// STAGE_WEIGHTS
// ---------------------------------------------------------------------------

describe('STAGE_WEIGHTS', () => {
  it('sums to 100', () => {
    const total = STAGE_WEIGHTS.indexing + STAGE_WEIGHTS.extraction + STAGE_WEIGHTS.commit;
    expect(total).toBe(100);
  });

  it('has the documented weights', () => {
    expect(STAGE_WEIGHTS.indexing).toBe(10);
    expect(STAGE_WEIGHTS.extraction).toBe(75);
    expect(STAGE_WEIGHTS.commit).toBe(15);
  });
});

// ---------------------------------------------------------------------------
// getActiveStageProgress
// ---------------------------------------------------------------------------

describe('getActiveStageProgress', () => {
  it('returns null when ingestion is absent', () => {
    const source = makeSource({ ingestion: undefined });
    expect(getActiveStageProgress(source)).toBeNull();
  });

  it('returns null when stage_progress is absent', () => {
    const source = makeSource({ ingestion: {} });
    expect(getActiveStageProgress(source)).toBeNull();
  });

  it('returns null when all stages are completed', () => {
    const source = makeSource({
      ingestion: {
        stage_progress: {
          vision: makeCompletedStageRecord(5, 5),
          embedding: makeCompletedStageRecord(20, 20),
        },
      },
    });
    expect(getActiveStageProgress(source)).toBeNull();
  });

  it('returns null when the only stage has total = 0', () => {
    const source = makeSource({
      ingestion: {
        stage_progress: {
          vision: { ...makeActiveStageRecord(0, 0), completed_at: null },
        },
      },
    });
    expect(getActiveStageProgress(source)).toBeNull();
  });

  it('returns the active vision stage with correct fields', () => {
    const record = makeActiveStageRecord(10, 3, 500);
    const source = makeSource({
      ingestion: {
        stage_progress: { vision: record },
      },
    });
    const result = getActiveStageProgress(source);
    expect(result).not.toBeNull();
    expect(result!.stageName).toBe('vision');
    expect(result!.processed).toBe(3);
    expect(result!.total).toBe(10);
    expect(result!.itemNoun).toBe('pages'); // vision → 'pages'
    expect(result!.avgMs).toBe(500);
    // remainingSeconds = round((10 - 3) * 500 / 1000) = round(3.5) = 4 (or 3 depending on rounding)
    // Actually: (10 - 3) * 500 / 1000 = 3.5 → Math.round(3.5) = 4
    expect(result!.remainingSeconds).toBe(4);
    expect(result!.record).toBe(record);
  });

  it('uses "chunks" as itemNoun for embedding stage', () => {
    const record = makeActiveStageRecord(50, 10, 100);
    const source = makeSource({
      ingestion: { stage_progress: { embedding: record } },
    });
    const result = getActiveStageProgress(source);
    expect(result!.itemNoun).toBe('chunks');
  });

  it('uses "chunks" as itemNoun for mcp_extraction stage', () => {
    const record = makeActiveStageRecord(30, 5, 200);
    const source = makeSource({
      ingestion: { stage_progress: { mcp_extraction: record } },
    });
    const result = getActiveStageProgress(source);
    expect(result!.itemNoun).toBe('chunks');
  });

  it('falls back to "items" for unknown stage names', () => {
    const record = makeActiveStageRecord(8, 2, 300);
    const source = makeSource({
      ingestion: { stage_progress: { custom_stage: record } },
    });
    const result = getActiveStageProgress(source);
    expect(result!.itemNoun).toBe('items');
  });

  it('returns null remainingSeconds when avg_ms is null', () => {
    const record = makeActiveStageRecord(10, 5, null);
    const source = makeSource({
      ingestion: { stage_progress: { vision: record } },
    });
    const result = getActiveStageProgress(source);
    expect(result!.remainingSeconds).toBeNull();
    expect(result!.avgMs).toBeNull();
  });

  it('returns null remainingSeconds when avg_ms is 0', () => {
    const record = makeActiveStageRecord(10, 5, 0);
    const source = makeSource({
      ingestion: { stage_progress: { vision: record } },
    });
    const result = getActiveStageProgress(source);
    expect(result!.remainingSeconds).toBeNull();
  });

  it('returns null remainingSeconds when processed >= total (nothing left)', () => {
    // processed === total but completed_at not set — edge case
    const record = { ...makeActiveStageRecord(10, 10, 200), completed_at: null };
    const source = makeSource({
      ingestion: { stage_progress: { vision: record } },
    });
    const result = getActiveStageProgress(source);
    // total > 0 and completed_at null → stage is active
    expect(result).not.toBeNull();
    // processed >= total → remainingSeconds should be null
    expect(result!.remainingSeconds).toBeNull();
  });

  it('picks the first active stage when multiple exist', () => {
    // Object.entries ordering follows insertion order in V8 for string keys.
    // Put a completed vision stage first, then active embedding.
    const source = makeSource({
      ingestion: {
        stage_progress: {
          vision: makeCompletedStageRecord(10, 10),
          embedding: makeActiveStageRecord(40, 15, 250),
        },
      },
    });
    const result = getActiveStageProgress(source);
    expect(result!.stageName).toBe('embedding');
  });

  it('calculates remainingSeconds correctly with large numbers', () => {
    // (total - processed) * avg_ms / 1000 = (100 - 20) * 1000 / 1000 = 80
    const record = makeActiveStageRecord(100, 20, 1000);
    const source = makeSource({
      ingestion: { stage_progress: { vision: record } },
    });
    const result = getActiveStageProgress(source);
    expect(result!.remainingSeconds).toBe(80);
  });
});

// ---------------------------------------------------------------------------
// calculateSegmentedProgress
// ---------------------------------------------------------------------------

describe('calculateSegmentedProgress', () => {
  describe('queued / error states', () => {
    it('returns 0 progress for default/unknown status (queued)', () => {
      const source = makeSource({ status: 'unknown_status' });
      const result = calculateSegmentedProgress(source);
      expect(result.totalProgress).toBe(0);
      expect(result.stageProgress).toBe(0);
      expect(result.stageName).toBe('queued');
      expect(result.stageNumber).toBe(0);
      expect(result.stageLabel).toBe('Queued');
      expect(result.estimatedRemainingSeconds).toBeNull();
    });

    it('returns 0 progress for error status', () => {
      const source = makeSource({ status: 'error' });
      const result = calculateSegmentedProgress(source);
      expect(result.totalProgress).toBe(0);
      expect(result.stageProgress).toBe(0);
      expect(result.stageName).toBe('error');
      expect(result.stageNumber).toBe(0);
      expect(result.stageLabel).toBe('Error');
      expect(result.estimatedRemainingSeconds).toBeNull();
    });
  });

  describe('complete state', () => {
    it('returns 100% for committed status', () => {
      const source = makeSource({ status: 'committed' });
      const result = calculateSegmentedProgress(source);
      expect(result.totalProgress).toBe(100);
      expect(result.stageProgress).toBe(100);
      expect(result.stageName).toBe('complete');
      expect(result.stageNumber).toBe(4);
      expect(result.stageLabel).toBe('Complete');
      expect(result.estimatedRemainingSeconds).toBeNull();
    });
  });

  describe('indexing stage', () => {
    it('maps "pending" status to indexing stage 1', () => {
      const source = makeSource({
        status: 'pending',
        ingestion: { current_step: 0, total_steps: 10 },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.stageName).toBe('indexing');
      expect(result.stageNumber).toBe(1);
      expect(result.stageLabel).toBe('Indexing');
    });

    it('maps "indexing" status to indexing stage with correct label', () => {
      const source = makeSource({
        status: 'indexing',
        ingestion: { current_step: 5, total_steps: 10 },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.stageName).toBe('indexing');
      expect(result.stageLabel).toBe('Indexing');
    });

    it('maps "vision_pending" status to indexing stage with Vision label', () => {
      const source = makeSource({
        status: 'vision_pending',
        ingestion: { current_step: 0, total_steps: 5 },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.stageName).toBe('indexing');
      expect(result.stageLabel).toBe('Vision');
    });

    it('calculates totalProgress within the indexing segment (0–10%)', () => {
      // 50% through indexing → 5% of 10 = 5% total
      const source = makeSource({
        status: 'indexing',
        ingestion: { current_step: 5, total_steps: 10 },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.stageProgress).toBe(50);
      // totalProgress = stageOffset(0) + (50/100) * 10 = 5
      expect(result.totalProgress).toBe(5);
    });

    it('handles zero total_steps without divide-by-zero', () => {
      const source = makeSource({
        status: 'indexing',
        ingestion: { current_step: 0, total_steps: 0 },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.stageProgress).toBe(0);
      expect(result.totalProgress).toBe(0);
    });

    it('handles missing ingestion gracefully (defaults to 0/1)', () => {
      const source = makeSource({ status: 'indexing', ingestion: undefined });
      const result = calculateSegmentedProgress(source);
      // current_step=0, total_steps=1 → stageProgress=0
      expect(result.stageProgress).toBe(0);
      expect(result.totalProgress).toBe(0);
    });
  });

  describe('extraction stage', () => {
    it('maps "indexed" status to extraction stage 2', () => {
      const source = makeSource({
        status: 'indexed',
        ingestion: { current_step: 0, total_steps: 20 },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.stageName).toBe('extraction');
      expect(result.stageNumber).toBe(2);
      expect(result.stageLabel).toBe('Extracting');
    });

    it('maps "extracting" to extraction with "Extracting" label', () => {
      const source = makeSource({
        status: 'extracting',
        ingestion: { current_step: 10, total_steps: 20 },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.stageName).toBe('extraction');
      expect(result.stageLabel).toBe('Extracting');
    });

    it('maps "mcp_extracting" to extraction with "MCP Extracting" label', () => {
      const source = makeSource({
        status: 'mcp_extracting',
        ingestion: { current_step: 5, total_steps: 20 },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.stageName).toBe('extraction');
      expect(result.stageLabel).toBe('MCP Extracting');
    });

    it('calculates totalProgress within the extraction segment (10–85%)', () => {
      // 50% through extraction → offset(10) + (50/100)*75 = 10 + 37.5 = 47.5
      const source = makeSource({
        status: 'extracting',
        ingestion: { current_step: 10, total_steps: 20 },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.stageProgress).toBe(50);
      expect(result.totalProgress).toBe(10 + 37.5); // 47.5
    });

    it('gives 10% totalProgress at 0% extraction progress (start of extraction segment)', () => {
      const source = makeSource({
        status: 'extracting',
        ingestion: { current_step: 0, total_steps: 10 },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.stageProgress).toBe(0);
      expect(result.totalProgress).toBe(10); // stageOffset for extraction
    });
  });

  describe('commit stage', () => {
    it('maps "extracted" status to commit stage 3', () => {
      const source = makeSource({
        status: 'extracted',
        ingestion: { current_step: 0, total_steps: 5 },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.stageName).toBe('commit');
      expect(result.stageNumber).toBe(3);
      expect(result.stageLabel).toBe('Committing');
    });

    it('maps "committing" to commit stage 3', () => {
      const source = makeSource({
        status: 'committing',
        ingestion: { current_step: 2, total_steps: 4 },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.stageName).toBe('commit');
      expect(result.stageLabel).toBe('Committing');
    });

    it('calculates totalProgress within the commit segment (85–100%)', () => {
      // 50% through commit → offset(85) + (50/100)*15 = 85 + 7.5 = 92.5
      const source = makeSource({
        status: 'committing',
        ingestion: { current_step: 2, total_steps: 4 },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.stageProgress).toBe(50);
      expect(result.totalProgress).toBe(85 + 7.5); // 92.5
    });
  });

  describe('estimatedRemainingSeconds', () => {
    it('returns null for complete stage', () => {
      const source = makeSource({ status: 'committed' });
      expect(calculateSegmentedProgress(source).estimatedRemainingSeconds).toBeNull();
    });

    it('returns null for error stage', () => {
      const source = makeSource({ status: 'error' });
      expect(calculateSegmentedProgress(source).estimatedRemainingSeconds).toBeNull();
    });

    it('returns null for queued stage', () => {
      const source = makeSource({ status: 'unknown_xyz' });
      expect(calculateSegmentedProgress(source).estimatedRemainingSeconds).toBeNull();
    });

    it('uses live stage_progress EMA when available (indexing)', () => {
      // Active vision stage: (10 - 3) * 500 / 1000 = 3.5 → round = 4
      const source = makeSource({
        status: 'indexing',
        size: 2 * 1024 * 1024,
        ingestion: {
          current_step: 3,
          total_steps: 10,
          stage_progress: {
            vision: makeActiveStageRecord(10, 3, 500),
          },
        },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.estimatedRemainingSeconds).toBe(4);
    });

    it('falls back to heuristic when no stage_progress is present (indexing)', () => {
      // 1 MB file, 0% through indexing:
      // indexingRemaining = 1 * 2 * (1 - 0/100) = 2s
      // extractionTime = max(0, 1*3) * 15 = 45s
      // commitTime = max(0, 50) * 0.05 = 2.5s
      // total = 2 + 45 + 2.5 = 49.5 → round = 50
      const source = makeSource({
        status: 'indexing',
        size: 1024 * 1024,
        ingestion: { current_step: 0, total_steps: 10, chunks_count: 0, entities_count: 0 },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.estimatedRemainingSeconds).toBe(50);
    });

    it('falls back to heuristic for extraction stage', () => {
      // total_steps=10, current_step=0, entities_count=0
      // extractionRemaining = (10 - 0) * 15 = 150
      // commitTime = max(0, 50) * 0.05 = 2.5
      // total = 152.5 → round = 153
      const source = makeSource({
        status: 'extracting',
        size: 1024 * 1024,
        ingestion: {
          current_step: 0,
          total_steps: 10,
          entities_count: 0,
        },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.estimatedRemainingSeconds).toBe(153);
    });

    it('falls back to heuristic for commit stage', () => {
      // stageProgress=0, entities_count=0 → max(0,50)*0.05*(1-0)=2.5 → round → 3?
      // Actually: max(entities_count=0, 50) = 50; 50 * 0.05 * (1 - 0/100) = 2.5 → round = 3
      // Wait: 2.5 rounds to 3 with Math.round? Math.round(2.5) = 3
      const source = makeSource({
        status: 'committing',
        size: 1024 * 1024,
        ingestion: {
          current_step: 0,
          total_steps: 10,
          entities_count: 0,
        },
      });
      const result = calculateSegmentedProgress(source);
      // stageProgress=0, remainingSeconds = max(0,50)*0.05*(1-0/100)=2.5 → Math.round(2.5)=3
      expect(result.estimatedRemainingSeconds).toBe(3);
    });

    it('returns null heuristic when remainingSeconds <= 0 (all done)', () => {
      // Commit stage, 100% through (stageProgress=100):
      // commitRemaining = max(50) * 0.05 * (1 - 100/100) = 0 → returns null
      const source = makeSource({
        status: 'committing',
        size: 1024 * 1024,
        ingestion: {
          current_step: 10,
          total_steps: 10,
          entities_count: 0,
        },
      });
      const result = calculateSegmentedProgress(source);
      expect(result.estimatedRemainingSeconds).toBeNull();
    });
  });
});

// ---------------------------------------------------------------------------
// aggregateProgress
// ---------------------------------------------------------------------------

describe('aggregateProgress', () => {
  it('returns zeros and queued for empty array', () => {
    const result = aggregateProgress([]);
    expect(result.totalProgress).toBe(0);
    expect(result.stageProgress).toBe(0);
    expect(result.dominantStage).toBe('queued');
    expect(result.dominantStageLabel).toBe('Queued');
    expect(result.totalEstimatedSeconds).toBeNull();
  });

  it('returns 100% for a single committed source', () => {
    const source = makeSource({ status: 'committed' });
    const result = aggregateProgress([source]);
    expect(result.totalProgress).toBe(100);
  });

  it('returns 0% for a single queued source', () => {
    const source = makeSource({ status: 'unknown_status' });
    const result = aggregateProgress([source]);
    expect(result.totalProgress).toBe(0);
    // When all counts are 0 the filter still returns [indexing:0, extraction:0, commit:0],
    // and sort picks the first entry ('indexing') since all are tied.
    expect(result.dominantStage).toBe('indexing');
  });

  it('returns 0% for all-queued sources', () => {
    const sources = [
      makeSource({ id: 'a', status: 'unknown_status' }),
      makeSource({ id: 'b', status: 'unknown_status' }),
    ];
    const result = aggregateProgress(sources);
    expect(result.totalProgress).toBe(0);
  });

  it('returns 100% for all-complete sources', () => {
    const sources = [
      makeSource({ id: 'a', status: 'committed' }),
      makeSource({ id: 'b', status: 'committed' }),
    ];
    const result = aggregateProgress(sources);
    expect(result.totalProgress).toBe(100);
  });

  it('weights sources by chunks_count', () => {
    // Source A: 100% (committed), weight=100 chunks
    // Source B: 0% (queued), weight=1 (fallback)
    // Weighted: (100*100 + 0*1) / (100+1) ≈ 99.01%
    const sourceA = makeSource({
      id: 'a',
      status: 'committed',
      ingestion: { chunks_count: 100 },
    });
    const sourceB = makeSource({ id: 'b', status: 'unknown_status' });
    const result = aggregateProgress([sourceA, sourceB]);
    expect(result.totalProgress).toBeCloseTo(100 * 100 / 101, 5);
  });

  it('falls back to weight 1 when chunks_count is 0', () => {
    const sourceA = makeSource({ id: 'a', status: 'committed', ingestion: { chunks_count: 0 } });
    const sourceB = makeSource({ id: 'b', status: 'unknown_status', ingestion: { chunks_count: 0 } });
    // Both weight 1 (fallback from `|| 1`): (100 + 0)/2 = 50
    const result = aggregateProgress([sourceA, sourceB]);
    expect(result.totalProgress).toBe(50);
  });

  it('selects the dominant stage based on most files in non-complete/error/queued stages', () => {
    // 2 sources in extraction, 1 in indexing → dominant = extraction
    const sources = [
      makeSource({ id: 'a', status: 'extracting', ingestion: { current_step: 5, total_steps: 10 } }),
      makeSource({ id: 'b', status: 'mcp_extracting', ingestion: { current_step: 3, total_steps: 10 } }),
      makeSource({ id: 'c', status: 'indexing', ingestion: { current_step: 2, total_steps: 10 } }),
    ];
    const result = aggregateProgress(sources);
    expect(result.dominantStage).toBe('extraction');
    expect(result.dominantStageLabel).toBe('Extracting');
  });

  it('selects indexing as dominant stage when all are complete (first among tied zeros)', () => {
    // All sources are complete. After filtering out complete/error/queued, stageCounts
    // has [indexing:0, extraction:0, commit:0]. Sort is stable-ish and the first entry
    // (indexing) wins the tie — dominantStage = 'indexing'.
    const sources = [
      makeSource({ id: 'a', status: 'committed' }),
      makeSource({ id: 'b', status: 'committed' }),
    ];
    const result = aggregateProgress(sources);
    expect(result.dominantStage).toBe('indexing');
    expect(result.dominantStageLabel).toBe('Indexing');
  });

  it('sums estimated seconds when present', () => {
    // Use live stage_progress so we get known remainingSeconds
    // vision: (10-3)*500/1000 = 3.5 → 4s each
    const makeActiveSource = (id: string) =>
      makeSource({
        id,
        status: 'indexing',
        size: 1024 * 1024,
        ingestion: {
          current_step: 3,
          total_steps: 10,
          stage_progress: { vision: makeActiveStageRecord(10, 3, 500) },
        },
      });
    const result = aggregateProgress([makeActiveSource('x'), makeActiveSource('y')]);
    // Both return 4s → sum = 8s
    expect(result.totalEstimatedSeconds).toBe(8);
  });

  it('returns null totalEstimatedSeconds when no source has an estimate', () => {
    // Committed sources return null for estimatedRemainingSeconds
    const sources = [
      makeSource({ id: 'a', status: 'committed' }),
      makeSource({ id: 'b', status: 'committed' }),
    ];
    const result = aggregateProgress(sources);
    expect(result.totalEstimatedSeconds).toBeNull();
  });

  it('handles mix of error, complete, and queued sources', () => {
    const sources = [
      makeSource({ id: 'a', status: 'error' }),
      makeSource({ id: 'b', status: 'committed' }),
      makeSource({ id: 'c', status: 'unknown_xyz' }),
    ];
    const result = aggregateProgress(sources);
    // error=0%, committed=100%, queued=0% — equal weights (1 each) → 100/3 ≈ 33.33%
    expect(result.totalProgress).toBeCloseTo(100 / 3, 5);
    // stageCounts after filter: [indexing:0, extraction:0, commit:0] — all tied at 0.
    // First entry ('indexing') wins the tie → dominantStage = 'indexing'
    expect(result.dominantStage).toBe('indexing');
  });

  it('stageProgress reflects position within the dominant segment', () => {
    // 100% complete → calculateStageProgressFromTotal(100) → {stageName:'complete', stageProgress:100}
    const source = makeSource({ status: 'committed' });
    const result = aggregateProgress([source]);
    expect(result.stageProgress).toBe(100);
  });

  it('calculates stageProgress for mid-extraction totalProgress', () => {
    // 50% through extraction → totalProgress=47.5
    // calculateStageProgressFromTotal(47.5):
    //   47.5 >= 10 (indexing weight) → in extraction
    //   stageProgress = (47.5 - 10) / 75 * 100 = 50
    const source = makeSource({
      status: 'extracting',
      ingestion: { current_step: 10, total_steps: 20 },
    });
    const result = aggregateProgress([source]);
    expect(result.totalProgress).toBeCloseTo(47.5, 5);
    expect(result.stageProgress).toBeCloseTo(50, 5);
  });
});

// ---------------------------------------------------------------------------
// calculateStageProgressFromTotal (tested via aggregateProgress)
// ---------------------------------------------------------------------------

describe('calculateStageProgressFromTotal (indirectly via aggregateProgress)', () => {
  it('returns complete/100 for totalProgress=100', () => {
    const source = makeSource({ status: 'committed' });
    const { stageProgress } = aggregateProgress([source]);
    expect(stageProgress).toBe(100);
  });

  it('places 92.5% totalProgress in commit stage', () => {
    // committing, 50% through → totalProgress=92.5
    const source = makeSource({
      status: 'committing',
      ingestion: { current_step: 2, total_steps: 4 },
    });
    const result = aggregateProgress([source]);
    expect(result.totalProgress).toBeCloseTo(92.5, 5);
    // extractionEnd = 10+75 = 85; 92.5 >= 85 → commit
    // stageProgress = (92.5-85)/15 * 100 = 50
    expect(result.stageProgress).toBeCloseTo(50, 5);
  });

  it('places 5% totalProgress in indexing stage', () => {
    // 50% indexing → totalProgress=5
    const source = makeSource({
      status: 'indexing',
      ingestion: { current_step: 5, total_steps: 10 },
    });
    const result = aggregateProgress([source]);
    expect(result.totalProgress).toBe(5);
    // 5 < 10 → indexing; stageProgress = 5/10 * 100 = 50
    expect(result.stageProgress).toBe(50);
  });
});
