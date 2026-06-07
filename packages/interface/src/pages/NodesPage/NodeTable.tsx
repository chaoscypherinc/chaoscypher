// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Table view for listing entity (node) rows with sorting and pagination.
 *
 * Owns sort state internally via the useSort hook. Renders the full table
 * including header sort controls, row selection checkboxes, template icons,
 * stat columns, and action buttons.
 */

import { useMemo } from 'react';
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
  Checkbox,
  Tooltip,
  TableSortLabel,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import VisibilityIcon from '@mui/icons-material/Visibility';
import TemplateIcon from '../../components/TemplateIcon';
import { InfoTooltip } from '../../components/InfoTooltip';
import GhostPagination from '../../components/GhostPagination';
import { useSort } from '../../hooks/useSort';
import type { ChangeEvent } from 'react';
import type { Node, Template } from '../../types';

type NodeSortField = 'label' | 'template_id' | 'edge_count' | 'citation_count' | 'relationship_type_count' | 'properties';


/** Sort nodes by the given field and direction. */
function sortNodes(nodes: Node[], field: NodeSortField, direction: 'asc' | 'desc'): Node[] {
  return [...nodes].sort((a, b) => {
    let aVal: string | number | undefined;
    let bVal: string | number | undefined;

    switch (field) {
      case 'label':
        aVal = (a.label || '').toLowerCase();
        bVal = (b.label || '').toLowerCase();
        break;
      case 'template_id':
        aVal = a.template_id.toLowerCase();
        bVal = b.template_id.toLowerCase();
        break;
      case 'edge_count':
        aVal = a.edge_count ?? 0;
        bVal = b.edge_count ?? 0;
        break;
      case 'citation_count':
        aVal = a.citation_count ?? 0;
        bVal = b.citation_count ?? 0;
        break;
      case 'relationship_type_count':
        aVal = a.relationship_type_count ?? 0;
        bVal = b.relationship_type_count ?? 0;
        break;
      case 'properties':
        aVal = Object.keys(a.properties || {}).length;
        bVal = Object.keys(b.properties || {}).length;
        break;
      default:
        return 0;
    }

    if (typeof aVal === 'string' && typeof bVal === 'string') {
      return direction === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
    }
    if (typeof aVal === 'number' && typeof bVal === 'number') {
      return direction === 'asc' ? aVal - bVal : bVal - aVal;
    }
    return 0;
  });
}

/** Render edge count with directional tooltip. */
function formatEdgeCount(node: Node) {
  const total = node.edge_count ?? 0;
  const incoming = node.incoming_edge_count ?? 0;
  const outgoing = node.outgoing_edge_count ?? 0;
  return (
    <Tooltip title={`${incoming} incoming, ${outgoing} outgoing`}>
      <span>{total}</span>
    </Tooltip>
  );
}

interface NodeTableProps {
  nodes: Node[];
  templates: Template[];
  selectedNodes: Set<string>;
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  onSelectAll: (event: ChangeEvent<HTMLInputElement>) => void;
  onSelectNode: (id: string) => void;
  onViewDetail: (id: string) => void;
  onEdit: (node: Node) => void;
  onDelete: (id: string) => void;
  onNavigate: (path: string) => void;
  onPageChange: (page: number) => void;
}

/** Paginated table of entity rows with sorting and bulk selection. */
export function NodeTable({
  nodes,
  templates,
  selectedNodes,
  page,
  totalPages,
  total,
  pageSize,
  onSelectAll,
  onSelectNode,
  onViewDetail,
  onEdit,
  onDelete,
  onNavigate,
  onPageChange,
}: NodeTableProps) {
  const { sortField, sortDirection, toggleSort } = useSort<NodeSortField>('label');

  const sortedNodes = useMemo(
    () => sortNodes(nodes, sortField, sortDirection),
    [nodes, sortField, sortDirection],
  );

  return (
    <>
      <TableContainer component={Paper} sx={{ maxWidth: '100%', overflowX: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell padding="checkbox">
                <Checkbox
                  indeterminate={selectedNodes.size > 0 && selectedNodes.size < nodes.length}
                  checked={nodes.length > 0 && selectedNodes.size === nodes.length}
                  onChange={onSelectAll}
                />
              </TableCell>
              <TableCell>
                <TableSortLabel
                  active={sortField === 'label'}
                  direction={sortField === 'label' ? sortDirection : 'asc'}
                  onClick={() => toggleSort('label')}
                >
                  Label
                </TableSortLabel>
              </TableCell>
              <TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}>
                <TableSortLabel
                  active={sortField === 'template_id'}
                  direction={sortField === 'template_id' ? sortDirection : 'asc'}
                  onClick={() => toggleSort('template_id')}
                >
                  Template
                </TableSortLabel>
              </TableCell>
              <TableCell align="center" sx={{ display: { xs: 'none', sm: 'table-cell' } }}>
                <TableSortLabel
                  active={sortField === 'edge_count'}
                  direction={sortField === 'edge_count' ? sortDirection : 'asc'}
                  onClick={() => toggleSort('edge_count')}
                >
                  Edges
                </TableSortLabel>
              </TableCell>
              <TableCell align="center" sx={{ display: { xs: 'none', lg: 'table-cell' } }}>
                <TableSortLabel
                  active={sortField === 'relationship_type_count'}
                  direction={sortField === 'relationship_type_count' ? sortDirection : 'asc'}
                  onClick={() => toggleSort('relationship_type_count')}
                >
                  Rel Types
                </TableSortLabel>
              </TableCell>
              <TableCell align="center" sx={{ display: { xs: 'none', lg: 'table-cell' } }}>
                <TableSortLabel
                  active={sortField === 'properties'}
                  direction={sortField === 'properties' ? sortDirection : 'asc'}
                  onClick={() => toggleSort('properties')}
                >
                  Props
                </TableSortLabel>
              </TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {sortedNodes.map((node) => (
              <TableRow
                key={node.id}
                hover
                selected={selectedNodes.has(node.id)}
                onClick={(e) => {
                  if ((e.target as HTMLElement).closest('button') ||
                      (e.target as HTMLElement).closest('[type="checkbox"]')) {
                    return;
                  }
                  onNavigate(`/nodes/${node.id}`);
                }}
                sx={{ cursor: 'pointer' }}
              >
                <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
                  <Checkbox
                    checked={selectedNodes.has(node.id)}
                    onChange={() => onSelectNode(node.id)}
                  />
                </TableCell>
                <TableCell sx={{ maxWidth: { xs: 150, sm: 250, md: 'none' } }}>
                  <Tooltip
                    title={
                      <InfoTooltip items={[
                        { label: 'ID', value: node.id, sx: { fontFamily: 'monospace', mb: 0.5 } },
                        { label: 'Created', value: new Date(node.created_at).toLocaleString() },
                        { label: 'Updated', value: new Date(node.updated_at).toLocaleString() },
                        ...(node.source_id
                          ? [{ label: 'Source', value: 'Has source document', sx: { color: 'primary.main' } }]
                          : []),
                        ...(node.position
                          ? [{ label: 'Position', value: `(${node.position.x.toFixed(0)}, ${node.position.y.toFixed(0)})` }]
                          : []),
                      ]} />
                    }
                    arrow
                    placement="right"
                  >
                    <Typography
                      variant="body2"
                      noWrap
                      sx={{ textOverflow: 'ellipsis', overflow: 'hidden' }}
                    >
                      {node.label}
                    </Typography>
                  </Tooltip>
                </TableCell>
                <TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}>
                  {(() => {
                    const tpl = templates.find(t => t.id === node.template_id);
                    return (
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, minWidth: 0 }}>
                        <TemplateIcon
                          template={tpl}
                          fallbackTemplateId={node.template_id}
                          size={16}
                          containerSize={16}
                        />
                        <Typography
                          variant="body2"
                          noWrap
                          sx={{ color: 'text.primary', minWidth: 0, textOverflow: 'ellipsis', overflow: 'hidden' }}
                        >
                          {tpl?.name || node.template_id}
                        </Typography>
                      </Box>
                    );
                  })()}
                </TableCell>
                <TableCell align="center" sx={{ display: { xs: 'none', sm: 'table-cell' } }}>
                  {formatEdgeCount(node)}
                </TableCell>
                <TableCell align="center" sx={{ display: { xs: 'none', lg: 'table-cell' } }}>
                  {node.relationship_type_count ?? 0}
                </TableCell>
                <TableCell align="center" sx={{ display: { xs: 'none', lg: 'table-cell' } }}>
                  {Object.keys(node.properties || {}).length}
                </TableCell>
                <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                  <IconButton
                    aria-label="View node details"
                    size="small"
                    onClick={() => onViewDetail(node.id)}
                    sx={{ color: 'rgba(255,255,255,0.25)', '&:hover': { color: 'primary.main' }, transition: 'color 0.15s' }}
                  >
                    <VisibilityIcon />
                  </IconButton>
                  <IconButton
                    aria-label="Edit node"
                    size="small"
                    onClick={() => onEdit(node)}
                    sx={{ color: 'rgba(255,255,255,0.25)', '&:hover': { color: 'primary.main' }, transition: 'color 0.15s' }}
                  >
                    <EditIcon />
                  </IconButton>
                  <IconButton
                    aria-label="Delete node"
                    size="small"
                    onClick={() => onDelete(node.id)}
                    sx={{ color: 'rgba(255,255,255,0.25)', '&:hover': { color: 'error.main' }, transition: 'color 0.15s' }}
                  >
                    <DeleteIcon />
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
