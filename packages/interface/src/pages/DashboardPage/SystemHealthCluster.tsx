// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Typography, Tooltip } from '@mui/material';
import { ChaosCypherPalette } from '../../theme/palette';
import type { ActivityEntry } from './types';
import type { ProgressSummary } from './useActivityLog';

/** Props for the SystemHealthCluster in the top-right dashboard corner. */
interface SystemHealthClusterProps {
  isIdle: boolean;
  activeCount: number;
  totalCostUsd: number;
  progress: ProgressSummary | null;
  activityEntries: ActivityEntry[];
  graphNodeCount: number;
  totalNodes: number;
}

/** Top-right system status cluster: status dot, cost, queue count, progress, and activity log. */
export default function SystemHealthCluster({
  isIdle,
  activeCount,
  totalCostUsd,
  progress,
  activityEntries,
  graphNodeCount,
  totalNodes,
}: SystemHealthClusterProps) {
  const statusColor = isIdle ? ChaosCypherPalette.success : ChaosCypherPalette.primary;
  const statusText = isIdle ? 'System idle' : `Processing (${activeCount})`;

  return (
    <Box
      sx={{
        position: 'absolute',
        top: 0,
        right: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-end',
        gap: '10px',
        pointerEvents: 'auto',
      }}
    >
      {/* Health row */}
      <Box
        sx={{
          display: 'flex',
          gap: '20px',
          fontSize: 11,
          opacity: 0.4,
          letterSpacing: 0.5,
          alignItems: 'center',
        }}
      >
        {/* Status indicator */}
        <Tooltip title={isIdle ? 'All queues idle — no background tasks running' : `${activeCount} task${activeCount !== 1 ? 's' : ''} active in background processing queues`} arrow placement="bottom-end">
          <Box sx={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'default' }}>
            <Box
              sx={{
                width: 7,
                height: 7,
                bgcolor: statusColor,
                borderRadius: '50%',
                boxShadow: `0 0 6px ${statusColor}, 0 0 12px ${statusColor}40`,
                animation: 'pulse-dot 3s ease-in-out infinite',
                '@keyframes pulse-dot': {
                  '0%, 100%': {
                    boxShadow: `0 0 6px ${statusColor}, 0 0 12px ${statusColor}40`,
                  },
                  '50%': {
                    boxShadow: `0 0 10px ${statusColor}, 0 0 20px ${statusColor}50`,
                  },
                },
              }}
            />
            <Typography sx={{ fontSize: 11, color: statusColor, opacity: 0.6 }}>
              {statusText}
            </Typography>
          </Box>
        </Tooltip>
        {/* Cost */}
        <Tooltip title={`Estimated LLM cost: ${totalCostUsd === 0 ? '$0.00' : totalCostUsd < 0.01 ? '<$0.01' : `$${totalCostUsd.toFixed(2)}`} — accumulated across all API providers this session`} arrow placement="bottom-end">
          <Box sx={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'default' }}>
            <svg
              width={13}
              height={13}
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.2}
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ opacity: 0.6 }}
            >
              <circle cx={8} cy={8} r={6.5} />
              <path d="M5.5 8.5L7 10l3.5-4" />
            </svg>
            <span>{totalCostUsd === 0 ? '$0.00' : totalCostUsd < 0.01 ? '<$0.01' : `$${totalCostUsd.toFixed(2)}`}</span>
          </Box>
        </Tooltip>
        {/* Queue */}
        <Tooltip title={activeCount === 0 ? 'Processing queue is empty' : `${activeCount} job${activeCount !== 1 ? 's' : ''} in queue — extraction, indexing, and chat tasks`} arrow placement="bottom-end">
          <Box sx={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'default' }}>
            <svg
              width={13}
              height={13}
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.2}
              strokeLinecap="round"
              style={{ opacity: 0.6 }}
            >
              <rect x={3} y={2} width={10} height={12} rx={1.5} />
              <line x1={5.5} y1={5.5} x2={10.5} y2={5.5} />
              <line x1={5.5} y1={8} x2={10.5} y2={8} />
              <line x1={5.5} y1={10.5} x2={8.5} y2={10.5} />
            </svg>
            <span>Queue: {activeCount}</span>
          </Box>
        </Tooltip>
      </Box>

      {/* Progress summary (when active) */}
      {progress && (
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            fontFamily: "'SF Mono', 'Cascadia Code', 'Fira Code', monospace",
            fontSize: 11,
          }}
        >
          <Box
            sx={{
              width: 5,
              height: 5,
              borderRadius: '50%',
              bgcolor: ChaosCypherPalette.primary,
              boxShadow: `0 0 4px ${ChaosCypherPalette.primary}`,
              animation: 'pulse-progress 1.5s ease-in-out infinite',
              '@keyframes pulse-progress': {
                '0%, 100%': { opacity: 1 },
                '50%': { opacity: 0.3 },
              },
            }}
          />
          <Typography
            sx={{
              color: ChaosCypherPalette.primary,
              opacity: 0.6,
              fontSize: 'inherit',
              fontFamily: 'inherit',
            }}
          >
            {progress.text}
          </Typography>
        </Box>
      )}

      {/* Activity log (recent completed or running tasks) - hidden on mobile to avoid overlap */}
      {activityEntries.length > 0 && (
        <Tooltip
          title={
            <>
              <strong>Recent Activity</strong>
              <br />
              {activityEntries.length} recent event{activityEntries.length !== 1 ? 's' : ''}
              {totalNodes > 0 && (
                <>
                  <br />
                  Graph: {graphNodeCount.toLocaleString()} of {totalNodes.toLocaleString()} nodes visualized
                </>
              )}
            </>
          }
          arrow
          placement="left"
        >
          <Box
            sx={{
              display: { xs: 'none', md: 'flex' },
              flexDirection: 'column',
              alignItems: 'flex-end',
              gap: '3px',
              fontFamily: "'SF Mono', 'Cascadia Code', 'Fira Code', monospace",
              fontSize: 10,
              maxHeight: 180,
              overflowY: 'auto',
              overflowX: 'hidden',
              cursor: 'default',
              // Hide scrollbar while keeping scroll functionality
              scrollbarWidth: 'none',
              '&::-webkit-scrollbar': { display: 'none' },
            }}
          >
            {activityEntries.map((entry) => (
              <Typography
                key={entry.id}
                sx={{
                  color: '#ffffff',
                  opacity: 0.35,
                  textAlign: 'right',
                  fontSize: 'inherit',
                  fontFamily: 'inherit',
                }}
              >
                <Box
                  component="span"
                  sx={{ opacity: 0.6, mr: '4px', color: ChaosCypherPalette.primary }}
                >
                  {entry.time}
                </Box>
                {entry.message}
              </Typography>
            ))}
          </Box>
        </Tooltip>
      )}
    </Box>
  );
}
