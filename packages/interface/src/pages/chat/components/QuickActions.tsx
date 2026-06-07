// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Quick action buttons for confirmation prompts.
 *
 * Displays "Approve" and "Decline" ghost-styled buttons when the AI
 * asks for permission or proposes an action.
 */

import { Box, Button } from '@mui/material';
import ApproveIcon from '@mui/icons-material/Check';
import DeclineIcon from '@mui/icons-material/Close';
import { ghostButtonSx } from '../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../theme/palette';

interface QuickActionsProps {
  /** Callback invoked with the quick action response text. */
  onQuickAction: (response: string) => void;
}

export default function QuickActions({ onQuickAction }: QuickActionsProps) {
  return (
    <Box sx={{ mt: 2, display: 'flex', gap: 1, flexWrap: 'wrap' }}>
      <Button
        size="small"
        variant="outlined"
        startIcon={<ApproveIcon />}
        onClick={() => onQuickAction('Yes, please proceed.')}
        sx={{ ...ghostButtonSx(ChaosCypherPalette.success), textTransform: 'none' }}
      >
        Approve
      </Button>
      <Button
        size="small"
        variant="outlined"
        startIcon={<DeclineIcon />}
        onClick={() => onQuickAction('No, please stop.')}
        sx={{ ...ghostButtonSx(ChaosCypherPalette.error), textTransform: 'none' }}
      >
        Decline
      </Button>
    </Box>
  );
}
