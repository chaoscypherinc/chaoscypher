// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * NodeFlagChips — one small chip per pipeline flag a node carries
 * ("Unconnected", "Reference", ...), each with a tooltip explaining it.
 *
 * Reads the flags through the shared `nodeFlags` helper, so a new flag in
 * `utils/nodeFlags.ts` shows up here with no change. Renders nothing for an
 * unflagged node.
 */

import { Box, Chip, Tooltip } from '@mui/material';
import { nodeFlags } from '../utils/nodeFlags';

interface NodeFlagChipsProps {
  /** The node's properties bag (``Node.properties``). */
  properties: unknown;
}

export function NodeFlagChips({ properties }: NodeFlagChipsProps) {
  const flags = nodeFlags(properties);
  if (flags.length === 0) return null;

  return (
    <Box
      component="span"
      sx={{ display: 'inline-flex', flexWrap: 'wrap', gap: 0.5, verticalAlign: 'middle' }}
    >
      {flags.map((flag) => (
        <Tooltip key={flag.key} title={flag.description} arrow>
          <Chip
            label={flag.label}
            size="small"
            color="warning"
            variant="outlined"
            sx={{ height: 20, fontSize: '0.7rem' }}
          />
        </Tooltip>
      ))}
    </Box>
  );
}
