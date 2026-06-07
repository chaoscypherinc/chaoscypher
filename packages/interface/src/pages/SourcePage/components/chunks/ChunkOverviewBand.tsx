// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import { Box, Collapse, Typography } from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import InsightsIcon from '@mui/icons-material/Insights';
import ImageIcon from '@mui/icons-material/Image';
import type { ExtractionTask, Source } from '../../../../types';
import { ChunkAccent } from '../../../../theme/colors';
import { SURFACE_BORDER } from '../../../../theme/cardStyles';
import type { LLMProcessingState } from '../pipeline/hooks/useLLMProcessing';
import { useVisionPages } from '../../../../services/api/useVisionPages';
import { useRerunChunk } from '../../../../services/api/useChunkRerun';
import { useNotification } from '../../../../contexts/useNotification';
import { ExtractDetail } from '../pipeline/details/ExtractDetail';
import { PerformanceChartsSection } from '../pipeline/PerformanceChartsSection';
import { VisionPagesGrid } from '../pipeline/details/VisionPagesGrid';
import { ConfirmRerunDialog } from '../ConfirmRerunDialog';

interface ChunkOverviewBandProps {
  source: Source;
  /** LLM-processing state, lifted into ChunksTab so the prompts section can share it. */
  llm: LLMProcessingState;
  onSelectChunk: (id: string | null) => void;
  /** Jump to a chunk's group in the list below (in-page scroll). */
  onViewChunk: (task: ExtractionTask) => void;
}

interface PendingRerun {
  chunkIndex: number;
  priorAttemptCount: number;
}

interface SubSectionProps {
  label: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}

/** Collapsed-by-default disclosure for the band's heavier sub-views. */
function SubSection({ label, icon, children }: SubSectionProps) {
  const [open, setOpen] = useState(false);
  return (
    <Box sx={{ mt: 1 }}>
      <Box
        component="button"
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        sx={{
          all: 'unset',
          display: 'flex',
          alignItems: 'center',
          gap: 0.75,
          width: '100%',
          cursor: 'pointer',
          fontSize: '0.6rem',
          letterSpacing: 1.2,
          textTransform: 'uppercase',
          color: 'text.secondary',
          opacity: 0.8,
          py: 0.5,
          '&:focus-visible': { outline: `2px solid ${ChunkAccent.focus}`, outlineOffset: 2 },
        }}
      >
        <Box sx={{ display: 'flex', fontSize: 14 }}>{icon}</Box>
        {label}
        <ExpandMoreIcon
          sx={{ fontSize: 16, ml: 'auto', transition: 'transform 0.2s', transform: open ? 'rotate(180deg)' : 'none' }}
        />
      </Box>
      <Collapse in={open} unmountOnExit>
        <Box sx={{ pt: 1 }}>{children}</Box>
      </Collapse>
    </Box>
  );
}

/**
 * Chunk Overview band at the top of the Chunks tab. The per-chunk extraction
 * grid is the hero: each cell is a chunk; clicking one jumps to its text +
 * entities in the list below. An always-visible Performance charts block (the
 * context-utilization bar inside it is kept compact and secondary) and a
 * collapsible Page-images sub-section follow. Self-hides when the source has
 * neither extraction tasks nor a vision job. (Per-source counter aggregates
 * live in the Overview Pipeline Flow section.)
 */
export function ChunkOverviewBand({ source, llm, onSelectChunk, onViewChunk }: ChunkOverviewBandProps) {
  const { data: visionData } = useVisionPages(source.id);
  const { notify } = useNotification();
  const rerunMutation = useRerunChunk(source.id);
  const [pendingRerun, setPendingRerun] = useState<PendingRerun | null>(null);

  const hasExtraction = llm.chartTasks.length > 0;
  const hasVision = !!visionData?.job;
  if (!hasExtraction && !hasVision) return null;

  const retried = llm.stats?.total_retries ?? 0;

  const handleRerunChunk = (chunkIndex: number, priorAttemptCount: number) =>
    setPendingRerun({ chunkIndex, priorAttemptCount });

  const handleConfirmRerun = () => {
    if (!pendingRerun) return;
    const { chunkIndex } = pendingRerun;
    rerunMutation.mutate(
      { chunkIndex },
      {
        onSuccess: (data) => {
          notify(`Chunk ${chunkIndex + 1} rerun started (attempt ${data.attempt_number}).`, 'success');
          setPendingRerun(null);
        },
        onError: (err) => {
          notify(`Rerun failed: ${err.message}`, 'error');
          setPendingRerun(null);
        },
      },
    );
  };

  const handleCancelRerun = () => {
    if (rerunMutation.isPending) return;
    setPendingRerun(null);
  };

  return (
    <Box sx={{ mb: 3 }}>
      {hasExtraction && (
        <>
          <Typography sx={{ fontSize: '1rem', fontWeight: 600 }}>
            Extraction
          </Typography>
          <Typography sx={{ fontSize: '0.72rem', color: 'text.secondary', mt: 0.25, mb: 1.5 }}>
            Each cell is a chunk — click one to jump to its text &amp; entities below.{' '}
            <Box component="span" sx={{ opacity: 0.7 }}>
              {llm.chartTasks.length} chunks{retried > 0 ? ` · ${retried} retried` : ''}
            </Box>
          </Typography>
          <ExtractDetail
            chartTasks={llm.chartTasks}
            selectedChunkId={llm.selectedChunkId}
            selectedTask={llm.selectedTask}
            selectedTaskLoading={llm.selectedTaskLoading}
            onSelectChunk={onSelectChunk}
            onRerun={(taskId) => {
              const task =
                llm.selectedTask && llm.selectedTask.id === taskId
                  ? llm.selectedTask
                  : llm.chartTasks.find((t) => t.id === taskId);
              if (task) handleRerunChunk(task.chunk_index, task.retry_count ?? 0);
            }}
            onViewChunk={onViewChunk}
            isRerunning={rerunMutation.isPending}
          />
        </>
      )}

      {/* Performance is always visible (no collapse) so the per-chunk
          extraction grid above reads as the hero with its supporting charts
          right there. The faint accent rule separates the two without a hard
          colour break. */}
      {hasExtraction && (
        <Box sx={{ mt: 2, pt: 2, borderTop: `1px solid ${SURFACE_BORDER}` }}>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.75,
              mb: 1.5,
              fontSize: '0.6rem',
              letterSpacing: 1.2,
              textTransform: 'uppercase',
              color: 'text.secondary',
              opacity: 0.8,
            }}
          >
            <InsightsIcon sx={{ fontSize: 14 }} />
            Performance
          </Box>
          <PerformanceChartsSection tasks={[]} chartTasks={llm.chartTasks} stats={llm.stats} />
        </Box>
      )}

      {hasVision && (
        <SubSection label="Page images" icon={<ImageIcon sx={{ fontSize: 14 }} />}>
          <VisionPagesGrid sourceId={source.id} sourceStatus={source.status} />
        </SubSection>
      )}

      <ConfirmRerunDialog
        open={pendingRerun !== null}
        chunkIndex={pendingRerun?.chunkIndex ?? 0}
        priorAttemptCount={pendingRerun?.priorAttemptCount ?? 0}
        onConfirm={handleConfirmRerun}
        onCancel={handleCancelRerun}
        pending={rerunMutation.isPending}
      />
    </Box>
  );
}
