// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Alert, Button, Box, Typography } from '@mui/material';
import PauseCircleIcon from '@mui/icons-material/PauseCircle';
import type { SystemPauseStatus } from '../hooks/useSystemPauseStatus';

interface Props {
  status: SystemPauseStatus;
  onResume: () => void;
}

/**
 * Full-width warning banner shown below the AppBar when system-wide
 * processing is paused. Renders nothing when the system is running
 * normally.
 */
export function SystemPauseBanner({ status, onResume }: Props) {
  if (!status.paused) {
    return null;
  }

  const pausedAtDisplay = status.paused_at
    ? new Date(status.paused_at).toLocaleString()
    : null;

  const isAutoPaused = status.reason?.startsWith('Auto-paused:') ?? false;
  const needsManualResume = isAutoPaused && status.reason?.includes('disk_space');

  return (
    <Alert
      severity="warning"
      icon={<PauseCircleIcon />}
      action={
        <Button color="inherit" size="small" onClick={onResume}>
          Resume
        </Button>
      }
      sx={{ borderRadius: 0, flexShrink: 0, mb: 2 }}
    >
      <Box>
        <strong>Source processing is paused.</strong>
        {status.reason && <> Reason: {status.reason}.</>}
        {pausedAtDisplay && <> Since {pausedAtDisplay}.</>}
        {isAutoPaused && (
          <Typography variant="body2" sx={{ mt: 0.5, opacity: 0.85 }}>
            {needsManualResume
              ? 'Manual resume required \u2014 free up disk space first.'
              : 'Will auto-resume when the issue resolves.'}
          </Typography>
        )}
      </Box>
    </Alert>
  );
}
