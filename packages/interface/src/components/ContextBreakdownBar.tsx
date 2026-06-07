// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Context Breakdown Bar Component
 *
 * Visualizes how the LLM context window is allocated during entity extraction.
 * Shows a SINGLE bar representing the shared context pool where:
 *   Input + Output ≤ Context Window
 *
 * Segments:
 * - System tokens (prompt template & instructions)
 * - Input tokens (chunks being processed)
 * - Output tokens (extraction results) - capped by min(available, maxOutputTokens)
 * - Buffer (remaining headroom)
 *
 * Also shows an output cap indicator when maxOutputTokens < available space.
 */

import { Box, Typography, Tooltip, useTheme, alpha } from '@mui/material';
import { ContextColors } from '../theme/colors';
import { formatCompactNumber } from '../utils/formatters';
import InputIcon from '@mui/icons-material/Input';
import SystemIcon from '@mui/icons-material/Psychology';
import OutputIcon from '@mui/icons-material/Output';
import MemoryIcon from '@mui/icons-material/Memory';
import WarningIcon from '@mui/icons-material/Warning';

import { calculateContextBreakdown, DEFAULT_OUTPUT_TOKENS_PER_CHUNK } from './contextBreakdown';

interface ContextBreakdownProps {
  /** Total context window in tokens (shared between input and output) */
  contextWindow: number;
  /** Maximum output tokens cap (separate limit, often smaller than context window) */
  maxOutputTokens: number;
  /** Fixed group size (chunks per LLM call) from settings - default 4 */
  groupSize?: number;
  /** Input tokens per chunk (small_chunk_size / 4) */
  inputPerChunk?: number;
  /** Expected output per chunk (tokens) - default 2000 */
  outputPerChunk?: number;
}

/** Format large token numbers as K/M for display. */
const formatTokens = formatCompactNumber;

/**
 * Visual bar showing context window allocation with color-coded segments.
 * Shows a single bar representing the shared context pool.
 */
export function ContextBreakdownBar({
  contextWindow,
  maxOutputTokens,
  groupSize = 4,
  inputPerChunk = 150,
  outputPerChunk = DEFAULT_OUTPUT_TOKENS_PER_CHUNK,
}: ContextBreakdownProps) {
  const theme = useTheme();
  const breakdown = calculateContextBreakdown(
    contextWindow,
    maxOutputTokens,
    inputPerChunk,
    outputPerChunk,
    groupSize
  );

  // Color scheme
  const colors = {
    ...ContextColors,
    buffer: alpha(theme.palette.grey[300], 0.3),
  };

  const segments = [
    {
      key: 'system',
      label: 'System',
      tokens: breakdown.systemTokens,
      percentage: breakdown.percentages.system,
      color: colors.system,
      icon: <SystemIcon sx={{ fontSize: 14 }} />,
      description: 'System prompt & extraction instructions',
    },
    {
      key: 'input',
      label: 'Input',
      tokens: breakdown.inputTokens,
      percentage: breakdown.percentages.input,
      color: colors.input,
      icon: <InputIcon sx={{ fontSize: 14 }} />,
      description: `${breakdown.chunks} chunks @ ${inputPerChunk} tokens each`,
    },
    {
      key: 'output',
      label: 'Output',
      tokens: breakdown.outputBudget,
      percentage: breakdown.percentages.output,
      color: colors.output,
      icon: <OutputIcon sx={{ fontSize: 14 }} />,
      description: breakdown.warnings.outputCapHit
        ? `Capped at ${formatTokens(maxOutputTokens)} (expected ${formatTokens(breakdown.expectedOutput)})`
        : breakdown.warnings.contextConstrained
        ? `Limited by context space (expected ${formatTokens(breakdown.expectedOutput)})`
        : `Expected extraction results (${outputPerChunk} tokens/chunk)`,
    },
    {
      key: 'buffer',
      label: 'Buffer',
      tokens: breakdown.buffer,
      percentage: breakdown.percentages.buffer,
      color: colors.buffer,
      icon: <MemoryIcon sx={{ fontSize: 14 }} />,
      description: 'Safety buffer for variable content',
    },
  ];

  // Determine if we should show the output cap indicator
  // Show it when the cap is meaningful (less than available space after input)
  const showOutputCapLine = maxOutputTokens < breakdown.availableForOutput;

  // Calculate utilization
  const utilization = (breakdown.totalUsed / contextWindow) * 100;

  return (
    <Box sx={{ mt: 2 }}>
      {/* Header with chunks count and warnings */}
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
        <Typography variant="subtitle2" sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <MemoryIcon fontSize="small" color="action" />
          Extraction Capacity
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          {breakdown.warnings.outputCapHit && (
            <Tooltip title={`Output capped at ${formatTokens(maxOutputTokens)} tokens`} arrow>
              <WarningIcon sx={{ fontSize: 16, color: 'warning.main' }} />
            </Tooltip>
          )}
          {breakdown.warnings.contextConstrained && (
            <Tooltip title="Output limited by available context space" arrow>
              <WarningIcon sx={{ fontSize: 16, color: 'error.main' }} />
            </Tooltip>
          )}
          <Typography
            variant="body2"
            sx={{
              fontWeight: 600,
              color: 'primary.main',
              bgcolor: alpha(theme.palette.primary.main, 0.1),
              px: 1,
              py: 0.25,
              borderRadius: 1,
            }}
          >
            {breakdown.chunks} chunks/call
          </Typography>
        </Box>
      </Box>
      {/* Visual bar */}
      <Box sx={{ position: 'relative' }}>
        <Box
          sx={{
            display: 'flex',
            height: 24,
            borderRadius: 1,
            overflow: 'hidden',
            bgcolor: 'background.paper',
            border: 1,
            borderColor: 'divider',
          }}
        >
          {segments.map((segment) => (
            segment.percentage > 0 && (
              <Tooltip
                key={segment.key}
                title={
                  <Box>
                    <Typography variant="body2" sx={{
                      fontWeight: 600
                    }}>
                      {segment.label}: {segment.tokens.toLocaleString()} tokens
                    </Typography>
                    <Typography variant="caption" sx={{
                      color: "inherit"
                    }}>
                      {segment.description}
                    </Typography>
                  </Box>
                }
                arrow
              >
                <Box
                  sx={{
                    width: `${segment.percentage}%`,
                    bgcolor: segment.color,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    transition: 'all 0.3s ease',
                    cursor: 'pointer',
                    '&:hover': {
                      filter: 'brightness(1.1)',
                    },
                  }}
                >
                  {segment.percentage > 8 && (
                    <Typography
                      variant="caption"
                      sx={{
                        color: segment.key === 'buffer' ? 'text.secondary' : 'white',
                        fontWeight: 500,
                        fontSize: '0.65rem',
                      }}
                    >
                      {Math.round(segment.percentage)}%
                    </Typography>
                  )}
                </Box>
              </Tooltip>
            )
          ))}
        </Box>

        {/* Output cap indicator line */}
        {showOutputCapLine && (
          <Tooltip
            title={
              <Box>
                <Typography variant="body2" sx={{
                  fontWeight: 600
                }}>
                  Max Output Cap: {formatTokens(maxOutputTokens)} tokens
                </Typography>
                <Typography variant="caption" sx={{
                  color: "inherit"
                }}>
                  Output cannot exceed this limit regardless of available space
                </Typography>
              </Box>
            }
            arrow
          >
            <Box
              sx={{
                position: 'absolute',
                left: `${breakdown.percentages.outputCapPosition}%`,
                top: -4,
                bottom: -4,
                width: 3,
                bgcolor: colors.outputCap,
                borderRadius: 1,
                cursor: 'pointer',
                zIndex: 2,
                boxShadow: `0 0 4px ${colors.outputCap}`,
                '&:hover': {
                  bgcolor: alpha(colors.outputCap, 0.8),
                },
              }}
            />
          </Tooltip>
        )}
      </Box>
      {/* Legend */}
      <Box sx={{ display: 'flex', gap: 2, mt: 1, flexWrap: 'wrap' }}>
        {segments.map((segment) => (
          <Box
            key={segment.key}
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.5,
            }}
          >
            <Box
              sx={{
                width: 12,
                height: 12,
                borderRadius: 0.5,
                bgcolor: segment.color,
                border: segment.key === 'buffer' ? 1 : 0,
                borderColor: 'divider',
              }}
            />
            <Typography variant="caption" sx={{
              color: "text.secondary"
            }}>
              {segment.label}: {segment.tokens.toLocaleString()}
            </Typography>
          </Box>
        ))}
        {showOutputCapLine && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <Box
              sx={{
                width: 3,
                height: 12,
                borderRadius: 0.5,
                bgcolor: colors.outputCap,
              }}
            />
            <Typography variant="caption" sx={{
              color: "text.secondary"
            }}>
              Output Cap
            </Typography>
          </Box>
        )}
      </Box>
      {/* Utilization summary */}
      <Typography
        variant="caption"
        sx={{
          color: "text.secondary",
          display: 'block',
          mt: 0.5
        }}>
        {Math.round(utilization)}% context utilization
        ({breakdown.totalUsed.toLocaleString()} / {contextWindow.toLocaleString()} tokens)
        {breakdown.warnings.outputCapHit && (
          <Typography
            component="span"
            variant="caption"
            sx={{ color: 'warning.main', ml: 1 }}
          >
            Output capped at {formatTokens(maxOutputTokens)}
          </Typography>
        )}
      </Typography>
    </Box>
  );
}
