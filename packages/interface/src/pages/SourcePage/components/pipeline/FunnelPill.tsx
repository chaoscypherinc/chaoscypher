// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { Box, Typography } from '@mui/material';
import { alpha } from '@mui/material/styles';
import { PipelineSeverityColors } from '../../../../theme/colors';

export type PillSeverity = 'ok' | 'warn' | 'err' | 'neutral';

export interface FunnelPillProps {
  count: string;
  label: string;
  sublabel: string;
  severity: PillSeverity;
  /** Per-stage identity color. Used as the ring color when severity is neutral. */
  stageColor?: string;
  selected: boolean;
  /** Optional — only fired when `interactive` is true. */
  onClick?: () => void;
  /** When false, renders a non-interactive div (no button role, not focusable). Defaults to true. */
  interactive?: boolean;
}

export function FunnelPill({
  count,
  label,
  sublabel,
  severity,
  stageColor,
  selected,
  onClick,
  interactive = true,
}: FunnelPillProps) {
  const ringColor = severity === 'neutral' ? (stageColor ?? '#4a8fc8') : PipelineSeverityColors[severity];
  return (
    <Box
      component={interactive ? 'button' : 'div'}
      role={interactive ? 'button' : undefined}
      aria-label={interactive ? `${label}: ${count} ${sublabel}` : undefined}
      data-selected={selected ? 'true' : 'false'}
      data-severity={severity}
      onClick={interactive ? onClick : undefined}
      sx={{
        all: 'unset',
        textAlign: 'center',
        flex: 1,
        cursor: interactive ? 'pointer' : 'default',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 0.75,
      }}
    >
      <Box
        sx={{
          width: 62,
          height: 62,
          borderRadius: '50%',
          background: alpha(ringColor, selected ? 0.22 : 0.12),
          border: `${selected ? 2.5 : 1.5}px solid ${ringColor}`,
          boxShadow: selected ? `0 0 0 4px ${alpha(ringColor, 0.12)}` : 'none',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontFamily: 'ui-monospace, monospace',
          fontWeight: selected ? 700 : 600,
          fontSize: '0.8rem',
          color: alpha(ringColor, 0.95),
        }}
      >
        {count}
      </Box>
      <Typography
        sx={{
          color: selected ? ringColor : '#fff',
          fontSize: '0.65rem',
          letterSpacing: 0.5,
          fontWeight: selected ? 600 : 400,
        }}
      >
        {label}
      </Typography>
      <Typography sx={{ color: '#888', fontSize: '0.58rem' }}>{sublabel}</Typography>
    </Box>
  );
}
