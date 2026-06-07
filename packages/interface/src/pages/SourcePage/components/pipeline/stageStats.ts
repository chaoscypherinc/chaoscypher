// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import type { ExtractionTaskStats, Source } from '../../../../types';
import type { FunnelStage } from './PipelineFunnel';

export type StatTone = 'neutral' | 'warn' | 'err';

export interface StageStatItem {
  label: string;
  value: string | number;
  tone?: StatTone;
  /** Plain-language explanation of the counter, surfaced as a hover tooltip. */
  description?: string;
}

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k`;
  return String(n);
}

// quality_metrics is loosely typed in the generated client; read through a
// permissive shape rather than polluting the shared Source type.
type QM = Record<string, number | string | undefined> | undefined;

/**
 * Per-stage stat items for the Processing stage stats board. Mirrors the
 * counters previously rendered by the per-stage detail components, minus the
 * vision grid (its own section). Only non-zero / meaningful counters are kept.
 */
export function buildStageStats(
  source: Source,
  llmStats: ExtractionTaskStats | null,
): Record<FunnelStage, StageStatItem[]> {
  const m = (source.quality_metrics as unknown as QM) ?? {};
  const num = (k: string): number => (typeof m[k] === 'number' ? (m[k] as number) : 0);

  const load: StageStatItem[] = [];
  if (num('loader_pdf_pages_failed') > 0) load.push({ label: 'PDF failed', value: num('loader_pdf_pages_failed'), tone: 'warn', description: 'Pages in the PDF that failed to parse and were skipped.' });
  if (num('loader_warnings_count') > 0) load.push({ label: 'Warnings', value: num('loader_warnings_count'), tone: 'warn', description: 'Non-fatal warnings emitted by the loader while reading the source.' });
  if (num('loader_files_skipped') > 0) load.push({ label: 'Files skipped', value: num('loader_files_skipped'), tone: 'warn', description: 'Files in the upload that were skipped — unsupported type or unreadable.' });
  if (num('loader_replacement_chars_count') > 0) load.push({ label: 'Repl. chars', value: num('loader_replacement_chars_count'), tone: 'warn', description: 'Unicode replacement characters (�) found — usually a decoding/encoding problem in the source.' });

  const clean: StageStatItem[] = [];
  if (num('cleaner_chars_removed') > 0) clean.push({ label: 'Removed', value: fmt(num('cleaner_chars_removed')), description: 'Characters removed by the text cleaner — boilerplate, control characters, and markup.' });
  if (num('cleaner_lines_removed') > 0) clean.push({ label: 'Lines', value: num('cleaner_lines_removed'), description: 'Whole lines removed by the cleaner.' });
  if (num('cleaner_paragraphs_deduplicated') > 0) clean.push({ label: 'Dedup ¶', value: num('cleaner_paragraphs_deduplicated'), description: 'Duplicate paragraphs removed (de-duplicated) before chunking.' });
  if (num('cleaner_plugin_load_failures') > 0) clean.push({ label: 'Plugin fails', value: num('cleaner_plugin_load_failures'), tone: 'warn', description: 'Cleaner plugins that failed to load — their cleaning step was skipped.' });

  const chunk: StageStatItem[] = [];
  if (num('chunks_coalesced_count') > 0) chunk.push({ label: 'Coalesced', value: num('chunks_coalesced_count'), description: 'Small adjacent chunks merged together to reach a workable size for extraction.' });
  if (num('chunker_normalize_drops') > 0) chunk.push({ label: 'Normalized', value: num('chunker_normalize_drops'), description: 'Chunks dropped during whitespace/format normalization (left empty after cleanup).' });
  if (num('chunks_skipped_by_depth') > 0) chunk.push({ label: 'Skipped', value: num('chunks_skipped_by_depth'), tone: 'warn', description: 'Chunks skipped because of the configured extraction depth.' });
  if (num('standalone_chunk_failures') > 0) chunk.push({ label: 'Failures', value: num('standalone_chunk_failures'), tone: 'warn', description: 'Standalone chunks that failed to process.' });

  const extract: StageStatItem[] = [];
  if (llmStats && llmStats.total_tasks > 0) extract.push({ label: 'Chunks', value: llmStats.total_tasks, description: 'Chunk groups sent to the LLM for entity/relationship extraction.' });
  if ((llmStats?.total_retries ?? 0) > 0) extract.push({ label: 'Retried', value: llmStats!.total_retries, description: 'Extraction calls retried after a transient failure (timeout, empty output, rate limit).' });

  const filter: StageStatItem[] = [];
  if (num('dedup_entities_merged') > 0) filter.push({ label: 'Merged', value: num('dedup_entities_merged'), description: 'Duplicate entities merged into one during cross-chunk de-duplication.' });
  if (num('structural_entities_filtered') > 0) filter.push({ label: 'Structural', value: num('structural_entities_filtered'), description: 'Entities removed by structural filters — headings, formatting artifacts, and other non-entities.' });
  if (num('orphan_entities_filtered') > 0) filter.push({ label: 'Orphan', value: num('orphan_entities_filtered'), description: 'Entities removed because they had no relationships (orphans), when orphan protection is off.' });
  if (num('relationships_dropped_invalid') > 0) filter.push({ label: 'Invalid rels', value: num('relationships_dropped_invalid'), description: 'Relationships dropped for being invalid — missing endpoints or a disallowed type.' });
  if (num('relationships_dropped_capped') > 0) filter.push({ label: 'Capped rels', value: num('relationships_dropped_capped'), description: 'Relationships dropped because a per-chunk relationship cap was exceeded.' });
  if (num('relationships_direction_corrected') > 0) filter.push({ label: 'Dir fixed', value: num('relationships_direction_corrected'), description: 'Relationships whose direction was auto-corrected to match the template definition.' });

  const commit: StageStatItem[] = [];
  if (num('citations_skipped_no_chunk_index') > 0) commit.push({ label: 'Cites · no chunk', value: num('citations_skipped_no_chunk_index'), tone: 'warn', description: 'Citations skipped because they carried no chunk index to anchor to.' });
  if (num('citations_skipped_index_not_mapped') > 0) commit.push({ label: 'Cites · unmapped', value: num('citations_skipped_index_not_mapped'), tone: 'warn', description: 'Citations skipped because their chunk index could not be mapped to a stored chunk.' });
  if (num('embedding_chunk_failures') > 0) commit.push({ label: 'Embed fails', value: num('embedding_chunk_failures'), tone: 'err', description: 'Chunks that failed to embed — no vector was produced, so they are not searchable.' });
  if (num('embedding_dimension_mismatches') > 0) commit.push({ label: 'Dim mismatch', value: num('embedding_dimension_mismatches'), tone: 'err', description: 'Embeddings whose dimension did not match the search index and were skipped.' });
  const idx = m['vector_indexing_status'];
  if (typeof idx === 'string' && idx !== 'indexed') {
    commit.push({ label: 'Search index', value: idx, tone: idx === 'failed' ? 'err' : 'warn', description: 'Vector-search index status for this source. Anything other than "indexed" means it is not yet fully searchable.' });
  }

  return { load, clean, chunk, extract, filter, commit };
}
