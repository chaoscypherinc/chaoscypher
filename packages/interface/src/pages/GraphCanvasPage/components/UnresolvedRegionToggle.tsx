// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * UnresolvedRegionToggle — canvas overlay button that collapses the
 * "Unresolved" region into its summary node and expands it back.
 * Hidden when no node carries a pipeline flag.
 */

import { Button, Tooltip } from '@mui/material';
import UnfoldLessIcon from '@mui/icons-material/UnfoldLess';
import UnfoldMoreIcon from '@mui/icons-material/UnfoldMore';
import { unresolvedSummaryLabel } from '../utils/unresolvedRegion';
import { canvasOverlaySx } from '../../../theme/cardStyles';

interface UnresolvedRegionToggleProps {
  count: number;
  expanded: boolean;
  onToggle: () => void;
}

export function UnresolvedRegionToggle({ count, expanded, onToggle }: UnresolvedRegionToggleProps) {
  if (count === 0) return null;

  const tooltip = expanded
    ? 'Collapse the Unresolved region into one summary node'
    : 'Expand the Unresolved region';

  return (
    <Tooltip title={tooltip} placement="bottom-start">
      <Button
        size="small"
        color="warning"
        aria-expanded={expanded}
        onClick={onToggle}
        startIcon={expanded ? <UnfoldLessIcon /> : <UnfoldMoreIcon />}
        sx={{
          alignSelf: 'flex-start',
          textTransform: 'none',
          ...canvasOverlaySx,
          px: 1.25,
        }}
      >
        {unresolvedSummaryLabel(count)}
      </Button>
    </Tooltip>
  );
}
