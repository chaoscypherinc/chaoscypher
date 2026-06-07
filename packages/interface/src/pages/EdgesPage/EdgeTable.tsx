// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Table view for listing edge (relationship) rows with sorting and pagination.
 *
 * Renders the full table including header sort controls, row selection
 * checkboxes, template-colored relationship chips, and action buttons.
 */

import { alpha } from '@mui/material/styles';
import {
  Box,
  Typography,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  IconButton,
  Chip,
  Checkbox,
  Tooltip,
  TableSortLabel,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import TemplateIcon from '../../components/TemplateIcon';
import { InfoTooltip } from '../../components/InfoTooltip';
import GhostPagination from '../../components/GhostPagination';
import { getColorForTemplate } from '../../utils/colorUtils';
import type { ChangeEvent } from 'react';
import type { Edge, Template } from '../../types';

type EdgeSortField = 'label' | 'source' | 'target';

interface EdgeTableProps {
  edges: Edge[];
  allEdges: Edge[];
  templates: Template[];
  selectedEdges: Set<string>;
  sortField: EdgeSortField;
  sortDirection: 'asc' | 'desc';
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  getNodeLabel: (nodeId: string) => string;
  getNodeTemplate: (nodeId: string) => { template: Template | undefined; fallbackId: string };
  onToggleSort: (field: EdgeSortField) => void;
  onSelectAll: (event: ChangeEvent<HTMLInputElement>) => void;
  onSelectEdge: (id: string) => void;
  onEdit: (edge: Edge) => void;
  onDelete: (id: string) => void;
  onNavigate: (edgeId: string) => void;
  onPageChange: (page: number) => void;
}

/** Paginated table of relationship rows with sorting and bulk selection. */
export function EdgeTable({
  edges,
  allEdges,
  templates,
  selectedEdges,
  sortField,
  sortDirection,
  page,
  totalPages,
  total,
  pageSize,
  getNodeLabel,
  getNodeTemplate,
  onToggleSort,
  onSelectAll,
  onSelectEdge,
  onEdit,
  onDelete,
  onNavigate,
  onPageChange,
}: EdgeTableProps) {
  return (
    <>
      <TableContainer component={Paper}>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell padding="checkbox">
                <Checkbox
                  indeterminate={selectedEdges.size > 0 && selectedEdges.size < allEdges.length}
                  checked={allEdges.length > 0 && selectedEdges.size === allEdges.length}
                  onChange={onSelectAll}
                />
              </TableCell>
              <TableCell>
                <TableSortLabel
                  active={sortField === 'source'}
                  direction={sortField === 'source' ? sortDirection : 'asc'}
                  onClick={() => onToggleSort('source')}
                >
                  Source
                </TableSortLabel>
              </TableCell>
              <TableCell>
                <TableSortLabel
                  active={sortField === 'label'}
                  direction={sortField === 'label' ? sortDirection : 'asc'}
                  onClick={() => onToggleSort('label')}
                >
                  Relationship
                </TableSortLabel>
              </TableCell>
              <TableCell>
                <TableSortLabel
                  active={sortField === 'target'}
                  direction={sortField === 'target' ? sortDirection : 'asc'}
                  onClick={() => onToggleSort('target')}
                >
                  Target
                </TableSortLabel>
              </TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {edges.map((edge) => (
              <TableRow
                key={edge.id}
                hover
                selected={selectedEdges.has(edge.id)}
                onClick={(e) => {
                  if ((e.target as HTMLElement).closest('button') ||
                      (e.target as HTMLElement).closest('[type="checkbox"]')) {
                    return;
                  }
                  onNavigate(edge.id);
                }}
                sx={{ cursor: 'pointer' }}
              >
                <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
                  <Checkbox
                    checked={selectedEdges.has(edge.id)}
                    onChange={() => onSelectEdge(edge.id)}
                  />
                </TableCell>
                <TableCell>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                    {(() => {
                      const { template, fallbackId } = getNodeTemplate(edge.source_node_id);
                      return (
                        <>
                          <TemplateIcon
                            template={template}
                            fallbackTemplateId={fallbackId}
                            size={16}
                            containerSize={16}
                          />
                          <Typography variant="body2" noWrap>{getNodeLabel(edge.source_node_id)}</Typography>
                        </>
                      );
                    })()}
                  </Box>
                </TableCell>
                <TableCell>
                  <Tooltip
                    title={
                      <InfoTooltip items={[
                        { label: 'ID', value: edge.id, sx: { fontFamily: 'monospace', mb: 0.5 } },
                        { label: 'Created', value: new Date(edge.created_at).toLocaleString() },
                        ...(edge.updated_at ? [{ label: 'Updated', value: new Date(edge.updated_at).toLocaleString() }] : []),
                      ]} />
                    }
                    arrow
                    placement="top"
                  >
                    {(() => {
                      const tpl = templates.find(t => t.id === edge.template_id);
                      const bgColor = tpl?.color || getColorForTemplate(edge.template_id);
                      return (
                        <Chip
                          variant="outlined"
                          label={edge.label}
                          size="small"
                          sx={{
                            bgcolor: 'transparent',
                            color: bgColor,
                            borderColor: alpha(bgColor, 0.5),
                            fontStyle: 'italic',
                          }}
                        />
                      );
                    })()}
                  </Tooltip>
                </TableCell>
                <TableCell>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                    {(() => {
                      const { template, fallbackId } = getNodeTemplate(edge.target_node_id);
                      return (
                        <>
                          <TemplateIcon
                            template={template}
                            fallbackTemplateId={fallbackId}
                            size={16}
                            containerSize={16}
                          />
                          <Typography variant="body2" noWrap>{getNodeLabel(edge.target_node_id)}</Typography>
                        </>
                      );
                    })()}
                  </Box>
                </TableCell>
                <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                  <IconButton
                    aria-label="Edit edge"
                    size="small"
                    onClick={() => onEdit(edge)}
                    sx={{ color: 'rgba(255,255,255,0.25)', '&:hover': { color: 'primary.main' }, transition: 'color 0.15s' }}
                  >
                    <EditIcon fontSize="small" />
                  </IconButton>
                  <IconButton
                    aria-label="Delete edge"
                    size="small"
                    onClick={() => onDelete(edge.id)}
                    sx={{ color: 'rgba(255,255,255,0.25)', '&:hover': { color: 'error.main' }, transition: 'color 0.15s' }}
                  >
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      <GhostPagination
        page={page}
        totalPages={totalPages}
        total={total}
        pageSize={pageSize}
        onPageChange={onPageChange}
      />
    </>
  );
}
