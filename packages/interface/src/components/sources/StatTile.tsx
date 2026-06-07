// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Tooltip, Typography } from '@mui/material';
import { alpha } from '@mui/material/styles';
import type { ReactNode } from 'react';

interface StatTileProps {
  value: string | number;
  label: string;
  color: string;
  icon: ReactNode;
  subValue?: string;
  tooltip?: ReactNode;
  onClick?: () => void;
  /** Required when onClick is set. */
  ariaLabel?: string;
}

/**
 * Glass-panel headline stat tile (icon + value + label). Shared across the
 * Overview headline row and the Extraction tab counts. Becomes a real,
 * keyboard-focusable button when `onClick` is provided.
 */
export function StatTile({ value, label, color, icon, subValue, tooltip, onClick, ariaLabel }: StatTileProps) {
  const interactive = !!onClick;
  const content = (
    <Box
      component={interactive ? 'button' : 'div'}
      type={interactive ? 'button' : undefined}
      aria-label={interactive ? ariaLabel : undefined}
      onClick={onClick}
      sx={{
        ...(interactive ? { all: 'unset' } : {}),
        boxSizing: 'border-box',
        flex: 1,
        minWidth: 112,
        py: 1.75,
        px: 1.25,
        textAlign: 'center',
        background: 'rgba(5, 5, 10, 0.25)',
        backdropFilter: 'blur(12px)',
        WebkitBackdropFilter: 'blur(12px)',
        border: `1px solid ${alpha(color, 0.15)}`,
        borderRadius: 1.5,
        overflow: 'hidden',
        cursor: interactive ? 'pointer' : 'default',
        transition: interactive ? 'border-color 0.15s, transform 0.15s' : undefined,
        '&:hover': interactive ? { borderColor: alpha(color, 0.4), transform: 'translateY(-1px)' } : undefined,
        '&:focus-visible': interactive ? { outline: `2px solid ${alpha(color, 0.6)}`, outlineOffset: 2 } : undefined,
      }}
    >
      <Typography
        sx={{
          fontSize: { xs: '1.4rem', sm: '1.6rem', md: '1.75rem' },
          fontWeight: 600,
          lineHeight: 1.15,
          color,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
      >
        {value}
      </Typography>
      <Box sx={{ mt: 0.5, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 0.5, minWidth: 0 }}>
        <Box sx={{ color, display: 'flex', fontSize: 16, flexShrink: 0 }}>{icon}</Box>
        <Typography
          variant="body2"
          sx={{ color: 'text.secondary', lineHeight: 1.25, whiteSpace: 'normal', overflowWrap: 'anywhere' }}
        >
          {label}
        </Typography>
      </Box>
      {subValue && (
        <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.65rem', display: 'block', mt: 0.25 }}>
          {subValue}
        </Typography>
      )}
    </Box>
  );
  return tooltip ? <Tooltip title={tooltip} arrow>{content}</Tooltip> : content;
}
