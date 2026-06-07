// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Sources Table Component
 *
 * Thin orchestrator that renders the MUI table structure with source
 * rows. Delegates row rendering to {@link SourceRow}, status display
 * to {@link SourceStatusCell}, and action menus to
 * {@link useSourceActionMenu}.
 */

import {
  Box,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Checkbox,
  Typography,
} from '@mui/material';
import UploadIcon from '@mui/icons-material/Upload';
import type { UnifiedSource, SourceQualityScore } from '../../types';
import { SourceRow } from './components/SourceRow';
import { useSourceActionMenu } from './components/SourceActionMenu';

interface SourcesTableProps {
  sources: UnifiedSource[];
  selectedIds: Set<string>;
  onSelectionChange: (sourceId: string) => void;
  onSelectAll: () => void;
  onRowClick: (source: UnifiedSource) => void;
  onStop: (source: UnifiedSource) => void;
  onDelete: (source: UnifiedSource) => void;
  onToggleEnabled: (source: UnifiedSource) => void;
  onChatWithSource?: (source: UnifiedSource) => void;
  onViewInGraph?: (source: UnifiedSource) => void;
  onPauseSource?: (source: UnifiedSource) => void;
  onResumeSource?: (source: UnifiedSource) => void;
  onRetrySource?: (source: UnifiedSource) => void;
  onConfirmExtraction?: (source: UnifiedSource) => void;
  qualityScores?: Map<string, SourceQualityScore>;
}

export function SourcesTable({
  sources,
  selectedIds,
  onSelectionChange,
  onSelectAll,
  onRowClick,
  onStop,
  onDelete,
  onToggleEnabled,
  onChatWithSource,
  onViewInGraph,
  onPauseSource,
  onResumeSource,
  onRetrySource,
  onConfirmExtraction,
  qualityScores,
}: SourcesTableProps) {
  // Selection state calculations
  const selectedCount = selectedIds.size;
  const allSelected = sources.length > 0 && selectedCount === sources.length;
  const someSelected = selectedCount > 0 && selectedCount < sources.length;

  // Action menu (uses useMenuState internally)
  const { openMenu, menuElement } = useSourceActionMenu({
    onRowClick,
    onStop,
    onDelete,
    onToggleEnabled,
    onChatWithSource,
    onViewInGraph,
    onPauseSource,
    onResumeSource,
    onRetrySource,
  });

  if (sources.length === 0) {
    return (
      <Box sx={{ py: 4, textAlign: 'center' }}>
        <UploadIcon sx={{ fontSize: 64, color: 'text.secondary', mb: 2 }} />
        <Typography color="textSecondary">
          No sources found. Click &quot;Add Source&quot; to upload files.
        </Typography>
        <Typography variant="caption" color="textSecondary">
          Supported formats: TXT, PDF, CSV, JSON, HTML, DOCX, TTL/RDF
        </Typography>
      </Box>
    );
  }

  return (
    <>
      <TableContainer>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell padding="checkbox" sx={{ width: 48 }}>
                <Checkbox
                  indeterminate={someSelected}
                  checked={allSelected}
                  onChange={onSelectAll}
                  sx={{
                    opacity: selectedCount > 0 ? 1 : 0,
                    '&:hover': { opacity: 1 },
                    transition: 'opacity 0.15s',
                  }}
                />
              </TableCell>
              <TableCell sx={{ width: { xs: 180, sm: 240, md: 320 } }}>Title</TableCell>
              <TableCell>Status</TableCell>
              <TableCell align="right" sx={{ width: 48 }}></TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {sources.map((source) => (
              <SourceRow
                key={source.id}
                source={source}
                isSelected={selectedIds.has(source.id)}
                anySelected={selectedCount > 0}
                onSelectionChange={onSelectionChange}
                onRowClick={onRowClick}
                onMenuOpen={openMenu}
                qualityScores={qualityScores}
                onConfirmExtraction={onConfirmExtraction}
              />
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      {menuElement}
    </>
  );
}
