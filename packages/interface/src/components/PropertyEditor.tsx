// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import React, { useState } from 'react';
import {
  Box,
  Button,
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  IconButton,
  Paper,
  Typography,
  Switch,
  FormControlLabel,
  Chip,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import ArrowUpIcon from '@mui/icons-material/ArrowUpward';
import ArrowDownIcon from '@mui/icons-material/ArrowDownward';

export interface PropertyDefinition {
  name: string;
  display_name: string;
  property_type: string;
  required: boolean;
  default_value?: unknown;
  enum_values?: string[];
  description?: string;
  validation_pattern?: string;
  allowed_node_types?: string[];
}

interface PropertyEditorProps {
  properties: PropertyDefinition[];
  onChange: (properties: PropertyDefinition[]) => void;
}

const PROPERTY_TYPES = [
  'string',
  'text',
  'integer',
  'float',
  'boolean',
  'date',
  'datetime',
  'url',
  'email',
  'enum',
  'json',
  'node_reference',
  'node_reference_list',
];

function PropertyEditor({ properties, onChange }: PropertyEditorProps) {
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null);

  const addProperty = () => {
    onChange([
      ...properties,
      {
        name: `property_${properties.length + 1}`,
        display_name: `Property ${properties.length + 1}`,
        property_type: 'string',
        required: false,
      },
    ]);
    setExpandedIndex(properties.length);
  };

  const removeProperty = (index: number) => {
    onChange(properties.filter((_, i) => i !== index));
    setExpandedIndex(null);
  };

  const updateProperty = (index: number, updates: Partial<PropertyDefinition>) => {
    const updated = [...properties];
    updated[index] = { ...updated[index], ...updates };
    onChange(updated);
  };

  const moveProperty = (index: number, direction: 'up' | 'down') => {
    const newIndex = direction === 'up' ? index - 1 : index + 1;
    if (newIndex < 0 || newIndex >= properties.length) return;

    const updated = [...properties];
    [updated[index], updated[newIndex]] = [updated[newIndex], updated[index]];
    onChange(updated);
    setExpandedIndex(newIndex);
  };

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          mb: 2
        }}>
        <Typography variant="subtitle1" sx={{
          fontWeight: "bold"
        }}>
          Properties
        </Typography>
        <Button startIcon={<AddIcon />} onClick={addProperty} size="small">
          Add Property
        </Button>
      </Box>
      {properties.length === 0 ? (
        <Typography
          variant="body2"
          align="center"
          sx={{
            color: "text.secondary",
            py: 2
          }}>
          No properties defined. Click "Add Property" to add one.
        </Typography>
      ) : (
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 1
          }}>
          {properties.map((prop, index) => (
            <Paper
              key={index}
              variant="outlined"
              sx={{
                p: 2,
                border: expandedIndex === index ? 2 : 1,
                borderColor: expandedIndex === index ? 'primary.main' : 'divider',
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  mb: expandedIndex === index ? 2 : 0
                }}>
                <Box
                  onClick={() => setExpandedIndex(expandedIndex === index ? null : index)}
                  sx={{
                    flexGrow: 1,
                    cursor: 'pointer'
                  }}>
                  <Typography variant="body2" sx={{
                    fontWeight: "bold"
                  }}>
                    {prop.display_name}
                  </Typography>
                  <Typography variant="caption" sx={{
                    color: "text.secondary"
                  }}>
                    {prop.name} ({prop.property_type})
                    {prop.required && <Chip label="Required" size="small" sx={{ ml: 1, height: 16 }} />}
                  </Typography>
                </Box>

                <IconButton
                  aria-label="Move up"
                  size="small"
                  onClick={() => moveProperty(index, 'up')}
                  disabled={index === 0}
                >
                  <ArrowUpIcon fontSize="small" />
                </IconButton>
                <IconButton
                  aria-label="Move down"
                  size="small"
                  onClick={() => moveProperty(index, 'down')}
                  disabled={index === properties.length - 1}
                >
                  <ArrowDownIcon fontSize="small" />
                </IconButton>
                <IconButton aria-label="Delete property" size="small" color="error" onClick={() => removeProperty(index)}>
                  <DeleteIcon fontSize="small" />
                </IconButton>
              </Box>

              {expandedIndex === index && (
                <Box
                  sx={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 2
                  }}>
                  <TextField
                    label="Property Name (field key)"
                    value={prop.name}
                    onChange={(e) => updateProperty(index, { name: e.target.value })}
                    size="small"
                    fullWidth
                    helperText="Used as the JSON key (no spaces, lowercase recommended)"
                  />

                  <TextField
                    label="Display Name"
                    value={prop.display_name}
                    onChange={(e) => updateProperty(index, { display_name: e.target.value })}
                    size="small"
                    fullWidth
                  />

                  <FormControl size="small" fullWidth>
                    <InputLabel>Property Type</InputLabel>
                    <Select
                      value={prop.property_type}
                      label="Property Type"
                      onChange={(e) => updateProperty(index, { property_type: e.target.value })}
                    >
                      {PROPERTY_TYPES.map((type) => (
                        <MenuItem key={type} value={type}>
                          {type}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>

                  <FormControlLabel
                    control={
                      <Switch
                        checked={prop.required}
                        onChange={(e) => updateProperty(index, { required: e.target.checked })}
                      />
                    }
                    label="Required"
                  />

                  {prop.property_type === 'enum' && (
                    <TextField
                      label="Enum Values (comma-separated)"
                      value={prop.enum_values?.join(', ') || ''}
                      onChange={(e) => {
                        // Store the raw value without parsing during typing
                        updateProperty(index, {
                          enum_values: e.target.value.split(',').map((v) => v.trim()),
                        });
                      }}
                      onBlur={(e) => {
                        // Clean up empty values on blur
                        updateProperty(index, {
                          enum_values: e.target.value.split(',').map((v) => v.trim()).filter(Boolean),
                        });
                      }}
                      size="small"
                      fullWidth
                      helperText="e.g., option1, option2, option3"
                      multiline
                      rows={2}
                    />
                  )}

                  {(prop.property_type === 'node_reference' || prop.property_type === 'node_reference_list') && (
                    <TextField
                      label="Allowed Template IDs (comma-separated, optional)"
                      value={prop.allowed_node_types?.join(', ') || ''}
                      onChange={(e) => {
                        // Store the raw value without parsing during typing
                        updateProperty(index, {
                          allowed_node_types: e.target.value.split(',').map((v) => v.trim()),
                        });
                      }}
                      onBlur={(e) => {
                        // Clean up empty values on blur
                        updateProperty(index, {
                          allowed_node_types: e.target.value.split(',').map((v) => v.trim()).filter(Boolean),
                        });
                      }}
                      size="small"
                      fullWidth
                      helperText="Leave empty to allow any template. e.g., template_person, template_project"
                      multiline
                      rows={2}
                    />
                  )}

                  <TextField
                    label="Default Value"
                    value={prop.default_value || ''}
                    onChange={(e) => updateProperty(index, { default_value: e.target.value })}
                    size="small"
                    fullWidth
                  />

                  <TextField
                    label="Description"
                    value={prop.description || ''}
                    onChange={(e) => updateProperty(index, { description: e.target.value })}
                    size="small"
                    fullWidth
                    multiline
                    rows={2}
                  />

                  <TextField
                    label="Validation Pattern (regex)"
                    value={prop.validation_pattern || ''}
                    onChange={(e) => updateProperty(index, { validation_pattern: e.target.value })}
                    size="small"
                    fullWidth
                    helperText="Optional regex pattern for validation"
                  />
                </Box>
              )}
            </Paper>
          ))}
        </Box>
      )}
    </Box>
  );
}

export default React.memo(PropertyEditor);
