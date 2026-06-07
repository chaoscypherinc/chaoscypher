// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * QueueStatsCards — Overview stat cards and per-queue depth indicators.
 */

import { Box, Typography, Tooltip } from '@mui/material';
import { getCardStyle, CardColors } from '../../../theme/cardStyles';
import { getQueueColor, getQueueDisplayName } from '../../../theme/colors';
import type { QueueStatsEntry as QueueStats } from '../../../services/api/useQueue';

interface QueueStatsCardsProps {
  /** Aggregated stats per queue from the API. */
  stats: QueueStats[];
  /** Total tasks across all pages (may exceed displayed count). */
  totalTasks: number;
  /** Number of tasks shown on the current page. */
  displayedTasks: number;
  /** Total items sitting in queues. */
  totalQueued: number;
  /** Currently running tasks (across all queues). */
  runningTasks: number;
  /** Recently failed tasks. */
  failedTasks: number;
}

const cardWrapSx = { flex: '1 1 calc(16.666% - 20px)', minWidth: 150 };

/** Stat card grid: overview counts + per-queue running/workers/depth. */
export function QueueStatsCards({
  stats,
  totalTasks,
  displayedTasks,
  totalQueued,
  runningTasks,
  failedTasks,
}: QueueStatsCardsProps) {
  return (
    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 3, mb: 3 }}>
      {/* Total Tasks */}
      <Box sx={cardWrapSx}>
        <Box sx={{ ...getCardStyle(CardColors.info, false), p: 2, height: '100%' }}>
          <Typography variant="body2" gutterBottom sx={{ color: 'text.secondary' }}>
            Total Tasks
          </Typography>
          <Typography variant="h3" sx={{ color: CardColors.info }}>
            {totalTasks}
          </Typography>
          {displayedTasks < totalTasks && (
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              (showing {displayedTasks})
            </Typography>
          )}
        </Box>
      </Box>

      {/* Queued */}
      <Box sx={cardWrapSx}>
        <Box sx={{ ...getCardStyle(CardColors.primary, false), p: 2, height: '100%' }}>
          <Typography variant="body2" gutterBottom sx={{ color: 'text.secondary' }}>
            Queued
          </Typography>
          <Typography variant="h3" sx={{ color: CardColors.primary }}>
            {totalQueued}
          </Typography>
        </Box>
      </Box>

      {/* Running */}
      <Box sx={cardWrapSx}>
        <Box sx={{ ...getCardStyle(CardColors.success, false), p: 2, height: '100%' }}>
          <Typography variant="body2" gutterBottom sx={{ color: 'text.secondary' }}>
            Running
          </Typography>
          <Typography variant="h3" sx={{ color: CardColors.success }}>
            {runningTasks}
          </Typography>
        </Box>
      </Box>

      {/* Failed */}
      <Box sx={cardWrapSx}>
        <Box sx={{ ...getCardStyle(CardColors.error, false), p: 2, height: '100%' }}>
          <Typography variant="body2" gutterBottom sx={{ color: 'text.secondary' }}>
            Failed (Recent)
          </Typography>
          <Typography variant="h3" sx={{ color: CardColors.error }}>
            {failedTasks}
          </Typography>
        </Box>
      </Box>

      {/* Per-queue cards */}
      {stats.map((queue) => {
        const runningCount = queue.running || 0;
        const queueColor = getQueueColor(queue.queue);
        return (
          <Box key={queue.queue} sx={cardWrapSx}>
            <Box sx={{ ...getCardStyle(queueColor, false), p: 2, height: '100%' }}>
              <Typography variant="body2" gutterBottom sx={{ color: 'text.secondary' }}>
                {getQueueDisplayName(queue.queue)}
              </Typography>
              <Box
                sx={{
                  display: 'flex',
                  gap: 0.5,
                  alignItems: 'baseline',
                  justifyContent: 'center',
                }}
              >
                <Tooltip title="Running Jobs" arrow>
                  <Typography
                    variant="h3"
                    sx={{ color: queueColor, lineHeight: 1, cursor: 'help' }}
                  >
                    {runningCount}
                  </Typography>
                </Tooltip>
                <Typography
                  variant="h5"
                  sx={{ color: 'text.secondary', lineHeight: 1, mx: 0.5 }}
                >
                  /
                </Typography>
                <Tooltip title="Available Workers" arrow>
                  <Typography
                    variant="h5"
                    sx={{ color: 'text.secondary', lineHeight: 1, cursor: 'help' }}
                  >
                    {queue.workers}
                  </Typography>
                </Tooltip>
              </Box>
              <Tooltip title={`Queued: ${queue.queued} / ${queue.max_depth} max`} arrow>
                <Typography
                  variant="caption"
                  sx={{ color: 'text.secondary', fontSize: '0.7rem', cursor: 'help' }}
                >
                  Queue: {queue.queued}
                </Typography>
              </Tooltip>
            </Box>
          </Box>
        );
      })}
    </Box>
  );
}
