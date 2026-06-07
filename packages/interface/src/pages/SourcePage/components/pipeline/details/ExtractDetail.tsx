// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { Box } from '@mui/material';
import type { ExtractionChartTask, ExtractionTask } from '../../../../../types';
import { ChunkGrid } from './ChunkGrid';
import { ChunkDetailCard } from './ChunkDetailCard';

export interface ExtractDetailProps {
  chartTasks: ExtractionChartTask[];
  selectedChunkId: string | null;
  selectedTask: ExtractionTask | null;
  selectedTaskLoading: boolean;
  onSelectChunk: (id: string | null) => void;
  onRerun: (taskId: string) => void;
  onViewChunk: (task: ExtractionTask) => void;
  isRerunning?: boolean;
}

/**
 * The per-chunk extraction navigator: a clickable grid of chunk task cells
 * (one per chunk group) + the selected chunk's detail card. Selecting a cell
 * shows its detail; "View chunk" jumps to that chunk in the list below. The
 * per-source counter aggregates moved to the Overview Pipeline Flow section.
 */
export function ExtractDetail({
  chartTasks,
  selectedChunkId,
  selectedTask,
  selectedTaskLoading,
  onSelectChunk,
  onRerun,
  onViewChunk,
  isRerunning,
}: ExtractDetailProps) {
  return (
    <Box>
      <ChunkGrid tasks={chartTasks} selectedChunkId={selectedChunkId} onSelectChunk={onSelectChunk} />
      <ChunkDetailCard
        task={selectedTask}
        loading={selectedTaskLoading}
        onRerun={onRerun}
        onViewChunk={onViewChunk}
        isRerunning={isRerunning}
      />
    </Box>
  );
}
