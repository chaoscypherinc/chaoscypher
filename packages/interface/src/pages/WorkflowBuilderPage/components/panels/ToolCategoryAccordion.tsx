// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ToolCategoryAccordion: Expandable section for a single tool category.
 *
 * Renders an accordion with the category header (icon, name, count chip)
 * and a list of ToolListItem entries for the tools in that category.
 */

import React, { DragEvent } from 'react';
import {
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Box,
  Typography,
  Chip,
  List,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import type { SystemTool } from '../../types';
import { ToolListItem } from './ToolListItem';

interface ToolCategoryAccordionProps {
  /** Category key (e.g. "ai", "graph") */
  category: string;
  /** Display name for the category */
  categoryName: string;
  /** MUI icon component for the category header */
  CategoryIcon: React.ElementType;
  /** Theme color for this category */
  categoryColor: string;
  /** Tools belonging to this category */
  tools: SystemTool[];
  /** Whether this accordion is currently expanded */
  expanded: boolean;
  /** Callback when the accordion expansion state changes */
  onAccordionChange: (event: React.SyntheticEvent, isExpanded: boolean) => void;
  /** Callback when a tool drag operation starts */
  onDragStart: (event: DragEvent<HTMLLIElement>, tool: SystemTool) => void;
}

/**
 * Accordion section for a single tool category in the ToolPalette.
 *
 * Displays the category header with an icon and tool count, and renders
 * each tool as a draggable ToolListItem.
 */
export const ToolCategoryAccordion: React.FC<ToolCategoryAccordionProps> = ({
  category,
  categoryName,
  CategoryIcon,
  categoryColor,
  tools,
  expanded,
  onAccordionChange,
  onDragStart,
}) => {
  return (
    <Accordion
      key={category}
      expanded={expanded}
      onChange={onAccordionChange}
      disableGutters
      elevation={0}
      sx={{
        bgcolor: 'transparent',
        '&:before': { display: 'none' },
        borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
      }}
    >
      <AccordionSummary
        expandIcon={<ExpandMoreIcon />}
        sx={{ minHeight: 48, '& .MuiAccordionSummary-content': { my: 0 } }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <CategoryIcon sx={{ fontSize: 18, color: categoryColor }} />
          <Typography variant="body2" sx={{ fontWeight: 500 }}>
            {categoryName}
          </Typography>
          <Chip
            label={tools.length}
            size="small"
            sx={{ height: 18, fontSize: '0.7rem' }}
          />
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ p: 0 }}>
        <List dense disablePadding>
          {tools.map((tool) => (
            <ToolListItem
              key={tool.id}
              tool={tool}
              categoryColor={categoryColor}
              onDragStart={onDragStart}
            />
          ))}
        </List>
      </AccordionDetails>
    </Accordion>
  );
};
