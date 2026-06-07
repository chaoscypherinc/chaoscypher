// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * SchemaFieldBuilder: Visual builder for JSON Schema field definitions
 *
 * Allows users to define workflow input/output schemas by adding fields
 * with name, type, description, and required flag - no JSON editing needed.
 */

import React, { useState, useCallback } from 'react';
import {
  Box,
  Typography,
  TextField,
  Select,
  MenuItem,
  FormControl,
  IconButton,
  Button,
  Paper,
  Tooltip,
  Collapse,
  Chip,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import DragIndicatorIcon from '@mui/icons-material/DragIndicator';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import type { FieldType } from '../../types/dataflow';
import { DataTypeColors } from '../../../../theme/colors';
import type { SchemaField } from './schemaFieldTypes';

interface SchemaFieldBuilderProps {
  /** Current fields */
  fields: SchemaField[];
  /** Callback when fields change */
  onChange: (fields: SchemaField[]) => void;
  /** Label for the section */
  label?: string;
  /** Helper text */
  helperText?: string;
  /** Whether to show default value option */
  showDefaultValue?: boolean;
  /** Whether to show enum values option */
  showEnumValues?: boolean;
  /** Maximum number of fields allowed */
  maxFields?: number;
}

/**
 * Available field types with descriptions
 */
const FIELD_TYPES: { value: FieldType; label: string; description: string }[] = [
  { value: 'string', label: 'Text', description: 'Single or multi-line text' },
  { value: 'number', label: 'Number', description: 'Integer or decimal values' },
  { value: 'boolean', label: 'Yes/No', description: 'True or false value' },
  { value: 'object', label: 'Object', description: 'Nested JSON object' },
  { value: 'array', label: 'List', description: 'Array of items' },
  { value: 'any', label: 'Any', description: 'Any data type' },
];

/**
 * Generate unique ID
 */
function generateId(): string {
  return `field-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * Single field row component
 */
interface FieldRowProps {
  field: SchemaField;
  onUpdate: (field: SchemaField) => void;
  onDelete: () => void;
  showDefaultValue?: boolean;
  showEnumValues?: boolean;
}

const FieldRow: React.FC<FieldRowProps> = ({
  field,
  onUpdate,
  onDelete,
  showDefaultValue,
  showEnumValues,
}) => {
  const [expanded, setExpanded] = useState(false);
  const typeColor = DataTypeColors[field.type];

  const handleChange = (key: keyof SchemaField, value: unknown) => {
    onUpdate({ ...field, [key]: value });
  };

  return (
    <Paper
      variant="outlined"
      sx={{
        mb: 1,
        overflow: 'hidden',
        borderLeft: `3px solid ${typeColor}`,
      }}
    >
      {/* Main row */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 1,
          p: 1,
        }}
      >
        {/* Drag handle */}
        <DragIndicatorIcon
          fontSize="small"
          sx={{ color: 'text.disabled', cursor: 'grab' }}
        />

        {/* Field name */}
        <TextField
          size="small"
          placeholder="Field name"
          value={field.name}
          onChange={(e) => handleChange('name', e.target.value)}
          sx={{ flex: 1, minWidth: 100 }}
          slotProps={{
            htmlInput: {
              style: { fontFamily: 'monospace', fontSize: '0.85rem' },
            }
          }}
        />

        {/* Type selector */}
        <FormControl size="small" sx={{ minWidth: 100 }}>
          <Select
            value={field.type}
            onChange={(e) => handleChange('type', e.target.value)}
            sx={{ fontSize: '0.85rem' }}
          >
            {FIELD_TYPES.map((type) => (
              <MenuItem key={type.value} value={type.value}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <Box
                    sx={{
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      bgcolor: DataTypeColors[type.value],
                    }}
                  />
                  {type.label}
                </Box>
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        {/* Required toggle */}
        <Tooltip title={field.required ? 'Required' : 'Optional'}>
          <Chip
            label={field.required ? 'Req' : 'Opt'}
            size="small"
            color={field.required ? 'primary' : 'default'}
            onClick={() => handleChange('required', !field.required)}
            sx={{
              height: 24,
              fontSize: '0.7rem',
              cursor: 'pointer',
            }}
          />
        </Tooltip>

        {/* Expand/collapse */}
        <IconButton aria-label={expanded ? "Collapse" : "Expand"} size="small" onClick={() => setExpanded(!expanded)}>
          {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
        </IconButton>

        {/* Delete */}
        <IconButton aria-label="Delete field" size="small" onClick={onDelete} color="error">
          <DeleteIcon fontSize="small" />
        </IconButton>
      </Box>
      {/* Expanded details */}
      <Collapse in={expanded}>
        <Box
          sx={{
            px: 2,
            pb: 2,
            pt: 1,
            bgcolor: 'action.hover',
            borderTop: 1,
            borderColor: 'divider',
          }}
        >
          {/* Description */}
          <TextField
            size="small"
            fullWidth
            label="Description"
            placeholder="Describe what this field is for..."
            value={field.description}
            onChange={(e) => handleChange('description', e.target.value)}
            sx={{ mb: 1.5 }}
          />

          {/* Default value */}
          {showDefaultValue && (
            <TextField
              size="small"
              fullWidth
              label="Default Value"
              placeholder="Value if not provided"
              value={field.defaultValue || ''}
              onChange={(e) => handleChange('defaultValue', e.target.value)}
              sx={{ mb: 1.5 }}
            />
          )}

          {/* Enum values for string type */}
          {showEnumValues && field.type === 'string' && (
            <TextField
              size="small"
              fullWidth
              label="Allowed Values (comma-separated)"
              placeholder="option1, option2, option3"
              value={field.enumValues?.join(', ') || ''}
              onChange={(e) =>
                handleChange(
                  'enumValues',
                  e.target.value
                    .split(',')
                    .map((v) => v.trim())
                    .filter(Boolean)
                )
              }
              helperText="Leave empty to allow any text"
            />
          )}
        </Box>
      </Collapse>
    </Paper>
  );
};

/**
 * SchemaFieldBuilder component
 */
export const SchemaFieldBuilder: React.FC<SchemaFieldBuilderProps> = ({
  fields,
  onChange,
  label = 'Fields',
  helperText,
  showDefaultValue = true,
  showEnumValues = true,
  maxFields = 20,
}) => {
  // Add new field
  const addField = useCallback(() => {
    if (fields.length >= maxFields) return;

    const newField: SchemaField = {
      id: generateId(),
      name: '',
      type: 'string',
      description: '',
      required: false,
    };
    onChange([...fields, newField]);
  }, [fields, onChange, maxFields]);

  // Update field
  const updateField = useCallback(
    (index: number, updatedField: SchemaField) => {
      const newFields = [...fields];
      newFields[index] = updatedField;
      onChange(newFields);
    },
    [fields, onChange]
  );

  // Delete field
  const deleteField = useCallback(
    (index: number) => {
      onChange(fields.filter((_, i) => i !== index));
    },
    [fields, onChange]
  );

  return (
    <Box>
      {/* Header */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          mb: 1,
        }}
      >
        <Box>
          <Typography variant="body2" sx={{
            fontWeight: 500
          }}>
            {label}
          </Typography>
          {helperText && (
            <Typography variant="caption" sx={{
              color: "text.secondary"
            }}>
              {helperText}
            </Typography>
          )}
        </Box>
        <Button
          size="small"
          startIcon={<AddIcon />}
          onClick={addField}
          disabled={fields.length >= maxFields}
        >
          Add Field
        </Button>
      </Box>
      {/* Field list */}
      {fields.length === 0 ? (
        <Paper
          variant="outlined"
          sx={{
            p: 3,
            textAlign: 'center',
            bgcolor: 'action.hover',
          }}
        >
          <Typography variant="body2" sx={{
            color: "text.secondary"
          }}>
            No fields defined yet.
          </Typography>
          <Button
            size="small"
            startIcon={<AddIcon />}
            onClick={addField}
            sx={{ mt: 1 }}
          >
            Add First Field
          </Button>
        </Paper>
      ) : (
        <Box>
          {fields.map((field, index) => (
            <FieldRow
              key={field.id}
              field={field}
              onUpdate={(updated) => updateField(index, updated)}
              onDelete={() => deleteField(index)}
              showDefaultValue={showDefaultValue}
              showEnumValues={showEnumValues}
            />
          ))}
        </Box>
      )}
      {/* Field count */}
      {fields.length > 0 && (
        <Typography
          variant="caption"
          sx={{
            color: "text.secondary",
            mt: 1
          }}>
          {fields.length} field{fields.length !== 1 ? 's' : ''} defined
          {maxFields && ` (max ${maxFields})`}
        </Typography>
      )}
    </Box>
  );
};
