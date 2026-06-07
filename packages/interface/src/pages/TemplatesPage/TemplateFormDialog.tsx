// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Dialog for creating or editing a template.
 *
 * Contains template name, type selector, description, icon picker,
 * a tabbed property editor (visual + raw JSON), and save/cancel actions.
 */

import { useState } from 'react';
import {
  Box,
  Typography,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Tabs,
  Tab,
} from '@mui/material';
import PropertyEditor from '../../components/PropertyEditor';
import type { PropertyDefinition } from '../../components/PropertyEditor';
import { TemplateIconPicker } from '../../components/TemplateIconPicker';
import type { Template } from '../../types';

export interface TemplateFormData {
  name: string;
  description: string;
  template_type: 'node' | 'edge';
  properties: PropertyDefinition[];
  icon: string | null;
  color: string | null;
}

interface TemplateFormDialogProps {
  open: boolean;
  editing: Template | null;
  formData: TemplateFormData;
  onFormChange: (data: TemplateFormData) => void;
  onSave: () => void;
  onClose: () => void;
}

/** Modal dialog for creating or editing a template definition. */
export function TemplateFormDialog({
  open,
  editing,
  formData,
  onFormChange,
  onSave,
  onClose,
}: TemplateFormDialogProps) {
  const [dialogTab, setDialogTab] = useState(0);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
    >
      <DialogTitle>
        {editing ? 'Edit Template' : 'Create Template'}
      </DialogTitle>
      <DialogContent>
        <Box sx={{ pt: 2 }}>
          <TextField
            label="Template Name"
            value={formData.name}
            onChange={(e) => onFormChange({ ...formData, name: e.target.value })}
            fullWidth
            required
            helperText="Cannot start with 'system_' - this prefix is reserved"
            sx={{ mb: 2 }}
          />

          <FormControl fullWidth disabled={!!editing} sx={{ mb: 2 }}>
            <InputLabel>Template Type</InputLabel>
            <Select
              value={formData.template_type}
              label="Template Type"
              onChange={(e) =>
                onFormChange({ ...formData, template_type: e.target.value as 'node' | 'edge' })
              }
            >
              <MenuItem value="node">Node</MenuItem>
              <MenuItem value="edge">Edge</MenuItem>
            </Select>
          </FormControl>

          <TextField
            label="Description"
            value={formData.description}
            onChange={(e) => onFormChange({ ...formData, description: e.target.value })}
            fullWidth
            multiline
            rows={2}
            sx={{ mb: 2 }}
          />

          <TemplateIconPicker
            icon={formData.icon}
            color={formData.color}
            templateName={formData.name}
            templateId={editing?.id}
            onIconChange={(icon) => onFormChange({ ...formData, icon })}
            onColorChange={(color) => onFormChange({ ...formData, color })}
          />

          <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 2, mt: 2 }}>
            <Tabs value={dialogTab} onChange={(_, newValue) => setDialogTab(newValue)}>
              <Tab label="Property Editor" />
              <Tab label="Raw JSON" />
            </Tabs>
          </Box>

          {/* Property Editor Tab */}
          {dialogTab === 0 && (
            <PropertyEditor
              properties={formData.properties}
              onChange={(properties) => onFormChange({ ...formData, properties })}
            />
          )}

          {/* Raw JSON Tab */}
          {dialogTab === 1 && (
            <Box>
              <Typography variant="subtitle2" gutterBottom>
                Properties (JSON Array)
              </Typography>
              <TextField
                multiline
                rows={12}
                value={JSON.stringify(formData.properties, null, 2)}
                onChange={(e) => {
                  try {
                    const properties = JSON.parse(e.target.value);
                    if (Array.isArray(properties)) {
                      onFormChange({ ...formData, properties });
                    }
                  } catch (_error) {
                    // Invalid JSON, don't update
                  }
                }}
                fullWidth
                placeholder='[]'
                sx={{ fontFamily: 'monospace' }}
              />
            </Box>
          )}
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
