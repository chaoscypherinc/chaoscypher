// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Source Table Row Component
 *
 * Renders a single row in the sources table, including the selection
 * checkbox, title cell with domain icon and tag chips, status cell,
 * and the 3-dot action button.
 */

import { useState } from 'react';
import {
  Box,
  TableRow,
  TableCell,
  IconButton,
  Chip,
  Typography,
  Tooltip,
  Checkbox,
} from '@mui/material';
import InfoIcon from '@mui/icons-material/InfoOutlined';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import type { UnifiedSource, SourceQualityScore } from '../../../types';
import { getMuiIcon } from '../../../utils/icons';
import { DomainColors } from '../../../theme/colors';
import { SourceInfoTooltip, ActiveSourceTooltip } from './SourceInfoTooltip';
import { SourceStatusCell } from './SourceStatusCell';

interface SourceRowProps {
  /** The source to render. */
  source: UnifiedSource;
  /** Whether this row is selected. */
  isSelected: boolean;
  /** Whether any rows are selected (controls checkbox visibility). */
  anySelected: boolean;
  /** Callback when checkbox is toggled. */
  onSelectionChange: (sourceId: string) => void;
  /** Callback when row is clicked. */
  onRowClick: (source: UnifiedSource) => void;
  /** Callback to open the action menu for this source. */
  onMenuOpen: (event: React.MouseEvent<HTMLElement>, source: UnifiedSource) => void;
  /** Quality scores per source. */
  qualityScores?: Map<string, SourceQualityScore>;
  /** Open the confirm-domain dialog for a parked (awaiting_confirmation) source. */
  onConfirmExtraction?: (source: UnifiedSource) => void;
}

/**
 * Single row in the sources table.
 *
 * Manages its own hover state for checkbox visibility and tag expansion.
 */
export function SourceRow({
  source,
  isSelected,
  anySelected,
  onSelectionChange,
  onRowClick,
  onMenuOpen,
  qualityScores,
  onConfirmExtraction,
}: SourceRowProps) {
  const [isHovered, setIsHovered] = useState(false);
  const [tagsExpanded, setTagsExpanded] = useState(false);

  const showCheckbox = isSelected || isHovered || anySelected;

  const MAX_VISIBLE_TAGS = 3;
  const tags = source.tags ?? [];
  const visibleTags = tagsExpanded ? tags : tags.slice(0, MAX_VISIBLE_TAGS);
  const hiddenCount = tags.length - MAX_VISIBLE_TAGS;

  return (
    <TableRow
      selected={isSelected}
      sx={{
        cursor: 'pointer',
        bgcolor: isSelected ? 'rgba(0, 229, 255, 0.06)' : undefined,
      }}
      onClick={() => onRowClick(source)}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
        <Checkbox
          checked={isSelected}
          onChange={() => onSelectionChange(source.id)}
          sx={{
            opacity: showCheckbox ? 1 : 0,
            transition: 'opacity 0.15s',
          }}
        />
      </TableCell>
      <TableCell>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Tooltip
            title={
              source.stage === 'active'
                ? <ActiveSourceTooltip source={source} />
                : <SourceInfoTooltip source={source} />
            }
            arrow
            placement="bottom"
          >
            <IconButton aria-label="Show source info" size="small" sx={{ p: 0.25, opacity: 0.6, '&:hover': { opacity: 1 } }}>
              {source.extraction_domain
                ? (() => {
                    const DomainIcon = getMuiIcon(source.extraction_domain_icon);
                    return <DomainIcon sx={{ fontSize: 16, color: source.extraction_domain_auto === false ? DomainColors.manual : DomainColors.auto }} />;
                  })()
                : <InfoIcon sx={{ fontSize: 16 }} />}
            </IconButton>
          </Tooltip>
          <Typography variant="body2">{source.title}</Typography>
          {tags.length > 0 && (
            <>
              {visibleTags.map((tag) => (
                <Chip
                  key={tag.id}
                  label={tag.name}
                  size="small"
                  sx={{
                    height: 20,
                    fontSize: '0.7rem',
                    bgcolor: tag.color || 'grey.500',
                    color: 'white',
                  }}
                />
              ))}
              {hiddenCount > 0 && !tagsExpanded && (
                <Chip
                  label={`+${hiddenCount}`}
                  size="small"
                  variant="outlined"
                  onClick={(e) => {
                    e.stopPropagation();
                    setTagsExpanded(true);
                  }}
                  sx={{
                    height: 20,
                    fontSize: '0.7rem',
                    cursor: 'pointer',
                  }}
                />
              )}
            </>
          )}
        </Box>
      </TableCell>
      <TableCell>
        {/* 2026-05-11: the vector-search badge that used to sit beside
            the status cell here is now merged into the Active chip
            inside SourceStatusCell (see deriveMergedChip there). The
            cell is back to a direct child of the TableCell so the
            segmented progress bar can use the full column width. */}
        <SourceStatusCell
          source={source}
          qualityScores={qualityScores}
          onConfirmExtraction={onConfirmExtraction}
        />
      </TableCell>
      <TableCell align="right" onClick={(e) => e.stopPropagation()}>
        <Tooltip title="Actions">
          <IconButton
            aria-label="More actions"
            size="small"
            onClick={(e) => onMenuOpen(e, source)}
          >
            <MoreVertIcon />
          </IconButton>
        </Tooltip>
      </TableCell>
    </TableRow>
  );
}
