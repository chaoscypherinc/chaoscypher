// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import { Box, Collapse, IconButton, Typography } from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import type { ReactNode } from 'react';
import { glassPanelSx } from '../../theme/cardStyles';

interface MetadataCardProps {
  /** Card heading (default: "Metadata"). */
  title?: string;
  /** Typically an ordered list of `<MetadataRow>` children. */
  children: ReactNode;
  /**
   * When true, the card collapses to a compact `summary` and reveals its
   * children behind a chevron toggle. Defaults to false (children always
   * rendered, no toggle) for backward compatibility.
   */
  collapsible?: boolean;
  /** Compact content shown in place of the children while collapsed. */
  summary?: ReactNode;
  /** Whether a collapsible card starts expanded (default: false — collapsed). */
  defaultExpanded?: boolean;
}

/**
 * Sidebar card for displaying metadata rows. Wraps a translucent glass panel
 * with a heading and a flex column for its children.
 *
 * When `collapsible` is set, the heading gains a chevron toggle: collapsed it
 * shows only `summary`, expanded it reveals the full row list. Collapsed
 * children are unmounted (not just hidden) so they stay out of the DOM.
 */
export default function MetadataCard({
  title = 'Metadata',
  children,
  collapsible = false,
  summary,
  defaultExpanded = false,
}: MetadataCardProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  if (!collapsible) {
    return (
      <Box sx={{ ...glassPanelSx, p: 2.5 }}>
        <Typography variant="h6" gutterBottom sx={{ color: 'text.primary' }}>
          {title}
        </Typography>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0 }}>{children}</Box>
      </Box>
    );
  }

  return (
    <Box sx={{ ...glassPanelSx, p: 2.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1 }}>
        <Typography variant="h6" sx={{ color: 'text.primary' }}>
          {title}
        </Typography>
        <IconButton
          size="small"
          onClick={() => setExpanded((prev) => !prev)}
          aria-label={expanded ? `Collapse ${title}` : `Expand ${title}`}
          aria-expanded={expanded}
          sx={{ color: 'text.secondary' }}
        >
          {expanded ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
        </IconButton>
      </Box>
      {!expanded && summary != null && <Box sx={{ mt: 1 }}>{summary}</Box>}
      <Collapse in={expanded} unmountOnExit>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0, mt: 1 }}>{children}</Box>
      </Collapse>
    </Box>
  );
}
