// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import type { UnifiedSource } from '../../../types';
import type { components } from '../../../types/generated/api';

type StageProgressRecord = components['schemas']['StageProgressRecord'];

/**
 * The active LLM-stage row for a source, plus the EMA-derived remaining
 * seconds. Returned by ``getActiveStageProgress`` and consumed both by
 * ``calculateEstimatedTime`` (per-source ETA used by the row top-right
 * slot and the page-header rollup) and by the row component itself to
 * render the "X/Y items" caption alongside the time.
 *
 * "Active" means: not yet ``completed_at`` and ``total > 0``. The first
 * such row wins — in practice the pipeline runs vision → embedding →
 * mcp_extraction sequentially, so there is only ever one active row.
 */
interface ActiveStageProgress {
  stageName: string;
  processed: number;
  total: number;
  itemNoun: string;
  avgMs: number | null;
  remainingSeconds: number | null;
  record: StageProgressRecord;
}

const STAGE_ITEM_NOUNS: Record<string, string> = {
  vision: 'pages',
  embedding: 'chunks',
  mcp_extraction: 'chunks',
};

/**
 * Pick the in-flight stage for a source and return its EMA-derived
 * remaining seconds, or null when the source has no active stage row
 * yet (typical of the very early loading phase, where the loader
 * itself runs before any LLM stage starts).
 */
export function getActiveStageProgress(source: UnifiedSource): ActiveStageProgress | null {
  const stages = source.ingestion?.stage_progress;
  if (!stages) return null;
  for (const [stageName, r] of Object.entries(stages)) {
    if (r.completed_at == null && r.total > 0) {
      const remainingSeconds =
        r.avg_ms != null && r.avg_ms > 0 && r.processed < r.total
          ? ((r.total - r.processed) * r.avg_ms) / 1000
          : null;
      return {
        stageName,
        processed: r.processed,
        total: r.total,
        itemNoun: STAGE_ITEM_NOUNS[stageName] ?? 'items',
        avgMs: r.avg_ms ?? null,
        remainingSeconds: remainingSeconds != null ? Math.round(remainingSeconds) : null,
        record: r,
      };
    }
  }
  return null;
}

// Stage weights (must sum to 100)
export const STAGE_WEIGHTS = {
  indexing: 10,
  extraction: 75,
  commit: 15,
} as const;

export type StageName = 'indexing' | 'extraction' | 'commit' | 'complete' | 'error' | 'queued';

interface SegmentedProgress {
  /** Total progress across all stages (0-100) */
  totalProgress: number;
  /** Progress within current stage (0-100) */
  stageProgress: number;
  /** Current stage name */
  stageName: StageName;
  /** Stage number (1=indexing, 2=extraction, 3=commit, 4=complete) */
  stageNumber: number;
  /** Human-readable stage label */
  stageLabel: string;
  /** Estimated remaining time in seconds (null if not calculable) */
  estimatedRemainingSeconds: number | null;
}

/**
 * Map status to stage information.
 */
function getStageFromStatus(status: string): {
  stageName: StageName;
  stageNumber: number;
  stageOffset: number;
  stageWeight: number;
  stageLabel: string;
} {
  switch (status) {
    case 'pending':
    case 'indexing':
    case 'vision_pending':
      return {
        stageName: 'indexing',
        stageNumber: 1,
        stageOffset: 0,
        stageWeight: STAGE_WEIGHTS.indexing,
        stageLabel: status === 'vision_pending' ? 'Vision' : 'Indexing',
      };
    case 'indexed':
    case 'extracting':
    case 'mcp_extracting':
      return {
        stageName: 'extraction',
        stageNumber: 2,
        stageOffset: STAGE_WEIGHTS.indexing,
        stageWeight: STAGE_WEIGHTS.extraction,
        stageLabel: status === 'mcp_extracting' ? 'MCP Extracting' : 'Extracting',
      };
    case 'extracted':
    case 'committing':
      return {
        stageName: 'commit',
        stageNumber: 3,
        stageOffset: STAGE_WEIGHTS.indexing + STAGE_WEIGHTS.extraction,
        stageWeight: STAGE_WEIGHTS.commit,
        stageLabel: 'Committing',
      };
    case 'committed':
      return {
        stageName: 'complete',
        stageNumber: 4,
        stageOffset: 100,
        stageWeight: 0,
        stageLabel: 'Complete',
      };
    case 'error':
      return {
        stageName: 'error',
        stageNumber: 0,
        stageOffset: 0,
        stageWeight: 0,
        stageLabel: 'Error',
      };
    default:
      return {
        stageName: 'queued',
        stageNumber: 0,
        stageOffset: 0,
        stageWeight: 0,
        stageLabel: 'Queued',
      };
  }
}

// Estimated processing times (conservative averages)
const ESTIMATED_TIMES = {
  indexingSecondsPerMB: 2,      // ~2s per MB for indexing
  extractionSecondsPerChunk: 15, // ~15s per chunk for LLM extraction
  commitSecondsPerEntity: 0.05,  // ~0.05s per entity for graph commit
};

/**
 * Calculate estimated remaining time for a source.
 *
 * Prefers the live ``stage_progress.avg_ms`` EMA when an LLM stage is
 * active — that's the new single source of truth and ticks in real
 * time. Falls back to the older size/chunk/entity heuristic only when
 * no stage_progress row exists yet (early loading phase before any
 * LLM stage starts, or sources that completed before the facility
 * landed).
 */
function calculateEstimatedTime(
  source: UnifiedSource,
  stageName: StageName,
  stageProgress: number,
): number | null {
  if (stageName === 'complete' || stageName === 'error' || stageName === 'queued') {
    return null;
  }

  // Preferred source of truth: live stage_progress EMA.
  const active = getActiveStageProgress(source);
  if (active?.remainingSeconds != null) {
    return active.remainingSeconds;
  }

  // Fallback: pre-stage_progress heuristic. Used during the loader phase
  // (no LLM stage row yet) and for any path where stage_progress is
  // unpopulated. Kept conservative so the UI still shows _something_.
  const ingestion = source.ingestion;
  const fileSizeMB = source.size / (1024 * 1024);
  const chunksCount = ingestion?.chunks_count ?? 0;
  const entitiesCount = ingestion?.entities_count ?? 0;

  let remainingSeconds = 0;

  if (stageName === 'indexing') {
    const indexingRemaining = fileSizeMB * ESTIMATED_TIMES.indexingSecondsPerMB * (1 - stageProgress / 100);
    const extractionTime = Math.max(chunksCount, fileSizeMB * 3) * ESTIMATED_TIMES.extractionSecondsPerChunk;
    const commitTime = Math.max(entitiesCount, 50) * ESTIMATED_TIMES.commitSecondsPerEntity;
    remainingSeconds = indexingRemaining + extractionTime + commitTime;
  } else if (stageName === 'extraction') {
    const totalChunks = ingestion?.total_steps ?? chunksCount;
    const completedChunks = ingestion?.current_step ?? 0;
    const remainingChunks = totalChunks - completedChunks;
    const extractionRemaining = remainingChunks * ESTIMATED_TIMES.extractionSecondsPerChunk;
    const commitTime = Math.max(entitiesCount, 50) * ESTIMATED_TIMES.commitSecondsPerEntity;
    remainingSeconds = extractionRemaining + commitTime;
  } else if (stageName === 'commit') {
    const commitRemaining = Math.max(entitiesCount, 50) * ESTIMATED_TIMES.commitSecondsPerEntity * (1 - stageProgress / 100);
    remainingSeconds = commitRemaining;
  }

  return remainingSeconds > 0 ? Math.round(remainingSeconds) : null;
}

/**
 * Calculate segmented progress for a source file.
 *
 * The progress bar is divided into 3 weighted segments:
 * - Indexing: 10% of bar
 * - Extraction: 75% of bar
 * - Commit: 15% of bar
 *
 * Within each stage, progress fills that segment proportionally.
 */
export function calculateSegmentedProgress(source: UnifiedSource): SegmentedProgress {
  const { stageName, stageNumber, stageOffset, stageWeight, stageLabel } = getStageFromStatus(source.status);

  // Special cases
  if (stageName === 'complete') {
    return {
      totalProgress: 100,
      stageProgress: 100,
      stageName,
      stageNumber,
      stageLabel,
      estimatedRemainingSeconds: null,
    };
  }

  if (stageName === 'error' || stageName === 'queued') {
    return {
      totalProgress: 0,
      stageProgress: 0,
      stageName,
      stageNumber,
      stageLabel,
      estimatedRemainingSeconds: null,
    };
  }

  // Calculate intra-stage progress
  const currentStep = source.ingestion?.current_step ?? 0;
  const totalSteps = source.ingestion?.total_steps ?? 1;
  const stageProgress = totalSteps > 0 ? (currentStep / totalSteps) * 100 : 0;

  // Calculate total progress across all stages
  const totalProgress = stageOffset + (stageProgress / 100) * stageWeight;

  // Calculate estimated remaining time
  const estimatedRemainingSeconds = calculateEstimatedTime(source, stageName, stageProgress);

  return {
    totalProgress,
    stageProgress,
    stageName,
    stageNumber,
    stageLabel,
    estimatedRemainingSeconds,
  };
}

/**
 * Calculate stage progress from total progress.
 */
function calculateStageProgressFromTotal(totalProgress: number): {
  stageName: StageName;
  stageProgress: number;
} {
  const extractionEnd = STAGE_WEIGHTS.indexing + STAGE_WEIGHTS.extraction;

  if (totalProgress >= 100) {
    return { stageName: 'complete', stageProgress: 100 };
  }
  if (totalProgress >= extractionEnd) {
    // In commit stage (85-100)
    const stageProgress = ((totalProgress - extractionEnd) / STAGE_WEIGHTS.commit) * 100;
    return { stageName: 'commit', stageProgress };
  }
  if (totalProgress >= STAGE_WEIGHTS.indexing) {
    // In extraction stage (10-85)
    const stageProgress = ((totalProgress - STAGE_WEIGHTS.indexing) / STAGE_WEIGHTS.extraction) * 100;
    return { stageName: 'extraction', stageProgress };
  }
  // In indexing stage (0-10)
  const stageProgress = (totalProgress / STAGE_WEIGHTS.indexing) * 100;
  return { stageName: 'indexing', stageProgress };
}

/**
 * Aggregate progress across multiple sources with weighting.
 * Files with more chunks contribute more to overall progress.
 */
export function aggregateProgress(sources: UnifiedSource[]): {
  totalProgress: number;
  stageProgress: number;
  dominantStage: StageName;
  dominantStageLabel: string;
  totalEstimatedSeconds: number | null;
} {
  if (sources.length === 0) {
    return { totalProgress: 0, stageProgress: 0, dominantStage: 'queued', dominantStageLabel: 'Queued', totalEstimatedSeconds: null };
  }

  let totalWeightedProgress = 0;
  let totalWeight = 0;
  let totalEstimatedSeconds = 0;
  let hasEstimate = false;
  const stageCounts: Record<StageName, number> = {
    indexing: 0,
    extraction: 0,
    commit: 0,
    complete: 0,
    error: 0,
    queued: 0,
  };

  for (const source of sources) {
    const progress = calculateSegmentedProgress(source);
    // Weight by chunk count (default to 1 for files without chunk info)
    const weight = source.ingestion?.chunks_count || 1;

    totalWeightedProgress += progress.totalProgress * weight;
    totalWeight += weight;

    if (progress.estimatedRemainingSeconds) {
      totalEstimatedSeconds += progress.estimatedRemainingSeconds;
      hasEstimate = true;
    }

    stageCounts[progress.stageName]++;
  }

  const totalProgress = totalWeight > 0 ? totalWeightedProgress / totalWeight : 0;

  // Calculate stage progress from the aggregate total
  const { stageName: calculatedStage, stageProgress } = calculateStageProgressFromTotal(totalProgress);

  // Find dominant stage (the one with most files) - for display label
  const dominantEntry = Object.entries(stageCounts)
    .filter(([stage]) => stage !== 'complete' && stage !== 'error' && stage !== 'queued')
    .sort((a, b) => b[1] - a[1])[0];

  const dominantStage = dominantEntry ? (dominantEntry[0] as StageName) : calculatedStage;
  const stageLabels: Record<StageName, string> = {
    indexing: 'Indexing',
    extraction: 'Extracting',
    commit: 'Committing',
    complete: 'Complete',
    error: 'Error',
    queued: 'Queued',
  };

  return {
    totalProgress,
    stageProgress,
    dominantStage,
    dominantStageLabel: stageLabels[dominantStage],
    totalEstimatedSeconds: hasEstimate ? totalEstimatedSeconds : null,
  };
}
