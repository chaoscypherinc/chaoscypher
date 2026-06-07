// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Typography, Chip, IconButton, Tooltip } from '@mui/material';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';
import CopyIcon from '@mui/icons-material/ContentCopy';
import { getCardStyle } from '../../theme/cardStyles';
import { StatusColors } from '../../theme/colors';
import type { UserTool } from '../../services/api/tools';

export type { UserTool };

/** Props for a single UserToolCard. */
interface UserToolCardProps {
  tool: UserTool;
  onEdit: (tool: UserTool) => void;
  onDuplicate: (toolId: string) => void;
  onDelete: (toolId: string) => void;
}

/** Card displaying a user tool's name, description, tags, and edit/duplicate/delete actions. */
export default function UserToolCard({ tool, onEdit, onDuplicate, onDelete }: UserToolCardProps) {
  return (
    <Box sx={{ flex: '1 1 calc(33.333% - 11px)', minWidth: 300 }}>
      <Box sx={{ ...getCardStyle(StatusColors.neutral, false), p: 2.5, display: 'flex', flexDirection: 'column', alignItems: 'stretch', height: '100%', minHeight: 220 }}>
        <Box sx={{ flexGrow: 1, mb: 2 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2, gap: 2 }}>
            <Typography variant="h6" component="div" sx={{ fontWeight: 600, flexGrow: 1 }}>
              {tool.name}
            </Typography>
            {!tool.is_active && <Chip label="Inactive" size="small" color="warning" sx={{ flexShrink: 0 }} />}
          </Box>
          {tool.description && (
            <Typography
              variant="body2"
              sx={{
                color: "text.secondary",
                mb: 2,
                lineHeight: 1.5
              }}>
              {tool.description}
            </Typography>
          )}
          <Typography
            variant="caption"
            sx={{
              color: "text.secondary",
              display: 'block',
              mb: 1.5
            }}>
            Based on: {tool.system_tool_id}
          </Typography>
          {tool.tags && tool.tags.length > 0 && (
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
              {tool.tags.map(tag => (
                <Chip key={tag} label={tag} size="small" variant="outlined" sx={{ borderColor: 'rgba(255,255,255,0.1)', color: 'text.secondary' }} />
              ))}
            </Box>
          )}
        </Box>
        <Box sx={{ display: 'flex', gap: 0.5, pt: 2, borderTop: 1, borderColor: 'rgba(255,255,255,0.06)', justifyContent: 'flex-start' }}>
          <Tooltip title="Edit">
            <IconButton aria-label="Edit tool" size="small" onClick={() => onEdit(tool)} sx={{ '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}>
              <EditIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Duplicate">
            <IconButton aria-label="Duplicate tool" size="small" onClick={() => onDuplicate(tool.id)} sx={{ '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}>
              <CopyIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Delete">
            <IconButton aria-label="Delete tool" size="small" onClick={() => onDelete(tool.id)} sx={{ '&:hover': { bgcolor: 'rgba(255, 0, 60, 0.08)' } }}>
              <DeleteIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>
      </Box>
    </Box>
  );
}
