// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * StatusIndicator: Colored dot with summary text for the system status trigger.
 *
 * Renders the clickable pill that lives in the sidebar header. Shows a pulsing
 * dot during activity, a warning dot when paused, and a steady green dot when
 * idle and healthy. The adjacent text summarises the current system state.
 */

import React from 'react';
import { Box, CircularProgress } from '@mui/material';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import { StatusColors } from '../../theme/colors';
import { ChaosCypherPalette } from '../../theme/palette';

interface StatusIndicatorProps {
  /** Whether the initial data load is still running. */
  loading: boolean;
  /** Whether the health check initial load is still running. */
  healthLoading: boolean;
  /** Whether the system is currently paused. */
  isSystemPaused: boolean;
  /** Whether the dot should animate with a pulse. */
  shouldPulse: boolean;
  /** Status text to display beside the dot. */
  statusText: string;
  /** Ref forwarded to the outer container for menu anchoring. */
  containerRef: React.Ref<HTMLDivElement>;
  /** Handler for click events (touch fallback). */
  onClick: (event: React.MouseEvent<HTMLElement>) => void;
  /** Handler for mouse enter (hover-open). */
  onMouseEnter: () => void;
  /** Handler for mouse leave (hover-close delay). */
  onMouseLeave: () => void;
}

/**
 * Clickable status pill showing a dot indicator and summary text.
 *
 * Used as the menu trigger for the MiniSystemStatus dropdown. The dot color
 * and animation reflect the overall system state at a glance.
 */
export function StatusIndicator({
  loading,
  healthLoading,
  isSystemPaused,
  shouldPulse,
  statusText,
  containerRef,
  onClick,
  onMouseEnter,
  onMouseLeave,
}: StatusIndicatorProps) {
  return (
    <Box
      ref={containerRef}
      onClick={onClick}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1,
        height: 36,
        px: 1.5,
        borderRadius: 5,
        border: '1px solid rgba(0, 229, 255, 0.15)',
        bgcolor: 'rgba(0, 229, 255, 0.03)',
        cursor: 'pointer',
        transition: 'all 0.2s ease-in-out',
        '&:hover': {
          bgcolor: 'rgba(0, 229, 255, 0.06)',
          borderColor: 'rgba(0, 229, 255, 0.25)',
        },
      }}
    >
      {/* Status Dot */}
      <Box
        sx={{
          position: 'relative',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {loading && healthLoading ? (
          <CircularProgress size={6} sx={{ color: StatusColors.neutral }} />
        ) : isSystemPaused ? (
          <Box
            sx={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              backgroundColor: StatusColors.warning,
              boxShadow: `0 0 4px ${StatusColors.warning}`,
            }}
          />
        ) : shouldPulse ? (
          <Box
            sx={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              backgroundColor: ChaosCypherPalette.success,
              boxShadow: `0 0 4px ${ChaosCypherPalette.success}`,
              animation: 'pulse 2s ease-in-out infinite',
              '@keyframes pulse': {
                '0%, 100%': { opacity: 1, transform: 'scale(1)' },
                '50%': { opacity: 0.6, transform: 'scale(1.1)' },
              },
            }}
          />
        ) : (
          <Box
            sx={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              backgroundColor: ChaosCypherPalette.success,
              boxShadow: `0 0 4px ${ChaosCypherPalette.success}`,
            }}
          />
        )}
      </Box>

      {/* Status Text */}
      <Box
        sx={{
          fontFamily: "'SF Mono', 'Cascadia Code', monospace",
          fontSize: '0.8rem',
          fontWeight: 400,
          color: 'rgba(255, 255, 255, 0.3)',
          whiteSpace: 'nowrap',
          display: { xs: 'none', md: 'block' },
        }}
      >
        {statusText}
      </Box>

      {/* Dropdown Arrow */}
      <ArrowDropDownIcon
        sx={{
          fontSize: 14,
          color: 'rgba(0, 229, 255, 0.3)',
          ml: -0.5,
          display: { xs: 'none', md: 'block' },
        }}
      />
    </Box>
  );
}
