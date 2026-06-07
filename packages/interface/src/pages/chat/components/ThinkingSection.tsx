// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Chain of thought display for AI reasoning.
 *
 * Renders the AI's internal thinking/reasoning text with:
 * - Animated loading dots when thinking is in progress
 * - Step-by-step display when thinking contains `---` separators
 * - Plain monospace text for single-block thinking
 */

import { Box, Typography } from '@mui/material';
import LoadingDots from './LoadingDots';

interface ThinkingSectionProps {
  /** Raw thinking text from the AI response. */
  thinking: string;
}

export default function ThinkingSection({ thinking }: ThinkingSectionProps) {
  return (
    <Box
      sx={{
        mt: 1.5,
        pl: 2,
        borderLeft: '1px dashed rgba(0, 229, 255, 0.3)',
        width: '100%',
        maxHeight: '400px',
        overflow: 'auto',
        boxSizing: 'border-box',
      }}
    >
      <Typography variant="caption" sx={{ display: 'block', mb: 1, color: 'rgba(0, 229, 255, 0.5)', fontFamily: 'monospace', fontSize: '0.65rem', textTransform: 'uppercase', letterSpacing: 1 }}>
        Chain of Thought
      </Typography>
      {thinking === '...' ? (
        <Typography
          sx={{
            fontSize: '0.8rem',
            color: 'rgba(255, 255, 255, 0.5)',
            fontFamily: 'monospace',
          }}
        >
          <LoadingDots />
        </Typography>
      ) : thinking.includes('\n\n---\n\n') ? (
        thinking.split('\n\n---\n\n').map((step, stepIdx, steps) => (
          <Box key={stepIdx}>
            <Typography
              variant="caption"
              sx={{ display: 'block', mb: 0.5, fontWeight: 'medium', color: 'rgba(0, 229, 255, 0.4)', fontFamily: 'monospace', fontSize: '0.65rem' }}
            >
              Step {stepIdx + 1}
            </Typography>
            <Typography
              sx={{
                fontSize: '0.8rem',
                color: 'rgba(255, 255, 255, 0.55)',
                whiteSpace: 'pre-wrap',
                wordWrap: 'break-word',
                overflowWrap: 'break-word',
                wordBreak: 'break-word',
                fontFamily: 'monospace',
                lineHeight: 1.6,
                maxWidth: '100%',
              }}
            >
              {step}
            </Typography>
            {stepIdx < steps.length - 1 && (
              <Box
                sx={{
                  my: 1.5,
                  borderBottom: '1px dashed',
                  borderColor: 'info.main',
                  opacity: 0.3,
                }}
              />
            )}
          </Box>
        ))
      ) : (
        <Typography
          sx={{
            fontSize: '0.8rem',
            color: 'rgba(255, 255, 255, 0.55)',
            whiteSpace: 'pre-wrap',
            wordWrap: 'break-word',
            overflowWrap: 'break-word',
            wordBreak: 'break-word',
            fontFamily: 'monospace',
            lineHeight: 1.6,
            maxWidth: '100%',
          }}
        >
          {thinking}
        </Typography>
      )}
    </Box>
  );
}
