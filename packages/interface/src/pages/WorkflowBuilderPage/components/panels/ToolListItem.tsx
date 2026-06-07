// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ToolListItem: A single draggable tool entry in the ToolPalette.
 *
 * Renders a tool with its icon, name, and description. Supports drag-and-drop
 * so users can drag tools onto the workflow canvas.
 */

import React, { DragEvent } from 'react';
import {
  ListItem,
  ListItemIcon,
  ListItemText,
  Typography,
} from '@mui/material';
import { getMuiIcon } from '../../../../utils/icons';
import type { SystemTool } from '../../types';

interface ToolListItemProps {
  /** The tool to render */
  tool: SystemTool;
  /** Border/icon color for this tool's category */
  categoryColor: string;
  /** Callback when the user starts dragging this tool */
  onDragStart: (event: DragEvent<HTMLLIElement>, tool: SystemTool) => void;
}

/**
 * Draggable list item representing a single workflow tool.
 *
 * Wrapped with React.memo to avoid unnecessary re-renders when sibling
 * items in the same category list change.
 */
export const ToolListItem: React.FC<ToolListItemProps> = React.memo(
  function ToolListItem({ tool, categoryColor, onDragStart }) {
    return (
      <ListItem
        draggable
        onDragStart={(e) => onDragStart(e, tool)}
        sx={{
          cursor: 'grab',
          borderLeft: `3px solid ${categoryColor}`,
          '&:hover': {
            bgcolor: 'rgba(0, 229, 255, 0.04)',
          },
          '&:active': {
            cursor: 'grabbing',
          },
        }}
      >
        <ListItemIcon sx={{ minWidth: 32 }}>
          {React.createElement(getMuiIcon(tool.icon), {
            fontSize: 'small',
            sx: { color: categoryColor },
          })}
        </ListItemIcon>
        <ListItemText
          primary={
            <Typography variant="body2" sx={{ fontWeight: 500 }}>
              {tool.name}
            </Typography>
          }
          secondary={
            <Typography
              variant="caption"
              sx={{
                color: 'text.secondary',
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden',
              }}
            >
              {tool.description}
            </Typography>
          }
        />
      </ListItem>
    );
  },
);
