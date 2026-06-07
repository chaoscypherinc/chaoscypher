// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ProvenanceSection: Shows which source document a graph node was extracted from.
 *
 * Displays an "Extracted from" label with a clickable link that selects
 * the corresponding source group node on the canvas.
 */

import React from 'react';
import { Box, Typography, Button } from '@mui/material';
import ImageIcon from '@mui/icons-material/ImageOutlined';
import type { SourceGroupState } from '../hooks/useSourceGroups';
import { SOURCE_GROUP_PREFIX } from '../types';

interface ProvenanceSectionProps {
  /** The source group this node belongs to. */
  sourceGroup: SourceGroupState;
  /** Callback to select a node by ID. */
  onSelectNode?: (nodeId: string) => void;
}

/**
 * Renders provenance information showing which source document a node
 * was extracted from, with a clickable link to the source group node.
 */
const ProvenanceSection: React.FC<ProvenanceSectionProps> = ({
  sourceGroup,
  onSelectNode,
}) => {
  return (
    <Box sx={{ mt: 2, p: 1.5, bgcolor: 'action.hover', borderRadius: 1 }}>
      <Typography
        variant="caption"
        gutterBottom
        sx={{
          color: "text.secondary",
          display: "block"
        }}>
        Extracted from
      </Typography>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <ImageIcon fontSize="small" color="action" />
        <Button
          size="small"
          variant="text"
          onClick={() => {
            const groupNodeId = `${SOURCE_GROUP_PREFIX}${sourceGroup.group.source_id}`;
            onSelectNode?.(groupNodeId);
          }}
          sx={{ textTransform: 'none' }}
        >
          {sourceGroup.group.title}
        </Button>
      </Box>
    </Box>
  );
};

export default ProvenanceSection;
