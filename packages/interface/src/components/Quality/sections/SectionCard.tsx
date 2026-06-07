// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * SectionCard - Reusable wrapper for quality score sections.
 *
 * Provides a color-coded bordered card with title, optional header value,
 * tooltip explanation, and customizable content area.
 */

import type { ReactNode } from 'react';
import { Box, Typography, Tooltip, alpha } from '@mui/material';
import HelpIcon from '@mui/icons-material/HelpOutlined';

interface SectionCardProps {
  /** Section title displayed in the header */
  title: string;
  /** Icon displayed next to the title */
  icon: ReactNode;
  /** Primary color for border and accents */
  color: string;
  /** Optional value displayed in the header (e.g., "75.2/100") */
  headerValue?: string;
  /** Optional weight badge (e.g., "Weight: 60%") */
  weightLabel?: string;
  /** Tooltip content explaining this section */
  tooltip: ReactNode;
  /** Section content */
  children: ReactNode;
}

/**
 * A color-coded section card for displaying quality metrics.
 *
 * Used in the expanded QualityScoreCard to show distinct calculation sections
 * with visual separation and detailed explanations.
 */
export function SectionCard({
  title,
  icon,
  color,
  headerValue,
  weightLabel,
  tooltip,
  children,
}: SectionCardProps) {
  return (
    <Box
      sx={{
        border: 2,
        borderColor: alpha(color, 0.4),
        borderRadius: 1.5,
        overflow: 'hidden',
        transition: 'all 0.3s ease',
        '&:hover': {
          borderColor: alpha(color, 0.6),
        },
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          px: 1.5,
          py: 1,
          bgcolor: alpha(color, 0.08),
          borderBottom: 1,
          borderColor: alpha(color, 0.2),
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Box sx={{ color, display: 'flex', alignItems: 'center' }}>
            {icon}
          </Box>
          <Typography
            variant="subtitle2"
            sx={{
              fontWeight: 600,
              color: 'text.primary'
            }}>
            {title}
          </Typography>
          <Tooltip
            title={tooltip}
            arrow
            placement="top"
            slotProps={{
              tooltip: {
                sx: {
                  maxWidth: 320,
                  p: 1.5,
                  '& .MuiTypography-root': {
                    fontSize: '0.75rem',
                  },
                },
              },
            }}
          >
            <HelpIcon
              sx={{
                fontSize: 16,
                color: 'text.secondary',
                cursor: 'help',
                '&:hover': { color: 'text.primary' },
              }}
            />
          </Tooltip>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          {weightLabel && (
            <Typography
              variant="caption"
              sx={{
                color: 'text.secondary',
                bgcolor: 'action.hover',
                px: 0.75,
                py: 0.25,
                borderRadius: 0.5,
              }}
            >
              {weightLabel}
            </Typography>
          )}
          {headerValue && (
            <Typography
              variant="subtitle2"
              sx={{
                fontWeight: 700,
                color
              }}>
              {headerValue}
            </Typography>
          )}
        </Box>
      </Box>
      {/* Content */}
      <Box sx={{ p: 1.5 }}>
        {children}
      </Box>
    </Box>
  );
}
