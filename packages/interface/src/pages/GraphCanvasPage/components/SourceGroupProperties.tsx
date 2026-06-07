// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * SourceGroupProperties: Properties display for source group virtual nodes.
 *
 * Shows the source image title, entity count, and action buttons for
 * expanding/collapsing the group and navigating to the source document.
 */

import React from 'react';
import { Box, Typography, Chip, Divider, Button } from '@mui/material';
import ImageIcon from '@mui/icons-material/ImageOutlined';
import OpenInNewIcon from '@mui/icons-material/OpenInNewOutlined';
import UnfoldMoreIcon from '@mui/icons-material/UnfoldMoreOutlined';
import UnfoldLessIcon from '@mui/icons-material/UnfoldLessOutlined';
import type { GraphNodeData } from '../types';
import { SOURCE_GROUP_PREFIX } from '../types';

interface SourceGroupPropertiesProps {
  /** The source group virtual node ID. */
  nodeId: string;
  /** The source group node data. */
  nodeData: GraphNodeData;
  /** Toggle expand/collapse for the source group. */
  onToggleSourceGroup?: (sourceId: string) => void;
  /** Navigate to a route. */
  onNavigate?: (path: string) => void;
}

/**
 * Renders properties for a source group virtual node.
 */
const SourceGroupProperties: React.FC<SourceGroupPropertiesProps> = ({
  nodeId,
  nodeData,
  onToggleSourceGroup,
  onNavigate,
}) => {
  const sourceId = nodeId.slice(SOURCE_GROUP_PREFIX.length);
  const nodeAttrs = nodeData as GraphNodeData & {
    title?: string;
    sourceGroupEntityCount?: number;
    expanded?: boolean;
  };

  return (
    <>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <ImageIcon color="action" />
        <Typography variant="h6">{nodeAttrs.title || 'Source Image'}</Typography>
      </Box>
      <Chip label="Source Image" size="small" color="primary" variant="outlined" sx={{ mb: 2 }} />

      <Divider sx={{ my: 2 }} />

      <Typography variant="subtitle2" gutterBottom>Source Details</Typography>
      <Typography variant="body2" gutterBottom>
        Entities: {nodeAttrs.sourceGroupEntityCount || 0}
      </Typography>

      <Divider sx={{ my: 2 }} />

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        {onToggleSourceGroup && (
          <Button
            variant="outlined"
            size="small"
            startIcon={nodeAttrs.expanded ? <UnfoldLessIcon /> : <UnfoldMoreIcon />}
            onClick={() => onToggleSourceGroup(sourceId)}
            fullWidth
          >
            {nodeAttrs.expanded ? 'Collapse Group' : 'Expand Group'}
          </Button>
        )}
        {onNavigate && (
          <Button
            variant="outlined"
            size="small"
            startIcon={<OpenInNewIcon />}
            onClick={() => onNavigate(`/sources/${sourceId}`)}
            fullWidth
          >
            View Source Document
          </Button>
        )}
      </Box>
    </>
  );
};

export default SourceGroupProperties;
