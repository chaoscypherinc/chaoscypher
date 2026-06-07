// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Dialog for creating or editing an edge (relationship).
 *
 * Contains template selection, source/target entity autocompletes,
 * a label field, and the property editor panel.
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
  Autocomplete,
} from '@mui/material';
import PropertyEditPanel from '../../components/PropertyEditPanel';
import type { Edge, Node, Template, EdgeCreateRequest } from '../../types';
import type { UsePropertyEditorReturn } from '../../hooks/usePropertyEditor';

interface EdgeFormDialogProps {
  open: boolean;
  editing: Edge | null;
  formData: Partial<EdgeCreateRequest>;
  templates: Template[];
  nodes: Node[];
  propEditor: UsePropertyEditorReturn;
  onFormChange: (data: Partial<EdgeCreateRequest>) => void;
  onSave: () => void;
  onClose: () => void;
}

/** Modal dialog for creating or editing a relationship between two entities. */
export function EdgeFormDialog({
  open,
  editing,
  formData,
  templates,
  nodes,
  propEditor,
  onFormChange,
  onSave,
  onClose,
}: EdgeFormDialogProps) {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{editing ? 'Edit Relationship' : 'Create Relationship'}</DialogTitle>
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

          <Autocomplete
            options={nodes}
            getOptionLabel={(node) => node.label || node.id}
            value={nodes.find((n) => n.id === formData.source_node_id) || null}
            onChange={(_, node) =>
              onFormChange({ ...formData, source_node_id: node?.id || '' })
            }
            disabled={!!editing}
            sx={{ mb: 2 }}
            renderInput={(params) => (
              <TextField {...params} label="Source Entity" required />
            )}
          />

          <Autocomplete
            options={nodes}
            getOptionLabel={(node) => node.label || node.id}
            value={nodes.find((n) => n.id === formData.target_node_id) || null}
            onChange={(_, node) =>
              onFormChange({ ...formData, target_node_id: node?.id || '' })
            }
            disabled={!!editing}
            sx={{ mb: 2 }}
            renderInput={(params) => (
              <TextField {...params} label="Target Entity" required />
            )}
          />

          <TextField
            label="Label"
            value={formData.label || ''}
            onChange={(e) => onFormChange({ ...formData, label: e.target.value })}
            fullWidth
            required
            helperText="Describe this relationship (e.g., 'supports', 'contradicts')"
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
