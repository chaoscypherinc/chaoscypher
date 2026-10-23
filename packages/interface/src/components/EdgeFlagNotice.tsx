// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * EdgeFlagNotice — every pipeline flag an edge carries ("Capped",
 * "Unevidenced"), each as a chip with its explanation, and the reason the
 * pipeline gave when it gave one.
 *
 * Reads the flags through the shared `edgeFlags` helper, so a new flag in
 * `utils/edgeFlags.ts` shows up here with no change. Renders nothing for an
 * unflagged edge.
 */

import { Box, Chip, Tooltip, Typography } from '@mui/material';
import { edgeFlags } from '../utils/edgeFlags';

interface EdgeFlagNoticeProps {
  /** The edge's ``properties`` bag. */
  properties: unknown;
}

export function EdgeFlagNotice({ properties }: EdgeFlagNoticeProps) {
  const carried = edgeFlags(properties);
  if (carried.length === 0) return null;

  return (
    <Box sx={{ mb: 2, display: 'flex', flexDirection: 'column', gap: 0.75 }}>
      {carried.map(({ flag, reason }) => (
        <Box key={flag.key}>
          <Tooltip title={flag.description} arrow>
            <Chip
              label={flag.label}
              size="small"
              color="warning"
              variant="outlined"
              sx={{ height: 20, fontSize: '0.7rem' }}
            />
          </Tooltip>
          {reason && (
            <Typography
              variant="caption"
              component="p"
              sx={{ color: 'text.secondary', mt: 0.5 }}
            >
              {reason}
            </Typography>
          )}
        </Box>
      ))}
    </Box>
  );
}
