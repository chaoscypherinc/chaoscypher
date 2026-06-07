// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * OllamaContextMenu: 3-dot context menu for installed Ollama models.
 *
 * Provides "Model Info" and "Remove" actions for a given model,
 * rendered as a positioned MUI Menu anchored to the triggering button.
 */

import React from 'react';
import {
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
} from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import DeleteOutlinedIcon from '@mui/icons-material/DeleteOutlined';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface MenuPosition {
  modelId: string;
  top: number;
  left: number;
}

interface OllamaContextMenuProps {
  /** Current menu position/state, or null if closed. */
  menuState: MenuPosition | null;
  /** Callback to close the menu. */
  onClose: () => void;
  /** Callback when "Model Info" is clicked. Receives the model ID. */
  onShowInfo: (modelId: string) => void;
  /** Callback when "Remove" is clicked. Receives the model ID. */
  onRemove: (modelId: string) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export const OllamaContextMenu = React.memo(function OllamaContextMenu({
  menuState,
  onClose,
  onShowInfo,
  onRemove,
}: OllamaContextMenuProps) {
  return (
    <Menu
      open={Boolean(menuState)}
      onClose={onClose}
      anchorReference="anchorPosition"
      anchorPosition={menuState ? { top: menuState.top, left: menuState.left } : undefined}
      transformOrigin={{ vertical: 'top', horizontal: 'right' }}
    >
      <MenuItem onClick={() => { if (menuState) onShowInfo(menuState.modelId); onClose(); }}>
        <ListItemIcon><InfoOutlinedIcon fontSize="small" /></ListItemIcon>
        <ListItemText>Model Info</ListItemText>
      </MenuItem>
      <MenuItem onClick={() => { if (menuState) onRemove(menuState.modelId); onClose(); }}>
        <ListItemIcon><DeleteOutlinedIcon fontSize="small" color="error" /></ListItemIcon>
        <ListItemText sx={{ color: 'error.main' }}>Remove</ListItemText>
      </MenuItem>
    </Menu>
  );
});
