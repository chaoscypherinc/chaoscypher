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
  if (num('loader_epub_chapters_skipped') > 0) load.push({ label: 'Chapters skipped', value: num('loader_epub_chapters_skipped'), tone: 'warn', description: 'EPUB chapters listed in the reading order that could not be loaded — missing from the manifest or absent from the archive.' });

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
  // Keep, flag, never delete (2026-10-07): orphan_entities_filtered now counts the edgeless
  // entities KEPT (flagged `orphan`); evidence_less_entities_dropped the ones still dropped.
  if (num('orphan_entities_filtered') > 0) filter.push({ label: 'Unconnected', value: num('orphan_entities_filtered'), description: 'Entities committed with no relationship, kept because they carry a description or evidence and flagged as unconnected (gathered under the canvas\'s Unresolved node).' });
  if (num('evidence_less_entities_dropped') > 0) filter.push({ label: 'No evidence', value: num('evidence_less_entities_dropped'), description: 'Entities with no relationship and neither a description nor an evidence sentence, dropped at commit (kept when orphan protection is on).' });
  // Keep, flag, never delete (2026-10-07): per-chunk entities kept flagged instead of dropped.
  if (num('plausibility_entities_flagged') > 0) filter.push({ label: 'Low plausibility', value: num('plausibility_entities_flagged'), description: 'Entities whose name scored below the plausibility bar, kept because they carry a description or evidence and flagged as low plausibility (gathered under the canvas\'s Unresolved node).' });
  if (num('unplaced_type_entities_flagged') > 0) filter.push({ label: 'Unplaced type', value: num('unplaced_type_entities_flagged'), description: 'Entities whose written type no core type answers to, kept as a Concept with the written type and flagged as unplaced (gathered under the canvas\'s Unresolved node).' });
  // Keep, flag, never delete (2026-10-07): relationships_unevidenced_kept counts the relationships
  // the evidence filter KEPT flagged `unevidenced`; evidence_relationships_dropped only the ones it
  // still drops (an endpoint that is no entity), where it used to count every one without evidence.
  if (num('relationships_unevidenced_kept') > 0) filter.push({ label: 'Unevidenced', value: num('relationships_unevidenced_kept'), description: 'Relationships no cited sentence states, kept with their justification and the reason, drawn dashed on the canvas and left out of chat answers by default.' });
  if (num('evidence_relationships_dropped') > 0) filter.push({ label: 'No-entity rels', value: num('evidence_relationships_dropped'), description: 'Relationships the evidence filter dropped because an endpoint is no entity of their chunk.' });
  // Keep, flag, never delete (2026-10-08): the entity half of the evidence filter, the domain's
  // exclusion examples and the strict type check keep what they used to drop, flagged.
  // evidence_entities_dropped now counts only the entities with no description, and
  // relationships_dropped_type_unmatched the relationships the type check KEPT flagged.
  if (num('entities_unevidenced_kept') > 0) filter.push({ label: 'Unevidenced ents', value: num('entities_unevidenced_kept'), description: 'Entities whose cited sentence does not name them, kept because they carry a description and flagged as unevidenced (gathered under the canvas\'s Unresolved node).' });
  if (num('evidence_entities_dropped') > 0) filter.push({ label: 'No-evidence ents', value: num('evidence_entities_dropped'), description: 'Entities the evidence filter dropped: their cited sentence does not name them and they carry no description.' });
  if (num('excluded_entities_kept') > 0) filter.push({ label: 'Excluded names', value: num('excluded_entities_kept'), description: 'Entities whose name is one of the domain\'s exclusion examples, kept because they carry a description or evidence and flagged as an excluded name (gathered under the canvas\'s Unresolved node).' });
  // The known-entities guards (2026-10-11): pass 1 is checked against the entities the source's
  // earlier groups named. A known name the guard could not support is counted whether it was
  // demoted or kept as written and flagged.
  if (num('aliases_crossed_known_dropped') > 0) filter.push({ label: 'Crossed aliases', value: num('aliases_crossed_known_dropped'), description: 'Aliases that are the name of another entity this source already knows, taken off the entity that gave them and kept on it as the crossed alias.' });
  if (num('referents_unevidenced_demoted') > 0) filter.push({ label: 'Demoted or flagged names', value: num('referents_unevidenced_demoted'), description: 'Entities written under a name this source already knows that their cited sentences never write: renamed to the one reference those sentences write, or kept under the name and flagged when they write none or several (their relationships then kept flagged as unevidenced), with the name they were given kept beside it.' });
  if (num('relationships_unmatched_label_kept') > 0) filter.push({ label: 'Unmatched labels', value: num('relationships_unmatched_label_kept'), description: 'Relationships whose label no relationship type of the domain answers to, kept as related_to with the label as written, flagged, ranked last and drawn thin.' });
  if (num('relationships_type_mismatch_kept') > 0) filter.push({ label: 'Type mismatches', value: num('relationships_type_mismatch_kept'), description: 'Relationships whose endpoints are not the types their relationship type allows, kept with their label, flagged, ranked last and drawn thin.' });
  if (num('relationships_dropped_invalid') > 0) filter.push({ label: 'Invalid rels', value: num('relationships_dropped_invalid'), description: 'Relationships dropped for being invalid — missing endpoints or a disallowed type.' });
  // Since 2026-10-07 relationships_dropped_capped counts the relationships past a cap KEPT
  // (flagged `capped`, drawn thin); the caps drop nothing.
  if (num('relationships_dropped_capped') > 0) filter.push({ label: 'Capped rels', value: num('relationships_dropped_capped'), description: 'Relationships past a relationship cap (same source and type, entity degree or total), kept and flagged as capped: they rank last and are drawn thin on the canvas.' });
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
