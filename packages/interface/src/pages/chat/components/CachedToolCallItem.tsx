// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Non-expandable cached tool call row.
 *
 * Renders with a grey dot indicator and "cached" label.
 * Used for tool calls that were deduplicated by the backend.
 */

import { Box, Typography } from '@mui/material';
import { ChatTheme } from '../../../theme/chatTheme';

interface CachedToolCallItemProps {
  /** Display name of the tool function. */
  toolName: string;
  /** Pre-formatted compact argument string. */
  compactArgs: string;
  /** Whether this is the last item (controls bottom border). */
  isLast: boolean;
}

export default function CachedToolCallItem({ toolName, compactArgs, isLast }: CachedToolCallItemProps) {
  return (
    <Box sx={{
      py: 0.5,
      ...(!isLast && { borderBottom: `1px solid ${ChatTheme.tools.cachedBorder}` }),
    }}>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 0.75,
          px: 0.5,
        }}
      >
        {/* Grey dot indicator */}
        <Box sx={{
          width: 6, height: 6, borderRadius: '50%',
          border: '1.5px solid',
          borderColor: 'text.disabled',
          backgroundColor: 'transparent',
          flexShrink: 0,
        }} />
        <Typography
          variant="caption"
          sx={{ fontFamily: 'monospace', fontWeight: 600, color: 'text.secondary', whiteSpace: 'nowrap' }}
        >
          {toolName}
        </Typography>
        <Typography
          variant="caption"
          sx={{ fontFamily: 'monospace', color: 'text.disabled', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
        >
          {compactArgs}
        </Typography>
        <Typography variant="caption" sx={{ fontStyle: 'italic', color: 'text.disabled', ml: 'auto', whiteSpace: 'nowrap' }}>
          cached
        </Typography>
      </Box>
    </Box>
  );
}
