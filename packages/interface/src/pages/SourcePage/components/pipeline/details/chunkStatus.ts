// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Shared status mapping for the per-chunk extraction hero (tile grid + the
 * whole-source status rail). One place so the tiles, the rail, and their
 * legends never drift apart.
 *
 * Extraction runs per *group* (one LLM call → one ExtractionTask), so a
 * "cell" here is a group; we keep the existing "chunk" wording the rest of
 * the tab uses to avoid a terminology split.
 */

/** Visual status bucket for a chunk/group cell. */
export type ChunkStatusKind = 'ok' | 'retried' | 'failed' | 'pending';

/** Map a task's raw status + retry count to a visual bucket. */
export function chunkStatusKind(task: { status: string; retry_count: number }): ChunkStatusKind {
  if (task.status === 'failed') return 'failed';
  if (task.status === 'completed') return task.retry_count > 0 ? 'retried' : 'ok';
  return 'pending';
}

/** Accent (`main`) + faint fill (`bg`) per bucket — matches the long-standing grid legend hues. */
export const ChunkStatusColor: Record<ChunkStatusKind, { main: string; bg: string }> = {
  ok: { main: '#7fcc84', bg: 'rgba(91,154,95,0.15)' },
  retried: { main: '#ffa726', bg: 'rgba(255,167,38,0.18)' },
  failed: { main: '#ef5350', bg: 'rgba(244,67,54,0.20)' },
  pending: { main: '#888', bg: 'rgba(255,255,255,0.04)' },
};

/** Short human label per bucket (badges, tooltips). */
export const ChunkStatusLabel: Record<ChunkStatusKind, string> = {
  ok: 'ok',
  retried: 'retried',
  failed: 'failed',
  pending: 'pending',
};
