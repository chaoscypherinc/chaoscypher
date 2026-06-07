// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * WorkflowSettingsModal: Dialog for editing workflow metadata
 *
 * Allows users to configure workflow name, description, category,
 * input/output schemas, and other settings. Uses visual schema builders
 * instead of raw JSON editing for better user experience.
 */

import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  FormControlLabel,
  Switch,
  Box,
  Typography,
  Chip,
  Alert,
  Divider,
  IconButton,
  Tooltip,
} from '@mui/material';
import CodeIcon from '@mui/icons-material/Code';
import type { WorkflowMetadata } from '../../types';
import { SchemaFieldBuilder } from '../forms/SchemaFieldBuilder';
import { fieldsToJsonSchema, jsonSchemaToFields } from '../forms/schemaFieldTypes';
import {
  ghostInputSx,
  ghostDialogPaperSx,
  ghostButtonSx,
  ghostCancelBtnSx,
  ghostErrorAlertSx,
  ghostSwitchSx,
} from '../../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../../theme/palette';

interface WorkflowSettingsModalProps {
  open: boolean;
  onClose: () => void;
  workflow: Partial<WorkflowMetadata> | null;
  onSave: (settings: Partial<WorkflowMetadata>) => void;
  isNewWorkflow: boolean;
}

const WORKFLOW_CATEGORIES = [
  'general',
  'data-processing',
  'ai-automation',
  'integrations',
  'notifications',
  'analytics',
  'other',
];

/**
 * SchemaField type for the visual builder
 */
interface SchemaField {
  id: string;
  name: string;
  type: 'string' | 'number' | 'boolean' | 'object' | 'array' | 'any';
  description: string;
  required: boolean;
  defaultValue?: string;
  enumValues?: string[];
}

export const WorkflowSettingsModal: React.FC<WorkflowSettingsModalProps> = ({
  open,
  onClose,
  workflow,
  onSave,
  isNewWorkflow,
}) => {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('general');
  const [isActive, setIsActive] = useState(true);
  const [exposeAsAiTool, setExposeAsAiTool] = useState(false);
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState('');
  const [inputFields, setInputFields] = useState<SchemaField[]>([]);
  const [outputFields, setOutputFields] = useState<SchemaField[]>([]);
  const [showJsonEditor, setShowJsonEditor] = useState(false);
  const [inputSchemaJson, setInputSchemaJson] = useState('{}');
  const [outputSchemaJson, setOutputSchemaJson] = useState('{}');
  const [schemaError, setSchemaError] = useState<string | null>(null);

  // Sync form with workflow data. Intentional setState-in-effect: when the
  // workflow prop changes (e.g. modal reopens for a different workflow), we
  // need to reset all the form fields from the new prop.
  useEffect(() => {
    if (workflow) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setName(workflow.name || '');
      setDescription(workflow.description || '');
      setCategory(workflow.category || 'general');
      setIsActive(workflow.is_active ?? true);
      setExposeAsAiTool(workflow.expose_as_ai_tool ?? false);
      setTags(workflow.tags || []);
      // Parse schemas into visual fields
      setInputFields(jsonSchemaToFields(workflow.input_schema as Record<string, unknown> || null));
      setOutputFields(jsonSchemaToFields(workflow.output_schema as Record<string, unknown> || null));
      // Also keep JSON versions for fallback editor
      setInputSchemaJson(JSON.stringify(workflow.input_schema || {}, null, 2));
      setOutputSchemaJson(JSON.stringify(workflow.output_schema || {}, null, 2));
    } else {
      // Reset for new workflow
      setName('');
      setDescription('');
      setCategory('general');
      setIsActive(true);
      setExposeAsAiTool(false);
      setTags([]);
      setInputFields([]);
      setOutputFields([]);
      setInputSchemaJson('{}');
      setOutputSchemaJson('{}');
    }
    setSchemaError(null);
    setShowJsonEditor(false);
  }, [workflow, open]);

  // Validate JSON schemas (only used in JSON editor mode)
  const validateJsonSchemas = (): boolean => {
    try {
      JSON.parse(inputSchemaJson);
      JSON.parse(outputSchemaJson);
      setSchemaError(null);
      return true;
    } catch (_e) {
      setSchemaError('Invalid JSON in schema fields');
      return false;
    }
  };

  // Sync JSON editor with visual fields when switching modes
  const handleToggleJsonEditor = () => {
    if (showJsonEditor) {
      // Switching FROM JSON to visual - parse JSON into fields
      try {
        setInputFields(jsonSchemaToFields(JSON.parse(inputSchemaJson)));
        setOutputFields(jsonSchemaToFields(JSON.parse(outputSchemaJson)));
        setSchemaError(null);
      } catch {
        setSchemaError('Invalid JSON - cannot switch to visual editor');
        return;
      }
    } else {
      // Switching FROM visual to JSON - convert fields to JSON
      setInputSchemaJson(JSON.stringify(fieldsToJsonSchema(inputFields), null, 2));
      setOutputSchemaJson(JSON.stringify(fieldsToJsonSchema(outputFields), null, 2));
    }
    setShowJsonEditor(!showJsonEditor);
  };

  // Handle save
  const handleSave = () => {
    if (!name.trim()) {
      setSchemaError('Workflow name is required');
      return;
    }

    let inputSchemaObj: Record<string, unknown>;
    let outputSchemaObj: Record<string, unknown>;

    if (showJsonEditor) {
      // Using JSON editor - validate and parse
      if (!validateJsonSchemas()) {
        return;
      }
      try {
        inputSchemaObj = JSON.parse(inputSchemaJson);
        outputSchemaObj = JSON.parse(outputSchemaJson);
      } catch {
        setSchemaError('Invalid JSON in schema fields');
        return;
      }
    } else {
      // Using visual builder - convert fields to JSON Schema
      inputSchemaObj = fieldsToJsonSchema(inputFields);
      outputSchemaObj = fieldsToJsonSchema(outputFields);
    }

    onSave({
      name: name.trim(),
      description: description.trim() || undefined,
      category,
      is_active: isActive,
      expose_as_ai_tool: exposeAsAiTool,
      tags: tags.length > 0 ? tags : undefined,
      input_schema: inputSchemaObj,
      output_schema: outputSchemaObj,
    });

    onClose();
  };

  // Handle tag addition
  const handleAddTag = () => {
    const tag = tagInput.trim().toLowerCase();
    if (tag && !tags.includes(tag)) {
      setTags([...tags, tag]);
      setTagInput('');
    }
  };

  // Handle tag removal
  const handleRemoveTag = (tagToRemove: string) => {
    setTags(tags.filter((t) => t !== tagToRemove));
  };

  // Handle tag input keypress
  const handleTagKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleAddTag();
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      slotProps={{
        paper: { sx: ghostDialogPaperSx }
      }}
    >
      <DialogTitle>
        {isNewWorkflow ? 'New Workflow Settings' : 'Workflow Settings'}
      </DialogTitle>
      <DialogContent>
        {schemaError && (
          <Alert severity="error" sx={{ mb: 2, ...ghostErrorAlertSx }}>
            {schemaError}
          </Alert>
        )}

        {/* Basic Info */}
        <TextField
          label="Workflow Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          fullWidth
          required
          margin="dense"
          autoFocus
          sx={ghostInputSx}
        />

        <TextField
          label="Description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          fullWidth
          multiline
          rows={2}
          margin="dense"
          sx={ghostInputSx}
        />

        <FormControl fullWidth margin="dense" sx={ghostInputSx}>
          <InputLabel>Category</InputLabel>
          <Select
            value={category}
            label="Category"
            onChange={(e) => setCategory(e.target.value)}
          >
            {WORKFLOW_CATEGORIES.map((cat) => (
              <MenuItem key={cat} value={cat}>
                {cat.replace('-', ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        {/* Tags */}
        <Box sx={{ mt: 2 }}>
          <Typography variant="subtitle2" gutterBottom>
            Tags
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, mb: 1 }}>
            <TextField
              size="small"
              placeholder="Add tag..."
              value={tagInput}
              onChange={(e) => setTagInput(e.target.value)}
              onKeyPress={handleTagKeyPress}
              sx={{ flex: 1, ...ghostInputSx }}
            />
            <Button
              variant="outlined"
              size="small"
              onClick={handleAddTag}
              sx={ghostButtonSx(ChaosCypherPalette.primary)}
            >
              Add
            </Button>
          </Box>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
            {tags.map((tag) => (
              <Chip
                key={tag}
                label={tag}
                size="small"
                onDelete={() => handleRemoveTag(tag)}
                sx={{
                  bgcolor: 'rgba(0, 229, 255, 0.08)',
                  borderColor: 'rgba(0, 229, 255, 0.2)',
                  border: '1px solid',
                  color: 'primary.main',
                  '& .MuiChip-deleteIcon': {
                    color: 'rgba(0, 229, 255, 0.5)',
                    '&:hover': { color: 'primary.main' },
                  },
                }}
              />
            ))}
          </Box>
        </Box>

        <Divider sx={{ my: 2, borderColor: 'rgba(255, 255, 255, 0.06)' }} />

        {/* Options */}
        <Typography variant="subtitle2" gutterBottom>
          Options
        </Typography>

        <FormControlLabel
          control={
            <Switch
              checked={isActive}
              onChange={(e) => setIsActive(e.target.checked)}
              sx={ghostSwitchSx}
            />
          }
          label="Active (can be executed)"
        />

        <FormControlLabel
          control={
            <Switch
              checked={exposeAsAiTool}
              onChange={(e) => setExposeAsAiTool(e.target.checked)}
              sx={ghostSwitchSx}
            />
          }
          label="Expose as AI Tool (available in chat)"
        />

        <Divider sx={{ my: 2, borderColor: 'rgba(255, 255, 255, 0.06)' }} />

        {/* Schemas */}
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
          <Typography variant="subtitle2">
            Input/Output Schemas
          </Typography>
          <Tooltip title={showJsonEditor ? 'Use visual builder' : 'Use JSON editor'}>
            <IconButton aria-label={showJsonEditor ? 'Use visual builder' : 'Use JSON editor'} size="small" onClick={handleToggleJsonEditor}>
              <CodeIcon fontSize="small" color={showJsonEditor ? 'primary' : 'inherit'} />
            </IconButton>
          </Tooltip>
        </Box>

        {showJsonEditor ? (
          // JSON Editor Mode (fallback)
          (<>
            <TextField
              label="Input Schema (JSON)"
              value={inputSchemaJson}
              onChange={(e) => setInputSchemaJson(e.target.value)}
              fullWidth
              multiline
              rows={4}
              margin="dense"
              sx={{ ...ghostInputSx, fontFamily: 'monospace', fontSize: '0.85rem' }}
              helperText="Define expected input parameters"
            />
            <TextField
              label="Output Schema (JSON)"
              value={outputSchemaJson}
              onChange={(e) => setOutputSchemaJson(e.target.value)}
              fullWidth
              multiline
              rows={4}
              margin="dense"
              sx={{ ...ghostInputSx, fontFamily: 'monospace', fontSize: '0.85rem' }}
              helperText="Define expected output format"
            />
          </>)
        ) : (
          // Visual Builder Mode (default)
          (<>
            <Box sx={{ mb: 3 }}>
              <SchemaFieldBuilder
                fields={inputFields}
                onChange={setInputFields}
                label="Input Parameters"
                helperText="Define what data this workflow needs to run"
                showDefaultValue={true}
                showEnumValues={true}
              />
            </Box>
            <Box sx={{ mb: 2 }}>
              <SchemaFieldBuilder
                fields={outputFields}
                onChange={setOutputFields}
                label="Output Fields"
                helperText="Define what data this workflow returns"
                showDefaultValue={false}
                showEnumValues={false}
              />
            </Box>
          </>)
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={ghostCancelBtnSx}>
          Cancel
        </Button>
        <Button
          variant="outlined"
          onClick={handleSave}
          sx={ghostButtonSx(ChaosCypherPalette.primary)}
        >
          {isNewWorkflow ? 'Create Workflow' : 'Save Settings'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};
