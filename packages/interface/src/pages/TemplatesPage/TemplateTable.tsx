// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Table view for listing template rows with selection and actions.
 *
 * Renders the full table including row selection checkboxes, template icon
 * previews, type chips, and per-row edit/delete action buttons.
 */

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
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import TemplateIcon from '../../components/TemplateIcon';
import { InfoTooltip } from '../../components/InfoTooltip';
import { formatDate } from '../../utils/formatters';
import type { ChangeEvent } from 'react';
import type { Template } from '../../types';

/** Thin wrapper preserving the small-circle preview sizing (32px container, 18px icon). */
function TemplateIconPreview({ template }: { template: Template }) {
  return (
    <TemplateIcon
      template={template}
      variant={template.template_type === 'edge' ? 'edge' : 'node'}
      size={18}
      containerSize={32}
    />
  );
}

interface TemplateTableProps {
  templates: Template[];
  selectableCount: number;
  selectedTemplates: Set<string>;
  showSystemTemplates: boolean;
  onSelectAll: (event: ChangeEvent<HTMLInputElement>) => void;
  onSelectTemplate: (id: string) => void;
  onEdit: (template: Template) => void;
  onDelete: (template: Template) => void;
  onNavigate: (path: string) => void;
}

/** Table of template rows with bulk selection and per-row actions. */
export function TemplateTable({
  templates,
  selectableCount,
  selectedTemplates,
  showSystemTemplates,
  onSelectAll,
  onSelectTemplate,
  onEdit,
  onDelete,
  onNavigate,
}: TemplateTableProps) {
  return (
    <TableContainer component={Paper}>
      <Table>
        <TableHead>
          <TableRow>
            <TableCell padding="checkbox">
              <Checkbox
                indeterminate={
                  selectedTemplates.size > 0 &&
                  selectedTemplates.size < selectableCount
                }
                checked={
                  selectableCount > 0 &&
                  selectedTemplates.size === selectableCount
                }
                onChange={onSelectAll}
                disabled={selectableCount === 0}
              />
            </TableCell>
            <TableCell>Name</TableCell>
            <TableCell>Type</TableCell>
            <TableCell>Description</TableCell>
            <TableCell align="right">Actions</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {templates.length === 0 ? (
            <TableRow>
              <TableCell colSpan={7} align="center">
                <Typography variant="body2" color="textSecondary" sx={{ py: 3 }}>
                  No templates available. {!showSystemTemplates && 'Enable "Show system templates" or '}Create custom templates to get started.
                </Typography>
              </TableCell>
            </TableRow>
          ) : (
            templates.map((template) => (
              <TableRow
                key={template.id}
                hover
                selected={selectedTemplates.has(template.id)}
                onClick={(e) => {
                  // Don't navigate if clicking on action buttons or checkbox
                  if ((e.target as HTMLElement).closest('button') ||
                      (e.target as HTMLElement).closest('[type="checkbox"]')) {
                    return;
                  }
                  onNavigate(`/templates/${template.id}`);
                }}
                sx={{ cursor: 'pointer' }}
              >
                <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
                  <Checkbox
                    checked={selectedTemplates.has(template.id)}
                    onChange={() => onSelectTemplate(template.id)}
                    disabled={template.is_system}
                  />
                </TableCell>
                <TableCell>
                  <Tooltip
                    title={
                      <InfoTooltip items={[
                        { label: 'ID', value: template.id, sx: { fontFamily: 'monospace', mb: 0.5 } },
                        { label: 'Created', value: formatDate(template.created_at || '') },
                        { label: 'Updated', value: formatDate(template.updated_at || '') },
                        ...(template.is_system
                          ? [{ label: 'Type', value: 'System Template', sx: { mt: 0.5, color: 'warning.main' } }]
                          : []),
                      ]} />
                    }
                    arrow
                    placement="right"
                  >
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                      <TemplateIconPreview template={template} />
                      <Typography variant="body2">{template.name}</Typography>
                      {template.is_system && (
                        <Chip label="System" size="small" variant="outlined" sx={{ ml: 0.5, height: 20 }} />
                      )}
                    </Box>
                  </Tooltip>
                </TableCell>
                <TableCell>
                  <Chip
                    label={template.template_type}
                    size="small"
                    variant="outlined"
                    color={template.template_type === 'node' ? 'primary' : 'secondary'}
                  />
                </TableCell>
                <TableCell>
                  <Typography
                    variant="body2"
                    sx={{
                      maxWidth: 300,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {template.description || '-'}
                  </Typography>
                </TableCell>
                <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                  {!template.is_system && (
                    <>
                      <IconButton
                        aria-label="Edit template"
                        size="small"
                        onClick={() => onEdit(template)}
                        sx={{ color: 'rgba(255,255,255,0.25)', '&:hover': { color: 'primary.main' }, transition: 'color 0.15s' }}
                      >
                        <EditIcon fontSize="small" />
                      </IconButton>
                      <IconButton
                        aria-label="Delete template"
                        size="small"
                        onClick={() => onDelete(template)}
                        sx={{ color: 'rgba(255,255,255,0.25)', '&:hover': { color: 'error.main' }, transition: 'color 0.15s' }}
                      >
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </>
                  )}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
