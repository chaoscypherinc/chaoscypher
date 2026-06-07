// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * StepTemplatePanel: Drawer for browsing and applying step templates
 *
 * Allows users to view saved step templates and apply them to create
 * new workflow steps quickly.
 */

import React, { useState } from 'react';
import {
  Drawer,
  Box,
  Typography,
  List,
  ListItem,
  ListItemText,
  ListItemSecondaryAction,
  IconButton,
  TextField,
  InputAdornment,
  Button,
  Chip,
  Alert,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Tooltip,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import CloseIcon from '@mui/icons-material/Close';
import DeleteIcon from '@mui/icons-material/Delete';
import AddIcon from '@mui/icons-material/Add';
import DownloadIcon from '@mui/icons-material/Download';
import UploadIcon from '@mui/icons-material/Upload';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import { useStepTemplates } from '../../hooks';
import type { StepTemplate } from '../../types';
import { CategoryColors } from '../../../../theme/colors';
import { ghostButtonSx, ghostCancelBtnSx, ghostDialogPaperSx } from '../../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../../theme/palette';

interface StepTemplatePanelProps {
  open: boolean;
  onClose: () => void;
  onApplyTemplate: (template: StepTemplate) => void;
}

export const StepTemplatePanel: React.FC<StepTemplatePanelProps> = ({
  open,
  onClose,
  onApplyTemplate,
}) => {
  const {
    templates,
    loading,
    error,
    deleteTemplate,
    exportTemplates,
    importTemplates,
  } = useStepTemplates();

  const [searchQuery, setSearchQuery] = useState('');
  const [importDialogOpen, setImportDialogOpen] = useState(false);
  const [importJson, setImportJson] = useState('');
  const [importError, setImportError] = useState<string | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);

  // Filter templates by search
  const filteredTemplates = templates.filter(
    (t) =>
      t.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      t.category.toLowerCase().includes(searchQuery.toLowerCase()) ||
      t.toolId.toLowerCase().includes(searchQuery.toLowerCase())
  );

  // Handle export
  const handleExport = () => {
    const json = exportTemplates();
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'workflow-step-templates.json';
    a.click();
    URL.revokeObjectURL(url);
  };

  // Handle import
  const handleImport = () => {
    setImportError(null);
    if (!importJson.trim()) {
      setImportError('Please paste JSON content');
      return;
    }

    const result = importTemplates(importJson);
    if (result.success > 0) {
      setImportDialogOpen(false);
      setImportJson('');
    }
    if (result.failed > 0) {
      setImportError(`Imported ${result.success} templates. ${result.failed} failed validation.`);
    }
  };

  // Handle delete confirmation
  const handleDelete = (templateId: string) => {
    deleteTemplate(templateId);
    setDeleteConfirmId(null);
  };

  // Copy template to clipboard
  const handleCopyTemplate = (template: StepTemplate) => {
    navigator.clipboard.writeText(JSON.stringify(template, null, 2));
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      sx={{
        '& .MuiDrawer-paper': {
          width: 360,
        },
      }}
    >
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* Header */}
        <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
            <Typography variant="subtitle1" sx={{
              fontWeight: 600
            }}>
              Step Templates
            </Typography>
            <IconButton aria-label="Close" size="small" onClick={onClose}>
              <CloseIcon fontSize="small" />
            </IconButton>
          </Box>

          {/* Search */}
          <TextField
            size="small"
            fullWidth
            placeholder="Search templates..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            slotProps={{
              input: {
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" />
                  </InputAdornment>
                ),
              }
            }}
          />
        </Box>

        {/* Error display */}
        {error && (
          <Alert severity="warning" sx={{ m: 1 }}>
            {error}
          </Alert>
        )}

        {/* Templates list */}
        <Box sx={{ flex: 1, overflow: 'auto' }}>
          {loading ? (
            <Box sx={{ p: 3, textAlign: 'center' }}>
              <Typography variant="body2" sx={{
                color: "text.secondary"
              }}>
                Loading templates...
              </Typography>
            </Box>
          ) : filteredTemplates.length === 0 ? (
            <Box sx={{ p: 3, textAlign: 'center' }}>
              <Typography variant="body2" sx={{
                color: "text.secondary"
              }}>
                {searchQuery
                  ? 'No templates match your search.'
                  : 'No templates saved yet. Save step configurations as templates from the properties panel.'}
              </Typography>
            </Box>
          ) : (
            <List dense>
              {filteredTemplates.map((template) => (
                <ListItem
                  key={template.id}
                  sx={{
                    borderLeft: `3px solid ${CategoryColors[template.category] || CategoryColors.templates}`,
                    '&:hover': {
                      bgcolor: 'action.hover',
                    },
                  }}
                >
                  <ListItemText
                    primary={
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Typography variant="body2" sx={{
                          fontWeight: 500
                        }}>
                          {template.name}
                        </Typography>
                        <Chip
                          label={template.category}
                          size="small"
                          sx={{
                            height: 16,
                            fontSize: '0.65rem',
                            bgcolor: `${CategoryColors[template.category] || CategoryColors.templates}20`,
                            color: CategoryColors[template.category] || CategoryColors.templates,
                          }}
                        />
                      </Box>
                    }
                    secondary={
                      <Typography variant="caption" sx={{
                        color: "text.secondary"
                      }}>
                        {template.toolId}
                      </Typography>
                    }
                  />
                  <ListItemSecondaryAction>
                    <Tooltip title="Apply Template">
                      <IconButton
                        aria-label="Apply Template"
                        size="small"
                        color="primary"
                        onClick={() => onApplyTemplate(template)}
                      >
                        <AddIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Copy JSON">
                      <IconButton
                        aria-label="Copy JSON"
                        size="small"
                        onClick={() => handleCopyTemplate(template)}
                      >
                        <ContentCopyIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Delete">
                      <IconButton
                        aria-label="Delete template"
                        size="small"
                        color="error"
                        onClick={() => setDeleteConfirmId(template.id)}
                      >
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </ListItemSecondaryAction>
                </ListItem>
              ))}
            </List>
          )}
        </Box>

        {/* Footer actions */}
        <Box sx={{ p: 2, borderTop: 1, borderColor: 'divider' }}>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button
              variant="outlined"
              size="small"
              startIcon={<DownloadIcon />}
              onClick={handleExport}
              disabled={templates.length === 0}
              fullWidth
            >
              Export
            </Button>
            <Button
              variant="outlined"
              size="small"
              startIcon={<UploadIcon />}
              onClick={() => setImportDialogOpen(true)}
              fullWidth
            >
              Import
            </Button>
          </Box>
        </Box>
      </Box>
      {/* Import Dialog */}
      <Dialog open={importDialogOpen} onClose={() => setImportDialogOpen(false)} maxWidth="sm" fullWidth slotProps={{
        paper: { sx: ghostDialogPaperSx }
      }}>
        <DialogTitle>Import Templates</DialogTitle>
        <DialogContent>
          <Typography
            variant="body2"
            sx={{
              color: "text.secondary",
              mb: 2
            }}>
            Paste the JSON content from an exported templates file.
          </Typography>
          <TextField
            fullWidth
            multiline
            rows={10}
            value={importJson}
            onChange={(e) => setImportJson(e.target.value)}
            placeholder="Paste JSON here..."
            error={!!importError}
            helperText={importError}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setImportDialogOpen(false)} sx={ghostCancelBtnSx}>Cancel</Button>
          <Button variant="outlined" sx={ghostButtonSx(ChaosCypherPalette.primary)} onClick={handleImport}>
            Import
          </Button>
        </DialogActions>
      </Dialog>
      {/* Delete Confirmation Dialog */}
      <Dialog
        open={!!deleteConfirmId}
        onClose={() => setDeleteConfirmId(null)}
        slotProps={{
          paper: { sx: ghostDialogPaperSx }
        }}
      >
        <DialogTitle>Delete Template?</DialogTitle>
        <DialogContent>
          <Typography>
            Are you sure you want to delete this template? This action cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteConfirmId(null)} sx={ghostCancelBtnSx}>Cancel</Button>
          <Button
            variant="outlined"
            sx={ghostButtonSx(ChaosCypherPalette.error)}
            onClick={() => deleteConfirmId && handleDelete(deleteConfirmId)}
          >
            Delete
          </Button>
        </DialogActions>
      </Dialog>
    </Drawer>
  );
};
