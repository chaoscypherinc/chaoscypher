// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * PropertyFieldRenderer: Renders a single template property field.
 *
 * Handles all supported property types: string, text, url, email,
 * integer, float, boolean, enum, date, datetime, and json.
 */

import React from 'react';
import {
  TextField,
  Switch,
  FormControlLabel,
  MenuItem,
  Select,
  InputLabel,
  FormControl,
} from '@mui/material';
import type { PropertyDefinition } from '../../../types';

interface PropertyFieldRendererProps {
  /** The property definition from the template. */
  propDef: PropertyDefinition;
  /** Current value for this property. */
  value: unknown;
  /** Callback when the property value changes. */
  onChange: (propName: string, value: unknown) => void;
}

/**
 * Get HTML input type for property types.
 */
function getInputType(propertyType: string): 'email' | 'url' | 'text' {
  switch (propertyType) {
    case 'email': return 'email';
    case 'url': return 'url';
    default: return 'text';
  }
}

/**
 * Renders a form field for a single template property definition.
 */
const PropertyFieldRenderer: React.FC<PropertyFieldRendererProps> = ({
  propDef,
  value,
  onChange,
}) => {
  switch (propDef.property_type) {
    case 'string':
    case 'url':
    case 'email':
      return (
        <TextField
          label={propDef.display_name}
          fullWidth
          value={value || ''}
          onChange={(e) => onChange(propDef.name, e.target.value)}
          required={propDef.required}
          helperText={propDef.description}
          type={getInputType(propDef.property_type)}
          sx={{ mb: 2 }}
        />
      );

    case 'text':
      return (
        <TextField
          label={propDef.display_name}
          fullWidth
          multiline
          rows={4}
          value={value || ''}
          onChange={(e) => onChange(propDef.name, e.target.value)}
          required={propDef.required}
          helperText={propDef.description}
          sx={{ mb: 2 }}
        />
      );

    case 'integer':
    case 'float':
      return (
        <TextField
          label={propDef.display_name}
          fullWidth
          type="number"
          value={value ?? ''}
          onChange={(e) => onChange(propDef.name, propDef.property_type === 'integer' ? parseInt(e.target.value) : parseFloat(e.target.value))}
          required={propDef.required}
          helperText={propDef.description}
          sx={{ mb: 2 }}
          slotProps={{
            htmlInput: { step: propDef.property_type === 'float' ? 'any' : '1' }
          }}
        />
      );

    case 'boolean':
      return (
        <FormControlLabel
          control={
            <Switch
              checked={Boolean(value)}
              onChange={(e) => onChange(propDef.name, e.target.checked)}
            />
          }
          label={propDef.display_name}
          sx={{ mb: 2 }}
        />
      );

    case 'enum':
      return (
        <FormControl fullWidth sx={{ mb: 2 }}>
          <InputLabel>{propDef.display_name}</InputLabel>
          <Select
            value={value || ''}
            onChange={(e) => onChange(propDef.name, e.target.value)}
            label={propDef.display_name}
            required={propDef.required}
          >
            {propDef.enum_values?.map(enumVal => (
              <MenuItem key={enumVal} value={enumVal}>{enumVal}</MenuItem>
            ))}
          </Select>
        </FormControl>
      );

    case 'date':
      return (
        <TextField
          label={propDef.display_name}
          fullWidth
          type="date"
          value={value || ''}
          onChange={(e) => onChange(propDef.name, e.target.value)}
          required={propDef.required}
          helperText={propDef.description}
          sx={{ mb: 2 }}
          slotProps={{
            inputLabel: { shrink: true }
          }}
        />
      );

    case 'datetime':
      return (
        <TextField
          label={propDef.display_name}
          fullWidth
          type="datetime-local"
          value={value || ''}
          onChange={(e) => onChange(propDef.name, e.target.value)}
          required={propDef.required}
          helperText={propDef.description}
          sx={{ mb: 2 }}
          slotProps={{
            inputLabel: { shrink: true }
          }}
        />
      );

    case 'json':
      return (
        <TextField
          label={propDef.display_name}
          fullWidth
          multiline
          rows={6}
          value={typeof value === 'object' ? JSON.stringify(value, null, 2) : value || '{}'}
          onChange={(e) => {
            try {
              const parsed = JSON.parse(e.target.value);
              onChange(propDef.name, parsed);
            } catch {
              // Invalid JSON, don't update
            }
          }}
          required={propDef.required}
          helperText={propDef.description || 'Enter valid JSON'}
          sx={{ mb: 2, fontFamily: 'monospace' }}
        />
      );

    default:
      return (
        <TextField
          label={propDef.display_name}
          fullWidth
          value={value || ''}
          onChange={(e) => onChange(propDef.name, e.target.value)}
          required={propDef.required}
          helperText={propDef.description}
          sx={{ mb: 2 }}
        />
      );
  }
};

export default PropertyFieldRenderer;
