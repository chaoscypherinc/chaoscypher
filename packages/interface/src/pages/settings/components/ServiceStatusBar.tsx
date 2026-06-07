// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Typography } from '@mui/material';
import CircleIcon from '@mui/icons-material/Circle';
import { ChaosCypherPalette } from '../../../theme/palette';
import { SERVICE_COLORS } from './logColors';

/** Map supervisor state to indicator color. */
function getStateColor(state: string): string {
  if (state === 'RUNNING') return ChaosCypherPalette.success;
  if (state === 'STOPPED') return 'rgba(255,255,255,0.4)';
  return ChaosCypherPalette.error;
}

/** Format uptime seconds as "Xh Ym". */
function formatUptime(seconds: number | null): string {
  if (seconds === null) return '';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

interface ServiceStatusBarProps {
  services: Array<{
    name: string;
    state: string;
    pid: number | null;
    uptime_seconds: number | null;
  }>;
}

/** Service process status indicators shown below the log pane. */
export function ServiceStatusBar({ services }: ServiceStatusBarProps) {
  return (
    <Box
      sx={{
        display: 'flex',
        gap: 2,
        p: 1.25,
        background: 'rgba(255,255,255,0.02)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderTop: 'none',
        borderRadius: '0 0 4px 4px',
        fontFamily: 'sans-serif',
        fontSize: '10px',
        flexWrap: 'wrap',
      }}
    >
      {services.map((svc, i) => (
        <Box key={svc.name} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          {i > 0 && (
            <Box
              sx={{
                width: '1px',
                height: 12,
                background: 'rgba(255,255,255,0.08)',
                mr: 1,
              }}
            />
          )}
          <Typography
            variant="caption"
            sx={{
              color: SERVICE_COLORS[svc.name] || 'rgba(255,255,255,0.5)',
              fontWeight: 600,
              fontSize: '10px',
            }}
          >
            {svc.name}
          </Typography>
          <CircleIcon sx={{ fontSize: 6, color: getStateColor(svc.state) }} />
          <Typography
            variant="caption"
            sx={{ color: 'rgba(255,255,255,0.35)', fontSize: '10px' }}
          >
            {svc.state === 'RUNNING' ? 'Running' : svc.state}
          </Typography>
          {svc.pid && (
            <Typography
              variant="caption"
              sx={{ color: 'rgba(255,255,255,0.25)', fontSize: '10px' }}
            >
              PID {svc.pid} · {formatUptime(svc.uptime_seconds)}
            </Typography>
          )}
        </Box>
      ))}
    </Box>
  );
}
