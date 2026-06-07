// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { Box, Tooltip } from '@mui/material';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import { FunnelPill, type PillSeverity } from './FunnelPill';
import { FunnelPillTooltip } from './FunnelPillTooltip';
import { StageStatsBoard } from './StageStatsBoard';
import { buildStageStats } from './stageStats';
import { PipelineStageColors } from '../../../../theme/colors';
import type { Source, ExtractionTaskStats } from '../../../../types';
import type { VisionJobSummary } from '../../../../services/api/useVisionPages';

export type FunnelStage = 'load' | 'clean' | 'chunk' | 'extract' | 'filter' | 'commit';

export interface PipelineFunnelProps {
  source: Source;
  llmStats: ExtractionTaskStats | null;
  /**
   * Vision-job summary from `useVisionPages(sourceId).data?.job`.
   * Null for text-only sources. Drives the LOAD pill's vision-failed
   * severity and the tooltip's per-vision counts.
   */
  visionJob: VisionJobSummary | null;
}

interface StageDescriptor {
  key: FunnelStage;
  count: string;
  label: string;
  sublabel: string;
  severity: PillSeverity;
  tooltip: { title: string; explanation: string; dataLines: string[]; footerHint: string };
}

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k`;
  return String(n);
}

function buildStages(
  source: Source,
  llmStats: ExtractionTaskStats | null,
  visionJob: VisionJobSummary | null,
): StageDescriptor[] {
  const m = source.quality_metrics;
  const loaded = source.total_content_length ?? 0;
  const charsRemoved = m?.cleaner_chars_removed ?? 0;
  const cleaned = Math.max(0, loaded - charsRemoved);
  const rawEntities = llmStats?.total_entities ?? 0;
  const filtered =
    (llmStats?.total_entities_filtered ?? 0) + (llmStats?.total_relationships_filtered ?? 0);
  const finalEntities = source.extraction_entities_count ?? 0;
  const failedPerm = m?.llm_chunks_failed_permanent ?? 0;
  const aborted = m?.llm_chunks_aborted_by_loop ?? 0;
  const timed = m?.llm_chunks_timed_out ?? 0;
  const retries = llmStats?.total_retries ?? 0;
  const indexStatus = m?.vector_indexing_status ?? 'pending';
  const embedFailures = m?.embedding_chunk_failures ?? 0;
  const visionFailed = visionJob?.failed ?? 0;
  const visionCompleted = visionJob?.completed ?? 0;
  const visionTotal = visionJob?.total_pages ?? 0;
  const pdfFailed = m?.loader_pdf_pages_failed ?? 0;

  // LOAD severity — flags genuine load failures (vision / pdf) via the ring.
  let loadSeverity: PillSeverity = 'neutral';
  if (visionFailed > 0) {
    loadSeverity = source.status === 'vision_pending' ? 'err' : 'warn';
  } else if (pdfFailed > 0) {
    loadSeverity = 'warn';
  }

  // EXTRACT severity — hard failures only. Retries are normal and stay neutral.
  const hardFail = failedPerm > 0 || aborted > 0 || timed > 0;
  const extractSeverity: PillSeverity = hardFail ? 'err' : 'neutral';

  // COMMIT severity — real index / embedding problems only.
  let commitSeverity: PillSeverity = 'neutral';
  if (embedFailures > 0 || indexStatus === 'failed') {
    commitSeverity = 'err';
  } else if (indexStatus === 'degraded') {
    commitSeverity = 'warn';
  }

  return [
    {
      key: 'load',
      count: fmt(loaded),
      label: 'LOAD',
      sublabel: 'chars',
      severity: loadSeverity,
      tooltip: {
        title: 'Loader',
        explanation: 'Read the source file and produced text. PDFs may also need vision processing.',
        dataLines: [
          `${fmt(loaded)} chars loaded`,
          ...(pdfFailed ? [`${pdfFailed} pdf pages failed`] : []),
          ...(visionJob
            ? [
                `vision: ${visionCompleted} / ${visionTotal} pages` +
                  (visionFailed > 0 ? ` (${visionFailed} failed)` : ''),
              ]
            : []),
        ],
        footerHint: 'loader counters + vision pages below',
      },
    },
    {
      key: 'clean',
      count: fmt(cleaned),
      label: 'CLEAN',
      sublabel: 'chars',
      // Normal cleanup removals are not a problem — stay neutral.
      severity: 'neutral',
      tooltip: {
        title: 'Cleanup',
        explanation: 'Stripped page numbers, dividers, OCR noise, and other structural junk.',
        dataLines: charsRemoved > 0 ? [`${fmt(charsRemoved)} chars removed`] : ['no edits applied'],
        footerHint: 'cleanup counters below',
      },
    },
    {
      key: 'chunk',
      count: fmt(source.chunk_count ?? 0),
      label: 'CHUNK',
      sublabel: 'groups',
      severity: 'neutral',
      tooltip: {
        title: 'Chunking',
        explanation: 'Split the cleaned text into chunks for LLM extraction.',
        dataLines: [`${source.chunk_count ?? 0} chunks`],
        footerHint: 'chunking counters below',
      },
    },
    {
      key: 'extract',
      count: rawEntities > 0 ? fmt(rawEntities) : '—',
      label: 'EXTRACT',
      sublabel: 'raw ent.',
      severity: extractSeverity,
      tooltip: {
        title: 'AI extraction',
        explanation: 'LLM read each chunk and surfaced entities + relationships.',
        dataLines: llmStats
          ? [
              `${llmStats.total_tasks} chunks · ${retries} retried`,
              ...(failedPerm ? [`${failedPerm} failed permanent`] : []),
              ...(llmStats.avg_duration_ms
                ? [`avg ${(llmStats.avg_duration_ms / 1000).toFixed(1)}s`]
                : []),
            ]
          : [],
        footerHint: 'prompts, tasks, charts below',
      },
    },
    {
      // FILTER pill — "kept" is the final entities count, which is what
      // survived all post-extraction filters AND committed. `filtered`
      // mixes entities + relationships so we never subtract it from
      // `rawEntities` (entities only) — that produced negative counts.
      key: 'filter',
      count: finalEntities > 0 ? fmt(finalEntities) : '—',
      label: 'FILTER',
      sublabel: 'entities kept',
      // Normal post-extraction filtering is expected — stay neutral.
      severity: 'neutral',
      tooltip: {
        title: 'Post-extraction filters',
        explanation: 'Removed structural / orphan / invalid entities and relationships.',
        dataLines: filtered > 0
          ? [
              `${llmStats?.total_entities_filtered ?? 0} entities dropped`,
              `${llmStats?.total_relationships_filtered ?? 0} relationships dropped`,
              `${finalEntities} entities kept`,
            ]
          : ['nothing dropped'],
        footerHint: 'filter breakdown below',
      },
    },
    {
      key: 'commit',
      count: indexStatus === 'indexed' ? fmt(finalEntities) : '…',
      label: 'COMMIT',
      sublabel: indexStatus === 'indexed' ? 'final · ✓ indexed' : `final · ${indexStatus}`,
      severity: commitSeverity,
      tooltip: {
        title: 'Commit + index',
        explanation: 'Entities saved, embedded into vectors, and indexed for search.',
        dataLines: [
          `${finalEntities} entities saved`,
          `search index: ${indexStatus}`,
          ...(embedFailures ? [`${embedFailures} embedding failures`] : []),
        ],
        footerHint: 'citation counters below',
      },
    },
  ];
}

/**
 * Vertical center of a pill — used to align separators with the pill
 * circle without depending on flexbox auto-stretch (the labels stack
 * underneath each pill and have variable height, so ``alignItems: 'center'``
 * would shift the arrows down out of line with the circles).
 */
const PILL_CIRCLE_CENTER_PX = 31; // 62px circle / 2

/** Fixed horizontal footprint for separators so the rhythm is uniform. */
const SEP_WIDTH_PX = 48;

function ArrowSep() {
  return (
    <Box
      aria-hidden
      sx={{
        width: SEP_WIDTH_PX,
        height: PILL_CIRCLE_CENTER_PX * 2,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: '#5b9a5f',
        opacity: 0.75,
      }}
    >
      <ChevronRightIcon sx={{ fontSize: 32 }} />
    </Box>
  );
}

function UnitDivider() {
  return (
    <Box
      aria-hidden
      sx={{
        width: SEP_WIDTH_PX,
        height: PILL_CIRCLE_CENTER_PX * 2,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <Box sx={{ width: '2px', height: '85%', bgcolor: 'rgba(255,255,255,0.22)' }} />
    </Box>
  );
}

export function PipelineFunnel({ source, llmStats, visionJob }: PipelineFunnelProps) {
  const stages = buildStages(source, llmStats, visionJob);

  // Grid columns: 6 equal-width pills.
  //
  // `minmax(0, 1fr)` (not bare `1fr`) is required so wide pill content
  // doesn't push that cell wider than its share and squish the others.
  // `justifyItems: center` centers the pill horizontally inside its cell
  // so the absolutely-positioned separators between cells land at the
  // visual midpoint between pill circles, not against one circle's edge.
  const gridTemplateColumns = `repeat(6, minmax(0, 1fr))`;

  return (
    <Box sx={{ pt: 0.5, mb: 1.75 }}>
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns,
          alignItems: 'start',
          justifyItems: 'center',
          columnGap: 0,
        }}
      >
        {stages.map((stage, idx) => {
          const isLast = idx === stages.length - 1;
          const isBeforeExtract = stages[idx + 1]?.key === 'extract';
          return (
            <Box
              key={stage.key}
              data-pill={stage.key}
              sx={{
                position: 'relative',
                // Span the cell horizontally so the right-anchored
                // separator measures from the cell boundary (otherwise
                // a content-sized wrapper would land the separator
                // against the pill edge instead of the cell edge).
                width: '100%',
                display: 'flex',
                justifyContent: 'center',
              }}
            >
              <Tooltip
                title={<FunnelPillTooltip {...stage.tooltip} />}
                arrow
                placement="bottom"
              >
                <Box>
                  <FunnelPill
                    count={stage.count}
                    label={stage.label}
                    sublabel={stage.sublabel}
                    severity={stage.severity}
                    stageColor={PipelineStageColors[stage.key]}
                    selected={false}
                    interactive={false}
                  />
                </Box>
              </Tooltip>
              {!isLast && (
                <Box
                  sx={{
                    position: 'absolute',
                    // Center separator horizontally on the boundary between
                    // this pill and the next, vertically on the pill circle.
                    right: `calc(-${SEP_WIDTH_PX / 2}px)`,
                    top: 0,
                    pointerEvents: 'none',
                  }}
                >
                  {isBeforeExtract ? <UnitDivider /> : <ArrowSep />}
                </Box>
              )}
            </Box>
          );
        })}
      </Box>
      <StageStatsBoard stats={buildStageStats(source, llmStats)} />
    </Box>
  );
}
