// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ConnectionErrorScreen — full-viewport "Connection Error" page shown
 * when the boot settings fetch fails (Cortex unreachable or returning
 * a non-2xx).
 *
 * Behaviour:
 *   1. Surfaces the error + a manual Retry button.
 *   2. Includes a diagnostic hint pointing at `docker logs cortex` —
 *      the most common cause of this screen during launch is the
 *      Cortex container not being up, and the hint shaves a support
 *      round-trip for self-hosted users.
 */

import { Box, Button, Typography } from '@mui/material';

interface ConnectionErrorScreenProps {
  /** Human-readable error message surfaced from the failed boot fetch. */
  error: string;
  /** Caller-provided retry callback (typically re-runs the settings fetch). */
  onRetry: () => void;
}

export function ConnectionErrorScreen({ error, onRetry }: ConnectionErrorScreenProps) {
  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100vh',
        gap: 2,
        bgcolor: 'background.default',
        color: 'text.primary',
        px: 3,
        textAlign: 'center',
      }}
    >
      <Typography variant="h4" component="h2">
        Connection Error
      </Typography>
      <Typography sx={{ color: 'text.secondary', maxWidth: 560 }}>
        {error}
      </Typography>
      <Typography
        variant="body2"
        sx={{
          color: 'text.disabled',
          fontFamily: 'monospace',
          fontSize: '0.8rem',
          maxWidth: 560,
        }}
      >
        Tip: check the backend with{' '}
        <Box component="span" sx={{ color: 'text.secondary' }}>
          docker logs cortex
        </Box>
        .
      </Typography>
      <Button variant="contained" onClick={onRetry}>
        Retry
      </Button>
    </Box>
  );
}
