// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Dialog for creating or editing an entity (node).
 *
 * Contains template selection, a label field, and the property editor panel.
 */

import {
  Box,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
} from '@mui/material';
import PropertyEditPanel from '../../components/PropertyEditPanel';
import type { Node, Template, NodeCreateRequest } from '../../types';
import type { UsePropertyEditorReturn } from '../../hooks/usePropertyEditor';

interface NodeFormDialogProps {
  open: boolean;
  editing: Node | null;
  formData: Partial<NodeCreateRequest>;
  templates: Template[];
  propEditor: UsePropertyEditorReturn;
  onFormChange: (data: Partial<NodeCreateRequest>) => void;
  onSave: () => void;
  onClose: () => void;
}

/** Modal dialog for creating or editing an entity. */
export function NodeFormDialog({
  open,
  editing,
  formData,
  templates,
  propEditor,
  onFormChange,
  onSave,
  onClose,
}: NodeFormDialogProps) {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{editing ? 'Edit Entity' : 'Create Entity'}</DialogTitle>
      <DialogContent>
        <Box sx={{ pt: 2 }}>
          <FormControl fullWidth sx={{ mb: 2 }}>
            <InputLabel>Template</InputLabel>
            <Select
              value={formData.template_id || ''}
              onChange={(e) =>
                onFormChange({ ...formData, template_id: e.target.value })
              }
              disabled={!!editing}
            >
              {templates.map((template) => (
                <MenuItem key={template.id} value={template.id}>
                  {template.name}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          <TextField
            label="Label"
            value={formData.label || ''}
            onChange={(e) => onFormChange({ ...formData, label: e.target.value })}
            fullWidth
            required
            sx={{ mb: 2 }}
          />

          <PropertyEditPanel {...propEditor} />
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button onClick={onSave} variant="outlined">
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}
