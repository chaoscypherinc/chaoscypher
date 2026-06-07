// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Box,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Tab,
  Tabs,
  TextField,
  Typography,
} from '@mui/material';
import type { Template } from '../../types';
import PropertyEditor, { PropertyDefinition } from '../../components/PropertyEditor';
import { TemplateIconPicker } from '../../components/TemplateIconPicker';
import { ghostInputSx, ghostTabsSx } from '../../theme/ghostStyles';
import { glassPanelSx } from '../../theme/cardStyles';

interface TemplateEditorPanelProps {
  template: Template;
  editing: boolean;
  formData: Partial<Template>;
  activeTab: number;
  onActiveTabChange: (tab: number) => void;
  onFormDataChange: (data: Partial<Template>) => void;
}

export function TemplateEditorPanel({
  template,
  editing,
  formData,
  activeTab,
  onActiveTabChange,
  onFormDataChange,
}: TemplateEditorPanelProps) {
  return (
    <Box sx={{ ...glassPanelSx, p: 3 }}>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mb: 3 }}>
        <TextField
          label="Template Name"
          value={editing ? formData.name || '' : template.name}
          onChange={(e) => onFormDataChange({ ...formData, name: e.target.value })}
          fullWidth
          disabled={!editing}
          helperText={
            editing ? "Cannot start with 'system_' - this prefix is reserved" : ''
          }
          sx={ghostInputSx}
        />

        <FormControl fullWidth disabled sx={ghostInputSx}>
          <InputLabel>Template Type</InputLabel>
          <Select value={template.template_type} label="Template Type">
            <MenuItem value="node">Node</MenuItem>
            <MenuItem value="edge">Edge</MenuItem>
          </Select>
        </FormControl>

        <TextField
          label="Description"
          value={editing ? formData.description || '' : template.description || ''}
          onChange={(e) => onFormDataChange({ ...formData, description: e.target.value })}
          fullWidth
          disabled={!editing}
          multiline
          rows={2}
          sx={ghostInputSx}
        />
      </Box>

      {editing && (
        <Box sx={{ mb: 2 }}>
          <TemplateIconPicker
            icon={formData.icon ?? template.icon ?? null}
            color={formData.color ?? template.color ?? null}
            templateName={formData.name || template.name}
            templateId={template.id}
            onIconChange={(icon) => onFormDataChange({ ...formData, icon })}
            onColorChange={(color) => onFormDataChange({ ...formData, color })}
          />
        </Box>
      )}

      <Box sx={{ borderBottom: '1px solid rgba(255, 255, 255, 0.06)', mb: 2 }} />

      <Box sx={{ borderBottom: '1px solid rgba(255, 255, 255, 0.06)', mb: 2 }}>
        <Tabs
          value={activeTab}
          onChange={(_, newValue) => onActiveTabChange(newValue)}
          sx={{ ...ghostTabsSx }}
        >
          <Tab label="Property Editor" />
          <Tab label="Raw JSON" />
        </Tabs>
      </Box>

      {activeTab === 0 &&
        (editing ? (
          <PropertyEditor
            properties={(formData.properties as PropertyDefinition[]) || []}
            onChange={(properties) => onFormDataChange({ ...formData, properties })}
          />
        ) : (
          <PropertyEditor
            properties={(template.properties as PropertyDefinition[]) || []}
            onChange={() => {}}
          />
        ))}

      {activeTab === 1 && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Typography variant="subtitle2" gutterBottom>
            Properties (JSON Array)
          </Typography>
          <TextField
            multiline
            rows={15}
            value={
              editing
                ? JSON.stringify(formData.properties || [], null, 2)
                : JSON.stringify(template.properties || [], null, 2)
            }
            onChange={(e) => {
              try {
                const properties = JSON.parse(e.target.value);
                if (Array.isArray(properties)) {
                  onFormDataChange({ ...formData, properties });
                }
              } catch (_error) {
                // Invalid JSON, don't update
              }
            }}
            fullWidth
            disabled={!editing}
            placeholder="[]"
            sx={{ fontFamily: 'monospace', ...ghostInputSx }}
          />
        </Box>
      )}
    </Box>
  );
}
