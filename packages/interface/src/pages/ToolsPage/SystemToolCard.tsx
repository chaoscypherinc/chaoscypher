// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import React from 'react';
import { Box, Typography, Button, Chip } from '@mui/material';
import CodeIcon from '@mui/icons-material/Code';
import { getMuiIcon } from '../../utils/icons';
import { getCardStyle } from '../../theme/cardStyles';
import { StatusColors } from '../../theme/colors';
import { ChaosCypherPalette } from '../../theme/palette';

const CYAN = ChaosCypherPalette.primary;

/** Summary data for a built-in system tool. */
export interface SystemToolSummary {
  id: string;
  category: string;
  icon: string | null;
  name: string;
  description: string;
  version: string;
  is_active: boolean;
}

/** Props for a single SystemToolCard. */
interface SystemToolCardProps {
  tool: SystemToolSummary;
  onViewSchema: (toolId: string) => void;
}

/** Card displaying a system tool's name, description, category, and schema action. */
export default function SystemToolCard({ tool, onViewSchema }: SystemToolCardProps) {
  return (
    <Box sx={{ flex: '1 1 calc(33.333% - 11px)', minWidth: 300 }}>
      <Box sx={{ ...getCardStyle(StatusColors.neutral, false), p: 2.5, display: 'flex', flexDirection: 'column', alignItems: 'stretch', height: '100%', minHeight: 220 }}>
        <Box sx={{ flexGrow: 1, mb: 2 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2, gap: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexGrow: 1 }}>
              {React.createElement(getMuiIcon(tool.icon), { fontSize: 'small', sx: { color: 'text.secondary' } })}
              <Typography variant="h6" component="div" sx={{ fontWeight: 600 }}>
                {tool.name}
              </Typography>
            </Box>
            <Chip label={tool.category} size="small" variant="outlined" sx={{ flexShrink: 0, borderColor: 'rgba(255,255,255,0.15)', color: 'text.secondary' }} />
          </Box>
          <Typography
            variant="body2"
            sx={{
              color: "text.secondary",
              mb: 2,
              lineHeight: 1.5
            }}>
            {tool.description}
          </Typography>
          <Typography
            variant="caption"
            sx={{
              color: "text.secondary",
              display: 'block'
            }}>
            ID: {tool.id} &middot; v{tool.version}
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', gap: 1, pt: 2, borderTop: 1, borderColor: 'rgba(255,255,255,0.06)' }}>
          <Button
            size="small"
            startIcon={<CodeIcon />}
            onClick={() => onViewSchema(tool.id)}
            sx={{ color: CYAN, '&:hover': { bgcolor: 'rgba(0, 229, 255, 0.08)' } }}
          >
            View Schema
          </Button>
        </Box>
      </Box>
    </Box>
  );
}
