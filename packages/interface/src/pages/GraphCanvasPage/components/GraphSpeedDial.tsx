// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * GraphSpeedDial: Floating action button with quick-action items.
 *
 * Renders the bottom-left SpeedDial for creating nodes, links, and
 * navigating to entity/relationship list views.
 */

import React, { useState } from 'react';
import { SpeedDial, SpeedDialAction, SpeedDialIcon } from '@mui/material';
import AddIcon from '@mui/icons-material/AddOutlined';
import LinkIcon from '@mui/icons-material/LinkOutlined';
import FormatListBulletedIcon from '@mui/icons-material/FormatListBulletedOutlined';

interface GraphSpeedDialProps {
  /** Open the template selection modal to create a new node. */
  onCreateItem: () => void;
  /** Open the edge creation modal. */
  onCreateLink: () => void;
  /** Navigate to the entities list page. */
  onViewEntities: () => void;
  /** Navigate to the relationships list page. */
  onViewRelationships: () => void;
}

/** Floating quick-action SpeedDial for the graph canvas. */
export const GraphSpeedDial: React.FC<GraphSpeedDialProps> = ({
  onCreateItem,
  onCreateLink,
  onViewEntities,
  onViewRelationships,
}) => {
  const [open, setOpen] = useState(false);

  return (
    <SpeedDial
      ariaLabel="Quick Actions"
      FabProps={{ size: 'small' }}
      sx={{
        position: 'absolute', bottom: 24, left: 24, zIndex: 10,
        '& .MuiSpeedDial-fab': {
          bgcolor: 'transparent',
          border: '1px solid',
          borderColor: 'primary.main',
          color: 'primary.main',
          boxShadow: 'none',
          '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.1)' },
        },
        '& .MuiSpeedDialAction-fab': {
          bgcolor: 'transparent',
          border: '1px solid rgba(255, 255, 255, 0.15)',
          color: 'text.primary',
          boxShadow: 'none',
          '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.08)', borderColor: 'primary.main', color: 'primary.main' },
        },
      }}
      icon={<SpeedDialIcon />}
      onClose={() => setOpen(false)}
      onOpen={() => setOpen(true)}
      open={open}
    >
      <SpeedDialAction
        key="create-item"
        icon={<AddIcon />}
        title="Create Item"
        onClick={() => { onCreateItem(); setOpen(false); }}
      />
      <SpeedDialAction
        key="create-link"
        icon={<LinkIcon />}
        title="Create Link"
        onClick={() => { onCreateLink(); setOpen(false); }}
      />
      <SpeedDialAction
        key="view-entities"
        icon={<FormatListBulletedIcon />}
        title="View Entities"
        onClick={() => { onViewEntities(); setOpen(false); }}
      />
      <SpeedDialAction
        key="view-relationships"
        icon={<LinkIcon sx={{ transform: 'rotate(45deg)' }} />}
        title="View Relationships"
        onClick={() => { onViewRelationships(); setOpen(false); }}
      />
    </SpeedDial>
  );
};
